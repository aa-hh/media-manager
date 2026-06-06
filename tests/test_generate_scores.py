from pathlib import Path

import generate
from generate import (
    _days_since,
    content_value_score,
    content_waste_score,
    _color_for_rec,
    _chart_data_top_shows_by_size,
    _chart_data_top_movies_by_size,
    _chart_data_storage_by_deletion,
    _chart_data_storage_by_requester,
    _chart_data_growth,
    _build_sparkline_points,
    _make_env,
)
from datetime import datetime, timedelta, timezone


# ── _days_since ───────────────────────────────────────────────────────────────

def test_days_since_none_returns_none():
    assert _days_since(None) is None


def test_days_since_invalid_string_returns_none():
    assert _days_since("not-a-date") is None


def test_days_since_valid_date():
    ten_days_ago = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    assert _days_since(ten_days_ago) == 10


def test_days_since_handles_z_suffix():
    dt = (datetime.now(timezone.utc) - timedelta(days=5))
    iso_with_z = dt.strftime("%Y-%m-%dT%H:%M:%S") + "Z"
    assert _days_since(iso_with_z) == 5


# ── content_value_score ───────────────────────────────────────────────────────

def test_content_value_score_no_watchers_is_zero():
    assert content_value_score({"watch_data": {}, "total_plays": 0}) == 0.0


def test_content_value_score_zero_plays_is_zero():
    item = {"watch_data": {"alice": {}}, "total_plays": 0}
    assert content_value_score(item) == 0.0


def test_content_value_score_show_uses_episode_count_for_rewatch_ratio():
    item = {
        "type": "show",
        "watch_data": {"alice": {}, "bob": {}},
        "total_plays": 10,
        "total_episodes": 5,
    }
    score = content_value_score(item)
    assert score > 0
    # breadth = ln(3), rewatch_ratio = 10/5 = 2, score = ln(3)*ln(3)
    import math
    expected = round(math.log(3) * math.log(3), 4)
    assert score == expected


def test_content_value_score_movie_uses_user_count_for_rewatch_ratio():
    item = {
        "type": "movie",
        "watch_data": {"alice": {}},
        "total_plays": 4,
    }
    import math
    expected = round(math.log(2) * math.log(5), 4)
    assert content_value_score(item) == expected


# ── content_waste_score ───────────────────────────────────────────────────────

def test_content_waste_score_recent_item_has_low_age_factor():
    item = {
        "added_at": datetime.now(timezone.utc).isoformat(),
        "tmdb_id": 1,
        "type": "movie",
        "size_gb": 100,
        "watch_data": {},
        "total_plays": 0,
    }
    score = content_waste_score(item, set())
    assert score == 0.0  # age_factor ~ 0


def test_content_waste_score_old_unwatched_item_scores_higher_than_watchlisted():
    old_date = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
    base_item = {
        "added_at": old_date,
        "tmdb_id": 42,
        "type": "movie",
        "size_gb": 50,
        "watch_data": {},
        "total_plays": 0,
    }
    not_on_watchlist = content_waste_score(base_item, set())
    on_watchlist = content_waste_score(base_item, {("movie", 42)})
    assert not_on_watchlist > on_watchlist
    assert on_watchlist == round(not_on_watchlist * 0.2, 4)


def test_content_waste_score_falls_back_to_request_date():
    old_date = (datetime.now(timezone.utc) - timedelta(days=180)).isoformat()
    item = {
        "request": {"requested_at": old_date},
        "tmdb_id": None,
        "type": "movie",
        "size_gb": 10,
        "watch_data": {},
        "total_plays": 0,
    }
    score = content_waste_score(item, set())
    assert score > 0


def test_content_waste_score_show_type_checked_against_tv_watchlist():
    old_date = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
    item = {
        "added_at": old_date, "tmdb_id": 7, "type": "show",
        "size_gb": 20, "watch_data": {}, "total_plays": 0,
    }
    on_watchlist = content_waste_score(item, {("tv", 7)})
    not_on_watchlist = content_waste_score(item, {("movie", 7)})
    assert on_watchlist < not_on_watchlist


# ── _color_for_rec ────────────────────────────────────────────────────────────

def test_color_for_rec_known_values():
    assert _color_for_rec("strong_delete") == "#e05252"
    assert _color_for_rec("suggest_delete") == "#e0b252"
    assert _color_for_rec("keep") == "#52a0e0"
    assert _color_for_rec("unknown") == "#52a0e0"


# ── chart data helpers ────────────────────────────────────────────────────────

def test_chart_data_top_shows_by_size_sorted_and_truncated():
    shows = [
        {"title": "A" * 40, "size_gb": 5, "deletion": {"recommendation": "keep"}},
        {"title": "B", "size_gb": 50, "deletion": {"recommendation": "strong_delete"}},
        {"title": "C", "size_gb": 20, "deletion": {}},
    ]
    result = _chart_data_top_shows_by_size(shows, n=2)
    assert result["labels"] == ["B", "C"]
    assert result["data"] == [50, 20]
    assert result["colors"] == ["#e05252", "#52a0e0"]


def test_chart_data_top_movies_by_size_truncates_titles():
    movies = [{"title": "X" * 40, "size_gb": 1, "deletion": {}}]
    result = _chart_data_top_movies_by_size(movies)
    assert len(result["labels"][0]) == 30


def test_chart_data_storage_by_deletion_buckets_and_unknown_falls_to_keep():
    items = [
        {"size_gb": 10, "deletion": {"recommendation": "strong_delete"}},
        {"size_gb": 5, "deletion": {"recommendation": "suggest_delete"}},
        {"size_gb": 3, "deletion": {"recommendation": "keep"}},
        {"size_gb": 2, "deletion": {"recommendation": "something_weird"}},
        {"size_gb": 1},
    ]
    result = _chart_data_storage_by_deletion(items)
    assert result["data"] == [10, 5, 6]  # keep bucket = 3 + 2 + 1


def test_chart_data_storage_by_requester_groups_and_sorts():
    items = [
        {"size_gb": 10, "request": {"requested": True, "requester_name": "alice"}},
        {"size_gb": 5, "request": {"requested": True, "requester_name": "alice"}},
        {"size_gb": 20, "request": {"requested": False}},
        {"size_gb": 1, "request": {"requested": True, "requester_name": "bob"}},
    ]
    result = _chart_data_storage_by_requester(items)
    assert result["labels"][0] == "Library"
    assert result["data"][0] == 20
    assert "alice" in result["labels"]
    assert result["data"][result["labels"].index("alice")] == 15


def test_chart_data_growth_extracts_series():
    snapshots = [
        {"date": "2024-01-01", "tv_gb": 1, "movie_gb": 2, "total_gb": 3},
        {"date": "2024-01-02", "tv_gb": 4, "movie_gb": 5, "total_gb": 9},
    ]
    result = _chart_data_growth(snapshots)
    assert result["labels"] == ["2024-01-01", "2024-01-02"]
    assert result["tv"] == [1, 4]
    assert result["total"] == [3, 9]


# ── _build_sparkline_points ───────────────────────────────────────────────────

def test_build_sparkline_points_empty_or_single_returns_empty_string():
    assert _build_sparkline_points([]) == ""
    assert _build_sparkline_points([{"total_gb": 5}]) == ""


def test_build_sparkline_points_normalizes_range():
    snapshots = [{"total_gb": 0}, {"total_gb": 5}, {"total_gb": 10}]
    points = _build_sparkline_points(snapshots)
    coords = [tuple(map(float, p.split(","))) for p in points.split(" ")]
    # x spans 0..60, y is inverted (higher value -> smaller y)
    assert coords[0][0] == 0.0
    assert coords[-1][0] == 60.0
    assert coords[0][1] == 20.0   # min value -> bottom
    assert coords[-1][1] == 0.0   # max value -> top


def test_build_sparkline_points_constant_values_no_division_by_zero():
    snapshots = [{"total_gb": 10}, {"total_gb": 10}, {"total_gb": 10}]
    points = _build_sparkline_points(snapshots)
    assert points  # should not raise, span defaults to 1
    coords = [tuple(map(float, p.split(","))) for p in points.split(" ")]
    assert all(y == 20.0 for _, y in coords)


# ── functions registered on the Jinja2 environment (closures in _make_env) ───

def _env_globals():
    env = _make_env(Path("/tmp/does-not-need-to-exist"))
    return env.globals, env.filters


def test_make_env_filters_handle_missing_values():
    globals_, filters = _env_globals()
    assert filters["gb"](None) == "—"
    assert filters["gb"](12.345) == "12.3 GB"
    assert filters["fmt_date"](None) == "—"
    assert filters["fmt_date"]("2024-01-15T00:00:00Z") == "Jan 15, 2024"
    assert filters["fmt_date"]("not-a-date") == "not-a-date"
    assert filters["pct"](None) == "—"
    assert filters["pct"](42.7) == "43%"


def test_requester_label():
    globals_, _ = _env_globals()
    fn = globals_["requester_label"]
    assert fn({"request": {"requested": True, "requester_name": "alice"}}) == "alice"
    assert fn({"request": {"requested": True}}) == "Unknown"
    assert fn({"request": {"requested": False}}) is None


def test_requester_status_label_variants():
    globals_, _ = _env_globals()
    fn = globals_["requester_status_label"]
    not_requested = {"request": {"requested": False}}
    assert fn(not_requested) is None

    completed = {"request": {"requested": True, "requester_name": "alice"},
                 "requester_status": {"completed": True}}
    assert fn(completed) == ("completed", "alice completed")

    watched = {"request": {"requested": True, "requester_name": "bob"},
               "requester_status": {"completed": False, "watched": True, "completion_pct": 55}}
    assert fn(watched) == ("watched", "bob watched 55%")

    never = {"request": {"requested": True, "requester_name": "carl"},
             "requester_status": {"completed": False, "watched": False}}
    assert fn(never) == ("never", "carl never watched")


def test_requester_status_pill_relabels_never_to_unwatched():
    globals_, _ = _env_globals()
    fn = globals_["requester_status_pill"]
    never = {"request": {"requested": True, "requester_name": "carl"},
             "requester_status": {"completed": False, "watched": False}}
    assert fn(never) == ("requester-unwatched", "Requested")

    completed = {"request": {"requested": True, "requester_name": "alice"},
                 "requester_status": {"completed": True}}
    assert fn(completed) == ("completed", "Requested")

    assert fn({"request": {"requested": False}}) is None


def test_requester_status_tooltip_variants():
    globals_, _ = _env_globals()
    fn = globals_["requester_status_tooltip"]
    assert fn({"request": {"requested": False}}) is None
    completed = {"request": {"requested": True, "requester_name": "alice"},
                 "requester_status": {"completed": True}}
    assert fn(completed) == "alice has completed this"
    watched = {"request": {"requested": True, "requester_name": "bob"},
               "requester_status": {"completed": False, "watched": True, "completion_pct": 30}}
    assert fn(watched) == "bob is watching this (30% complete)"
    never = {"request": {"requested": True, "requester_name": "carl"},
             "requester_status": {"completed": False, "watched": False}}
    assert fn(never) == "carl has not started watching this"


def test_file_format_tags():
    globals_, _ = _env_globals()
    fn = globals_["file_format_tags"]

    h265 = fn({"file_info": {"video_codec": "H.265", "resolution": "4K", "hdr": True, "bit_depth": 10}})
    assert ("H.265", "codec-h265") in h265
    assert ("4K", "res-4k") in h265
    assert ("HDR", "tag-hdr") in h265
    assert ("10-bit", "tag-bit") in h265

    h264 = fn({"file_info": {"video_codec": "H.264", "resolution": "1080p"}})
    assert h264 == [("H.264", "codec-h264"), ("1080p", "res-hd")]

    other = fn({"file_info": {"video_codec": "MPEG2"}})
    assert other == [("MPEG2", "codec-other")]

    empty = fn({"file_info": {}})
    assert empty == []

    av1 = fn({"file_info": {"video_codec": "AV1"}})
    assert av1 == [("AV1", "codec-h265")]

    eight_bit = fn({"file_info": {"bit_depth": 8}})
    assert eight_bit == []

    no_file_info = fn({})
    assert no_file_info == []


def test_transcode_label_variants():
    globals_, _ = _env_globals()
    fn = globals_["transcode_label"]

    assert fn({}) is None
    assert fn({"transcode_stats": {"total": 0}}) is None
    assert fn({"transcode_stats": None}) is None

    direct = fn({"transcode_stats": {"total": 10, "direct": 9, "transcode": 1}})
    assert direct == ("transcode-direct", "Direct Play")

    bad = fn({"transcode_stats": {"total": 10, "direct": 2, "transcode": 6}})
    assert bad == ("transcode-bad", "Transcodes 60%")

    mixed = fn({"transcode_stats": {"total": 10, "direct": 5, "transcode": 3}})
    assert mixed == ("transcode-mixed", "50% Direct")


def test_deletion_badge_known_and_unknown_recommendations():
    globals_, _ = _env_globals()
    fn = globals_["deletion_badge"]
    assert fn({"deletion": {"recommendation": "strong_delete"}}) == ("danger", "Strong Delete")
    assert fn({"deletion": {"recommendation": "suggest_delete"}}) == ("warning", "Consider Delete")
    assert fn({"deletion": {"recommendation": "keep"}}) == ("safe", "Keep")
    assert fn({"deletion": {"recommendation": "unknown_value"}}) == ("safe", "Keep")
    assert fn({}) == ("safe", "Keep")


def test_top_genres_counts_and_orders_by_frequency():
    globals_, _ = _env_globals()
    fn = globals_["top_genres"]
    items = [
        {"genres": ["Action", "Drama"]},
        {"genres": ["Action", "Comedy"]},
        {"genres": ["Action"]},
        {"genres": ["Drama"]},
    ]
    assert fn(items, n=2) == ["Action", "Drama"]
    assert fn([]) == []


def test_top_genres_limits_to_n():
    globals_, _ = _env_globals()
    fn = globals_["top_genres"]
    items = [{"genres": [f"G{i}"]} for i in range(10)]
    assert len(fn(items, n=3)) == 3
