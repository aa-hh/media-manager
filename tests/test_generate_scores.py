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
    _chart_data_tv_top_seasons,
    _chart_data_quality_by_type,
    _chart_data_watched_unwatched,
    _chart_data_content_age,
    _chart_data_resolution_codec,
    _build_content_age_scatter,
    _compute_codec_efficiency,
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


# ── smart_size filter ─────────────────────────────────────────────────────────

def test_smart_size_none_returns_dash():
    _, filters = _env_globals()
    assert filters["smart_size"](None) == "—"

def test_smart_size_below_1000_returns_gb():
    _, filters = _env_globals()
    assert filters["smart_size"](500) == "500.0 GB"

def test_smart_size_above_1000_returns_tb():
    _, filters = _env_globals()
    assert filters["smart_size"](2369.31) == "2.37 TB"

def test_smart_size_exactly_1000():
    _, filters = _env_globals()
    assert filters["smart_size"](1000) == "1.00 TB"


# ── _chart_data_tv_top_seasons ────────────────────────────────────────────────

def _make_show(title, seasons):
    return {
        "title": title,
        "seasons": [
            {"season_number": s, "size_gb": gb, "deletion": {"recommendation": rec}}
            for s, gb, rec in seasons
        ]
    }

def test_tv_top_seasons_sorted_by_size():
    shows = [
        _make_show("Alpha", [(1, 10.0, "keep"), (2, 50.0, "suggest_delete")]),
        _make_show("Beta", [(1, 30.0, "strong_delete")]),
    ]
    result = _chart_data_tv_top_seasons(shows)
    assert result["data"][0] == 50.0
    assert "S2" in result["labels"][0]
    assert result["data"] == sorted(result["data"], reverse=True)

def test_tv_top_seasons_respects_n_limit():
    shows = [_make_show("X", [(i, float(i*10), "keep") for i in range(1, 6)])]
    result = _chart_data_tv_top_seasons(shows, n=3)
    assert len(result["labels"]) == 3

def test_tv_top_seasons_skips_zero_size_seasons():
    shows = [_make_show("Y", [(1, 0.0, "keep"), (2, 20.0, "keep")])]
    result = _chart_data_tv_top_seasons(shows)
    assert len(result["labels"]) == 1

def test_tv_top_seasons_colors_match_deletion_rec():
    shows = [_make_show("Z", [(1, 10.0, "strong_delete")])]
    result = _chart_data_tv_top_seasons(shows)
    assert result["colors"][0] == "#e05252"

def test_tv_top_seasons_label_format():
    shows = [_make_show("ShowName", [(3, 15.0, "keep")])]
    result = _chart_data_tv_top_seasons(shows)
    assert result["labels"][0] == "ShowName S3"


# ── _chart_data_quality_by_type ───────────────────────────────────────────────

def _make_item_with_res(res, gb, itype="movie"):
    return {"file_info": {"resolution": res}, "size_gb": gb, "type": itype}

def test_quality_by_type_normalizes_resolutions():
    items = [
        _make_item_with_res("4K", 10),
        _make_item_with_res("2160p", 5),
        _make_item_with_res("1080p", 20),
        _make_item_with_res("1080", 3),
        _make_item_with_res("720p", 8),
        _make_item_with_res("unknown_res", 2),
    ]
    result = _chart_data_quality_by_type(items)
    labels = result["labels"]
    data = result["data"]
    assert data[labels.index("4K")] == 15.0
    assert data[labels.index("1080p")] == 23.0
    assert data[labels.index("720p")] == 8.0
    assert data[labels.index("Other")] == 2.0

def test_quality_by_type_empty_items():
    result = _chart_data_quality_by_type([])
    assert all(d == 0 for d in result["data"])

def test_quality_by_type_missing_file_info():
    items = [{"size_gb": 10, "type": "movie"}]  # no file_info
    result = _chart_data_quality_by_type(items)
    labels = result["labels"]
    assert result["data"][labels.index("Other")] == 10.0


# ── _chart_data_watched_unwatched ─────────────────────────────────────────────

def test_watched_unwatched_sums_correctly():
    items = [
        {"any_watched": True, "size_gb": 10},
        {"any_watched": True, "size_gb": 5},
        {"any_watched": False, "size_gb": 20},
    ]
    result = _chart_data_watched_unwatched(items)
    assert result["data"][0] == 15.0
    assert result["data"][1] == 20.0

def test_watched_unwatched_all_watched():
    items = [{"any_watched": True, "size_gb": 10}]
    result = _chart_data_watched_unwatched(items)
    assert result["data"][1] == 0.0

def test_watched_unwatched_labels():
    result = _chart_data_watched_unwatched([])
    assert result["labels"] == ["Watched", "Unwatched"]


# ── _chart_data_content_age ───────────────────────────────────────────────────

def test_content_age_groups_by_year():
    items = [
        {"added_at": "2022-06-01T00:00:00Z", "size_gb": 10, "type": "show"},
        {"added_at": "2022-11-01T00:00:00Z", "size_gb": 5, "type": "movie"},
        {"added_at": "2023-01-01T00:00:00Z", "size_gb": 20, "type": "show"},
    ]
    result = _chart_data_content_age(items)
    assert 2022 in result["labels"]
    assert 2023 in result["labels"]
    idx22 = result["labels"].index(2022)
    assert result["tv"][idx22] == 10.0
    assert result["movie"][idx22] == 5.0

def test_content_age_skips_missing_added_at():
    items = [
        {"size_gb": 10, "type": "movie"},  # no added_at
        {"added_at": "2023-01-01T00:00:00Z", "size_gb": 5, "type": "movie"},
    ]
    result = _chart_data_content_age(items)
    assert len(result["labels"]) == 1

def test_content_age_labels_are_sorted():
    items = [
        {"added_at": "2023-01-01T00:00:00Z", "size_gb": 1, "type": "movie"},
        {"added_at": "2021-01-01T00:00:00Z", "size_gb": 1, "type": "movie"},
        {"added_at": "2022-01-01T00:00:00Z", "size_gb": 1, "type": "movie"},
    ]
    result = _chart_data_content_age(items)
    assert result["labels"] == sorted(result["labels"])


# ── _chart_data_resolution_codec ──────────────────────────────────────────────

def _make_codec_item(codec, res, gb):
    return {"file_info": {"video_codec": codec, "resolution": res}, "size_gb": gb}

def test_resolution_codec_structure():
    result = _chart_data_resolution_codec([])
    assert result["labels"] == ["4K", "1080p", "720p", "Other"]
    assert len(result["datasets"]) == 3
    labels = [d["label"] for d in result["datasets"]]
    assert "H.265" in labels
    assert "H.264" in labels
    assert "Other" in labels

def test_resolution_codec_buckets_correctly():
    items = [_make_codec_item("H.265", "4K", 100)]
    result = _chart_data_resolution_codec(items)
    h265_ds = next(d for d in result["datasets"] if d["label"] == "H.265")
    assert h265_ds["data"][0] == 100.0  # index 0 = 4K

def test_resolution_codec_normalizes_codec_strings():
    items = [
        _make_codec_item("hevc", "1080p", 10),
        _make_codec_item("HEVC", "1080p", 10),
        _make_codec_item("avc", "720p", 5),
        _make_codec_item("H.264", "720p", 5),
        _make_codec_item("ProRes", "4K", 8),
    ]
    result = _chart_data_resolution_codec(items)
    h265 = next(d for d in result["datasets"] if d["label"] == "H.265")
    h264 = next(d for d in result["datasets"] if d["label"] == "H.264")
    other = next(d for d in result["datasets"] if d["label"] == "Other")
    assert h265["data"][1] == 20.0   # 1080p index
    assert h264["data"][2] == 10.0   # 720p index
    assert other["data"][0] == 8.0   # 4K index

def test_resolution_codec_empty_items():
    result = _chart_data_resolution_codec([])
    for ds in result["datasets"]:
        assert ds["data"] == [0, 0, 0, 0]


# ── _build_content_age_scatter ────────────────────────────────────────────────

def _make_scatter_item(days_ago, size_gb, watched=False, score=0, itype="movie"):
    from datetime import datetime, timedelta, timezone
    added = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    return {
        "added_at": added,
        "size_gb": size_gb,
        "type": itype,
        "slug": "test-slug",
        "title": "Test",
        "any_watched": watched,
        "deletion": {"score": score},
    }

def test_content_age_scatter_basic_fields():
    items = [_make_scatter_item(30, 10, watched=True, score=45)]
    result = _build_content_age_scatter(items)
    assert len(result) == 1
    r = result[0]
    assert "title" in r and "slug" in r and "type" in r
    assert "size_gb" in r and "days_since_added" in r
    assert "any_watched" in r and "deletion_score" in r
    assert r["any_watched"] is True
    assert r["deletion_score"] == 45

def test_content_age_scatter_skips_missing_added_at():
    items = [
        {"size_gb": 10, "type": "movie", "slug": "x", "title": "X", "any_watched": False, "deletion": {}},
        _make_scatter_item(10, 5),
    ]
    result = _build_content_age_scatter(items)
    assert len(result) == 1

def test_content_age_scatter_respects_200_limit():
    items = [_make_scatter_item(i, 1) for i in range(250)]
    result = _build_content_age_scatter(items)
    assert len(result) == 200

def test_content_age_scatter_days_since_added_is_nonneg_int():
    items = [_make_scatter_item(15, 10)]
    result = _build_content_age_scatter(items)
    assert isinstance(result[0]["days_since_added"], int)
    assert result[0]["days_since_added"] >= 0


# ── _compute_codec_efficiency ─────────────────────────────────────────────────

def _item_with_codec(codec, gb):
    return {"file_info": {"video_codec": codec}, "size_gb": gb}

def test_codec_efficiency_h264_bucket():
    items = [_item_with_codec("H.264", 100)]
    result = _compute_codec_efficiency(items)
    assert result["h264_gb"] == 100.0
    assert result["h265_gb"] == 0.0

def test_codec_efficiency_h265_bucket():
    items = [_item_with_codec("HEVC", 80)]
    result = _compute_codec_efficiency(items)
    assert result["h265_gb"] == 80.0
    assert result["h264_gb"] == 0.0

def test_codec_efficiency_other_bucket():
    items = [_item_with_codec("ProRes", 30)]
    result = _compute_codec_efficiency(items)
    assert result["other_gb"] == 30.0

def test_codec_efficiency_estimated_savings_is_45pct_of_h264():
    items = [_item_with_codec("H.264", 200)]
    result = _compute_codec_efficiency(items)
    assert result["estimated_savings_gb"] == round(200 * 0.45, 2)

def test_codec_efficiency_case_insensitive():
    items = [
        _item_with_codec("avc", 50),
        _item_with_codec("hevc", 40),
        _item_with_codec("h.265", 10),
    ]
    result = _compute_codec_efficiency(items)
    assert result["h264_gb"] == 50.0
    assert result["h265_gb"] == 50.0

def test_codec_efficiency_missing_file_info():
    items = [{"size_gb": 10}]
    result = _compute_codec_efficiency(items)
    assert result["other_gb"] == 10.0
