"""Polite HTTP client: per-host throttle, browser UA, exponential backoff, raw responses cached
to disk (http_cache table + files under cache/http/). Streams large bodies so RSS stays low."""
from __future__ import annotations

import hashlib
import logging
import random
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .config import Config

log = logging.getLogger(__name__)
RETRY_STATUSES = {403, 408, 425, 429, 500, 502, 503, 504}


class FetchError(Exception):
    pass


@dataclass
class Response:
    status: int
    path: Path            # raw body on disk
    from_cache: bool

    def text(self, encoding: str = "utf-8") -> str:
        return self.path.read_text(encoding=encoding, errors="replace")

    def bytes(self) -> bytes:
        return self.path.read_bytes()


class Http:
    def __init__(self, cfg: Config, con):
        self.cfg = cfg
        self.con = con
        self.cache_dir = cfg.path("http_cache")
        h = cfg.http
        self.min_interval = float(h["min_interval_seconds"])
        self.max_tries = int(h["max_tries"])
        self.backoff_base = float(h["backoff_base_seconds"])
        self._last: dict[str, float] = {}
        self.client = httpx.Client(
            headers={
                "User-Agent": h["user_agent"],
                "Accept": "text/html,application/xhtml+xml,application/pdf,*/*;q=0.8",
                "Accept-Language": "en-IN,en;q=0.9",
            },
            timeout=float(h["timeout_seconds"]),
            follow_redirects=True,
        )

    # ------------------------------------------------------------------ cache
    @staticmethod
    def cache_key(method: str, url: str, body: str | None) -> str:
        return hashlib.sha256(f"{method}\n{url}\n{body or ''}".encode()).hexdigest()

    def _cache_path(self, key: str, ext: str) -> Path:
        d = self.cache_dir / key[:2]
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{key}{ext}"

    def _cached(self, key: str) -> Response | None:
        row = self.con.execute("SELECT status, path FROM http_cache WHERE url_sha=?", (key,)).fetchone()
        if not row:
            return None
        p = self.cfg.data_root / row["path"]
        if not p.exists():
            return None
        return Response(status=row["status"], path=p, from_cache=True)

    def _remember(self, key: str, url: str, method: str, status: int, path: Path) -> None:
        rel = str(path.relative_to(self.cfg.data_root))
        self.con.execute(
            "INSERT INTO http_cache(url_sha,url,method,status,path,fetched_at) VALUES(?,?,?,?,?,datetime('now')) "
            "ON CONFLICT(url_sha) DO UPDATE SET status=excluded.status,path=excluded.path,fetched_at=excluded.fetched_at",
            (key, url, method, status, rel),
        )
        self.con.commit()

    # --------------------------------------------------------------- throttle
    def _throttle(self, url: str) -> None:
        host = urlsplit(url).netloc
        wait = self._last.get(host, 0) + self.min_interval + random.uniform(0, 0.5) - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last[host] = time.monotonic()

    # ------------------------------------------------------------------ fetch
    def request(self, method: str, url: str, *, data: dict | None = None, use_cache: bool = True,
                ext: str = ".bin", headers: dict | None = None) -> Response:
        body = "&".join(f"{k}={v}" for k, v in sorted((data or {}).items())) if data else None
        key = self.cache_key(method, url, body)
        if use_cache and (hit := self._cached(key)):
            return hit
        path = self._cache_path(key, ext)
        last_err: Exception | None = None
        for attempt in range(self.max_tries):
            self._throttle(url)
            try:
                with self.client.stream(method, url, data=data, headers=headers) as r:
                    if r.status_code in RETRY_STATUSES:
                        raise FetchError(f"HTTP {r.status_code}")
                    with open(path, "wb") as f:
                        for chunk in r.iter_bytes(1 << 16):
                            f.write(chunk)
                    status = r.status_code
                self._remember(key, url, method, status, path)
                return Response(status=status, path=path, from_cache=False)
            except (httpx.HTTPError, FetchError) as e:
                last_err = e
                delay = self.backoff_base * (2 ** attempt) + random.uniform(0, 1)
                log.warning("%s %s failed (%s), try %d/%d, sleeping %.0fs", method, url, e, attempt + 1,
                            self.max_tries, delay)
                time.sleep(delay)
        raise FetchError(f"gave up on {method} {url}: {last_err}")

    def get(self, url: str, **kw) -> Response:
        return self.request("GET", url, **kw)

    def post(self, url: str, data: dict, **kw) -> Response:
        kw.setdefault("headers", {"X-Requested-With": "XMLHttpRequest",
                                  "Content-Type": "application/x-www-form-urlencoded"})
        return self.request("POST", url, data=data, **kw)

    def download(self, url: str, dest: Path) -> tuple[str, int]:
        """Stream a file to dest; returns (sha256, size). Not cached twice — dest is the copy."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        last_err: Exception | None = None
        for attempt in range(self.max_tries):
            self._throttle(url)
            try:
                h = hashlib.sha256()
                size = 0
                with self.client.stream("GET", url) as r:
                    if r.status_code in RETRY_STATUSES:
                        raise FetchError(f"HTTP {r.status_code}")
                    if r.status_code >= 400:
                        raise FetchError(f"HTTP {r.status_code} (not retryable)")
                    with open(tmp, "wb") as f:
                        for chunk in r.iter_bytes(1 << 16):
                            f.write(chunk)
                            h.update(chunk)
                            size += len(chunk)
                tmp.replace(dest)
                return h.hexdigest(), size
            except FetchError as e:
                if "not retryable" in str(e):
                    raise
                last_err = e
            except httpx.HTTPError as e:
                last_err = e
            delay = self.backoff_base * (2 ** attempt) + random.uniform(0, 1)
            log.warning("GET %s failed (%s), try %d/%d, sleeping %.0fs", url, last_err, attempt + 1,
                        self.max_tries, delay)
            time.sleep(delay)
        tmp.unlink(missing_ok=True)
        raise FetchError(f"gave up on {url}: {last_err}")
