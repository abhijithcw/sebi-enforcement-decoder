"""Config loading. config.yaml + .env; SED_DATA_ROOT env overrides data_root (useful on the Pi
until the real HDD mount path is confirmed)."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


def _find_root() -> Path:
    """Repo root holds config.yaml, sql/, prompts/, web/. Editable install (`pip install -e .`) is the supported
    way; SED_HOME or running from the repo directory also work. Fails loudly instead of reading from site-packages."""
    if os.environ.get("SED_HOME"):
        return Path(os.environ["SED_HOME"]).resolve()
    for cand in (Path(__file__).resolve().parents[2], Path.cwd()):
        if (cand / "config.yaml").exists() and (cand / "sql" / "schema.sql").exists():
            return cand
    raise RuntimeError("sebi-enforcement-decoder repo files not found. Use `pip install -e .` from the repo, "
                       "run from the repo directory, or set SED_HOME=/path/to/sebi-enforcement-decoder")


REPO_ROOT = _find_root()


class Config:
    def __init__(self, raw: dict[str, Any]):
        self.raw = raw
        self.data_root = Path(os.environ.get("SED_DATA_ROOT") or raw["data_root"])
        self.timezone = raw.get("timezone", "Asia/Kolkata")
        self.http = raw["http"]
        self.sqlite = raw["sqlite"]
        self.scrape = raw["scrape"]
        self.pdf = raw["pdf"]
        self.llm = raw["llm"]
        self.legal = raw["legal"]

    def path(self, key: str) -> Path:
        p = self.data_root / self.raw["paths"][key]
        p.parent.mkdir(parents=True, exist_ok=True) if key == "db" else p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def db_path(self) -> Path:
        return self.path("db")

    @property
    def anthropic_api_key(self) -> str | None:
        return os.environ.get("ANTHROPIC_API_KEY")


@lru_cache(maxsize=1)
def load(config_file: str | os.PathLike | None = None) -> Config:
    load_dotenv(REPO_ROOT / ".env")
    cfg_path = Path(config_file) if config_file else REPO_ROOT / "config.yaml"
    with open(cfg_path, encoding="utf-8") as f:
        return Config(yaml.safe_load(f))
