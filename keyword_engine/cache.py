"""Disk cache for fetched responses: cache/<source>/<slug>-<hash>.json."""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

MISSING = object()  # "nothing cached", distinct from a cached None


def slug(text, max_len=60):
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return s[:max_len] or "empty"


class Cache:
    def __init__(self, root, max_age_days=30, refresh=False):
        self.root = Path(root)
        self.max_age = timedelta(days=max_age_days)
        self.refresh = refresh

    def path_for(self, source, key):
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]
        return self.root / slug(source) / f"{slug(key)}-{digest}.json"

    def read(self, source, key):
        """Returns (data, is_fresh), or (MISSING, False) when nothing is cached."""
        path = self.path_for(source, key)
        if not path.exists():
            return MISSING, False
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            fetched = datetime.fromisoformat(entry["fetched_at"])
        except (ValueError, KeyError, OSError):
            return MISSING, False
        fresh = datetime.now(timezone.utc) - fetched < self.max_age
        return entry["data"], fresh

    def write(self, source, key, data):
        path = self.path_for(source, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"fetched_at": datetime.now(timezone.utc).isoformat(), "key": key, "data": data}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def get_or_fetch(self, source, key, fetch_fn):
        """Returns (data, how) where how is "cache", "live" or "stale".

        A failed live fetch falls back to stale cached data when there is any;
        otherwise the error is raised for the caller to log.
        """
        cached, fresh = self.read(source, key)
        if cached is not MISSING and fresh and not self.refresh:
            return cached, "cache"
        try:
            data = fetch_fn()
        except Exception:
            if cached is not MISSING:
                return cached, "stale"
            raise
        self.write(source, key, data)
        return data, "live"
