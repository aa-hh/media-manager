from datetime import datetime, timedelta, timezone

from lib.processors import deletion


def _iso(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


# ── _days_since ───────────────────────────────────────────────────────────────

def test_days_since_none_and_empty():
    assert deletion._days_since(None) is None
    assert deletion._days_since("") is None


def test_days_since_iso_string():
    assert deletion._days_since(_iso(15)) == 15


def test_days_since_z_suffix_and_numeric_timestamp():
    dt = datetime.now(timezone.utc) - timedelta(days=3)
    assert deletion._days_since(dt.strftime("%Y-%m-%dT%H:%M:%S") + "Z") == 3
    assert deletion._days_since(dt.timestamp()) == 3


def test_days_since_invalid_returns_none():
    assert deletion._days_since("garbage-not-a-date") is None


# ── score: size points ────────────────────────────────────────────────────────

def _watched_item(size_gb=0, **overrides):
    """An item that's been watched and recently added — isolates size scoring."""
    item = {
        "size_gb": size_gb,
        "rating": 8.0,
        "total_plays": 5,
        "any_watched": True,
        "added_at": _iso(5),
        "request": {"requested": False},
        "requester_status": {},
        "watch_data": {"alice": {"last_watched": _iso(1)}},
    }
    item.update(overrides)
    return item


def test_score_size_buckets():
    assert deletion.score(_watched_item(size_gb=600))["score"] >= 25
    assert "Very large" in deletion.score(_watched_item(size_gb=600))["reasons"][0]
    assert deletion.score(_watched_item(size_gb=300))["reasons"][0].startswith("Large (300")
    assert deletion.score(_watched_item(size_gb=100))["reasons"][0].startswith("Large (100")
    assert deletion.score(_watched_item(size_gb=25))["score"] == 8
    assert deletion.score(_watched_item(size_gb=10))["score"] == 4
    assert deletion.score(_watched_item(size_gb=1))["score"] == 0


# ── score: watch / requester points (outside grace period) ───────────────────

def _stale_item(**overrides):
    item = {
        "size_gb": 0,
        "rating": 8.0,
        "total_plays": 0,
        "any_watched": False,
        "added_at": _iso(200),
        "request": {"requested": False},
        "requester_status": {},
        "watch_data": {},
    }
    item.update(overrides)
    return item


def test_score_never_watched_adds_points_and_reason():
    result = deletion.score(_stale_item())
    assert result["score"] >= 30
    assert "Never watched by anyone" in result["reasons"]


def test_score_watched_but_not_25pct_by_anyone():
    item = _stale_item(total_plays=1, any_watched=False)
    result = deletion.score(item)
    assert "No user has watched 25%+" in result["reasons"]


def test_score_requester_never_watched():
    item = _stale_item(total_plays=3, any_watched=True,
                       request={"requested": True, "requester_name": "bob"},
                       requester_status={"watched": False, "completion_pct": 0})
    result = deletion.score(item)
    assert "Requester (bob) never watched" in result["reasons"]


def test_score_requester_watched_below_25_percent():
    item = _stale_item(total_plays=3, any_watched=True,
                       request={"requested": True, "requester_name": "bob"},
                       requester_status={"watched": True, "completion_pct": 10})
    result = deletion.score(item)
    assert "Requester watched only 10%" in result["reasons"]


def test_score_requester_watched_enough_no_extra_points():
    item = _stale_item(total_plays=3, any_watched=True,
                       request={"requested": True, "requester_name": "bob"},
                       requester_status={"watched": True, "completion_pct": 90})
    result = deletion.score(item)
    assert not any("Requester" in r for r in result["reasons"])


# ── score: grace period ───────────────────────────────────────────────────────

def test_score_grace_period_suppresses_watch_points():
    item = _stale_item(added_at=_iso(5), total_plays=0)
    result = deletion.score(item)
    assert any("grace period" in r for r in result["reasons"])
    assert "Never watched by anyone" not in result["reasons"]


def test_score_grace_period_uses_most_recent_anchor():
    # added long ago, but requested recently -> still in grace period
    item = _stale_item(added_at=_iso(100), total_plays=0,
                       request={"requested": True, "requester_name": "bob", "requested_at": _iso(2)},
                       requester_status={"watched": False})
    result = deletion.score(item)
    assert any("grace period" in r for r in result["reasons"])


def test_score_no_grace_period_when_dates_missing():
    item = _stale_item(added_at=None, total_plays=0)
    result = deletion.score(item)
    assert "Never watched by anyone" in result["reasons"]


# ── score: inactivity ─────────────────────────────────────────────────────────

def test_score_inactivity_points_accumulate_after_90_days():
    item = _stale_item(
        added_at=_iso(400), total_plays=2, any_watched=True,
        watch_data={"alice": {"last_watched": _iso(120)}},
    )
    result = deletion.score(item)
    assert any("inactivity pts" in r for r in result["reasons"])
    # 120 - 90 = 30 days over -> 4 weeks * 0.5 = 2.0 pts
    assert any("+2.00 inactivity" in r for r in result["reasons"])


def test_score_inactivity_uses_added_at_when_never_watched():
    item = _stale_item(added_at=_iso(200), total_plays=0)
    result = deletion.score(item)
    assert any("Unwatched for 200 days" in r for r in result["reasons"])


def test_score_inactivity_slower_when_on_watchlist():
    base = _stale_item(
        added_at=_iso(400), total_plays=2, any_watched=True,
        watch_data={"alice": {"last_watched": _iso(120)}},
    )
    not_watchlisted = deletion.score(dict(base, on_watchlist=False))
    watchlisted = deletion.score(dict(base, on_watchlist=True))
    assert watchlisted["score"] < not_watchlisted["score"]


def test_score_no_inactivity_points_within_90_days():
    item = _stale_item(added_at=_iso(400), total_plays=2, any_watched=True,
                       watch_data={"alice": {"last_watched": _iso(30)}})
    result = deletion.score(item)
    assert not any("inactivity" in r for r in result["reasons"])


# ── score: rating ─────────────────────────────────────────────────────────────

def test_score_low_rating_adds_points_and_reason():
    item = _watched_item(rating=3.5)
    result = deletion.score(item)
    assert "Low TMDB rating (3.5)" in result["reasons"]


def test_score_medium_rating_adds_points_no_reason():
    item = _watched_item(rating=6.0)
    result = deletion.score(item)
    assert not any("Low TMDB rating" in r for r in result["reasons"])


def test_score_high_rating_no_extra_points():
    low = deletion.score(_watched_item(rating=3.0, size_gb=0))
    mid = deletion.score(_watched_item(rating=6.0, size_gb=0))
    high = deletion.score(_watched_item(rating=9.0, size_gb=0))
    assert low["score"] > mid["score"] > high["score"]


# ── score: thresholds & clamping ──────────────────────────────────────────────

def test_score_recommendation_thresholds():
    keep = deletion.score(_watched_item(size_gb=0))
    assert keep["recommendation"] == "keep"

    suggest = deletion.score(_stale_item(size_gb=60, total_plays=0, rating=4.0))
    assert suggest["score"] >= 45
    assert suggest["recommendation"] in ("suggest_delete", "strong_delete")

    strong = deletion.score(_stale_item(size_gb=600, total_plays=0, rating=2.0,
                                         added_at=_iso(2000)))
    assert strong["score"] >= 70
    assert strong["recommendation"] == "strong_delete"


def test_score_clamped_to_100():
    item = _stale_item(size_gb=1000, total_plays=0, rating=1.0, added_at=_iso(5000),
                       on_watchlist=False)
    result = deletion.score(item)
    assert result["score"] == 100


# ── score_season ──────────────────────────────────────────────────────────────

def _show(**overrides):
    show = {"added_at": _iso(5), "request": {}, "rating": 8.0}
    show.update(overrides)
    return show


def test_score_season_size_buckets():
    big = deletion.score_season({"size_gb": 150, "total_plays": 5, "any_watched": True,
                                 "watch_data": {"a": {"last_watched": _iso(1)}}}, _show())
    assert "Large season" in big["reasons"][0]

    small = deletion.score_season({"size_gb": 5, "total_plays": 5, "any_watched": True,
                                   "watch_data": {"a": {"last_watched": _iso(1)}}}, _show())
    assert small["score"] == 0


def test_score_season_never_watched():
    season = {"size_gb": 0, "total_plays": 0, "any_watched": False, "watch_data": {}}
    result = deletion.score_season(season, _show(added_at=_iso(200)))
    assert "Never watched" in result["reasons"]


def test_score_season_partial_watch():
    season = {"size_gb": 0, "total_plays": 2, "any_watched": False, "watch_data": {}}
    result = deletion.score_season(season, _show(added_at=_iso(200)))
    assert "No user has watched 25%+ of season" in result["reasons"]


def test_score_season_grace_period_from_show():
    season = {"size_gb": 0, "total_plays": 0, "any_watched": False, "watch_data": {}}
    result = deletion.score_season(season, _show(added_at=_iso(2)))
    assert any("grace period" in r for r in result["reasons"])


def test_score_season_inactivity_points():
    season = {
        "size_gb": 0, "total_plays": 3, "any_watched": True,
        "watch_data": {"alice": {"last_watched": _iso(120)}},
    }
    result = deletion.score_season(season, _show(added_at=_iso(400)))
    assert any("+2.0 inactivity" in r for r in result["reasons"])


def test_score_season_low_rating_points_no_explicit_reason():
    season = {"size_gb": 0, "total_plays": 5, "any_watched": True,
              "watch_data": {"alice": {"last_watched": _iso(1)}}}
    low = deletion.score_season(season, _show(rating=3.0, added_at=_iso(5)))
    high = deletion.score_season(season, _show(rating=9.0, added_at=_iso(5)))
    assert low["score"] > high["score"]


def test_score_season_recommendation_thresholds():
    keep = deletion.score_season(
        {"size_gb": 1, "total_plays": 5, "any_watched": True,
         "watch_data": {"a": {"last_watched": _iso(1)}}},
        _show(added_at=_iso(5)),
    )
    assert keep["recommendation"] == "keep"

    strong = deletion.score_season(
        {"size_gb": 200, "total_plays": 0, "any_watched": False, "watch_data": {}},
        _show(rating=2.0, added_at=_iso(2000)),
    )
    assert strong["recommendation"] == "strong_delete"


# ── apply ─────────────────────────────────────────────────────────────────────

def test_apply_attaches_deletion_to_items_and_seasons():
    items = [
        {
            "type": "show", "size_gb": 5, "rating": 8.0, "total_plays": 1,
            "any_watched": True, "added_at": _iso(5), "request": {},
            "requester_status": {}, "watch_data": {"a": {"last_watched": _iso(1)}},
            "seasons": [
                {"size_bytes": 10 * 1024 ** 3, "size_gb": 10, "total_plays": 1,
                 "any_watched": True, "watch_data": {"a": {"last_watched": _iso(1)}}},
                {"size_bytes": 0, "size_gb": 0, "total_plays": 0,
                 "any_watched": False, "watch_data": {}},
            ],
        },
        {
            "type": "movie", "size_gb": 2, "rating": 7.0, "total_plays": 1,
            "any_watched": True, "added_at": _iso(5), "request": {},
            "requester_status": {}, "watch_data": {"a": {"last_watched": _iso(1)}},
        },
    ]
    result = deletion.apply(items)
    assert "deletion" in result[0]
    assert "deletion" in result[0]["seasons"][0]
    # zero-byte season is skipped
    assert "deletion" not in result[0]["seasons"][1]
    assert "deletion" in result[1]
