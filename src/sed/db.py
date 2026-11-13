"""SQLite access. One writer process; WAL; busy_timeout; batched idempotent upserts."""
from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence
from contextlib import contextmanager
from pathlib import Path

from .config import REPO_ROOT, Config

SCHEMA_FILE = REPO_ROOT / "sql" / "schema.sql"


def connect(cfg: Config, path: Path | None = None) -> sqlite3.Connection:
    con = sqlite3.connect(str(path or cfg.db_path), timeout=cfg.sqlite["busy_timeout_ms"] / 1000)
    con.row_factory = sqlite3.Row
    con.execute(f"PRAGMA journal_mode={cfg.sqlite['journal_mode']}")
    con.execute(f"PRAGMA busy_timeout={cfg.sqlite['busy_timeout_ms']}")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def init_db(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))
    con.commit()


@contextmanager
def tx(con: sqlite3.Connection):
    if con.in_transaction:          # flush any implicit transaction a caller left open
        con.commit()
    try:
        con.execute("BEGIN")
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise


def upsert_many(
    con: sqlite3.Connection,
    table: str,
    rows: Iterable[dict],
    key_cols: Sequence[str],
    batch_size: int = 500,
) -> int:
    """Idempotent INSERT ... ON CONFLICT(key) DO UPDATE in batches. Never loads all rows at once."""
    n = 0
    batch: list[dict] = []

    def flush():
        nonlocal n
        if not batch:
            return
        cols = list(batch[0].keys())
        placeholders = ",".join("?" for _ in cols)
        updates = ",".join(f"{c}=excluded.{c}" for c in cols if c not in key_cols)
        sql = (
            f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT({','.join(key_cols)}) DO UPDATE SET {updates}"
            if updates
            else f"INSERT OR IGNORE INTO {table} ({','.join(cols)}) VALUES ({placeholders})"
        )
        with tx(con):
            con.executemany(sql, [tuple(r[c] for c in cols) for r in batch])
        n += len(batch)
        batch.clear()

    for r in rows:
        batch.append(r)
        if len(batch) >= batch_size:
            flush()
    flush()
    return n
