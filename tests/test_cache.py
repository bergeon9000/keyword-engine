import json
from datetime import datetime, timedelta, timezone

import pytest

from keyword_engine.cache import Cache, slug


def test_slug():
    assert slug("Reef Tank | en-us") == "reef_tank_en_us"


def test_fetch_then_cache(tmp_path):
    c = Cache(tmp_path)
    assert c.get_or_fetch("google", "q", lambda: [1, 2]) == ([1, 2], "live")
    assert c.get_or_fetch("google", "q", lambda: pytest.fail("should not fetch")) == ([1, 2], "cache")


def test_cached_none_is_still_a_hit(tmp_path):
    c = Cache(tmp_path)
    c.get_or_fetch("wikipedia", "nothing", lambda: None)
    assert c.get_or_fetch("wikipedia", "nothing", lambda: pytest.fail("refetched")) == (None, "cache")


def test_refresh_refetches(tmp_path):
    Cache(tmp_path).get_or_fetch("google", "q", lambda: "old")
    assert Cache(tmp_path, refresh=True).get_or_fetch("google", "q", lambda: "new") == ("new", "live")


def test_failure_falls_back_to_stale(tmp_path):
    c = Cache(tmp_path)
    c.get_or_fetch("google", "q", lambda: "old")
    path = c.path_for("google", "q")
    entry = json.loads(path.read_text(encoding="utf-8"))
    entry["fetched_at"] = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    path.write_text(json.dumps(entry), encoding="utf-8")

    def boom():
        raise OSError("offline")

    assert c.get_or_fetch("google", "q", boom) == ("old", "stale")


def test_failure_without_cache_raises(tmp_path):
    def boom():
        raise OSError("offline")

    with pytest.raises(OSError):
        Cache(tmp_path).get_or_fetch("google", "q", boom)
