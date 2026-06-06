from lib.processors.enrichment import _gb, _slug, _requester_watch_status, build_shows, build_movies


# ── _gb ───────────────────────────────────────────────────────────────────────

def test_gb_zero():
    assert _gb(0) == 0.0


def test_gb_exact_gigabyte():
    assert _gb(1024 ** 3) == 1.0


def test_gb_fractional():
    assert _gb(1.5 * 1024 ** 3) == 1.5


def test_gb_large_value():
    assert _gb(500 * 1024 ** 3) == 500.0


# ── _slug ─────────────────────────────────────────────────────────────────────

def test_slug_with_year():
    assert _slug("Breaking Bad", 2008) == "breaking-bad-2008"


def test_slug_without_year():
    assert _slug("Breaking Bad", None) == "breaking-bad"


def test_slug_special_characters():
    assert _slug("Avengers: Endgame!", 2019) == "avengers-endgame-2019"


def test_slug_missing_title_falls_back_to_unknown():
    assert _slug("", None) == "unknown"
    assert _slug(None, None) == "unknown"


def test_slug_zero_year_treated_as_missing():
    # 0 is falsy, so the slug should not include a year suffix
    assert _slug("Some Show", 0) == "some-show"


# ── _requester_watch_status ───────────────────────────────────────────────────

def test_requester_watch_status_no_entry():
    result = _requester_watch_status(None, "show")
    assert result == {
        "watched": False,
        "completed": False,
        "completion_pct": 0,
        "plays": 0,
        "last_watched": None,
    }


def test_requester_watch_status_show_below_threshold():
    entry = {"plays": 1, "completion_pct": 24, "last_watched": "2024-01-01"}
    result = _requester_watch_status(entry, "show")
    assert result["watched"] is False
    assert result["completed"] is False
    assert result["completion_pct"] == 24


def test_requester_watch_status_show_watched_threshold():
    entry = {"plays": 2, "completion_pct": 25, "last_watched": "2024-01-01"}
    result = _requester_watch_status(entry, "show")
    assert result["watched"] is True
    assert result["completed"] is False


def test_requester_watch_status_show_completed_threshold():
    entry = {"plays": 5, "completion_pct": 80, "last_watched": "2024-01-01"}
    result = _requester_watch_status(entry, "show")
    assert result["watched"] is True
    assert result["completed"] is True


def test_requester_watch_status_show_just_below_completed():
    entry = {"plays": 5, "completion_pct": 79.9, "last_watched": "2024-01-01"}
    result = _requester_watch_status(entry, "show")
    assert result["watched"] is True
    assert result["completed"] is False


def test_requester_watch_status_movie_no_plays():
    entry = {"plays": 0, "completion_pct": 0, "last_watched": None}
    result = _requester_watch_status(entry, "movie")
    assert result["watched"] is False
    assert result["completed"] is False


def test_requester_watch_status_movie_one_play():
    entry = {"plays": 1, "completion_pct": 100, "last_watched": "2024-01-01"}
    result = _requester_watch_status(entry, "movie")
    assert result["watched"] is True
    assert result["completed"] is True


def test_requester_watch_status_rounds_completion_pct():
    entry = {"plays": 1, "completion_pct": 33.456, "last_watched": None}
    result = _requester_watch_status(entry, "show")
    assert result["completion_pct"] == 33.5


def test_requester_watch_status_missing_completion_pct_defaults_to_zero():
    entry = {"plays": 0}
    result = _requester_watch_status(entry, "show")
    assert result["completion_pct"] == 0
    assert result["watched"] is False


# ── build_shows ───────────────────────────────────────────────────────────────

def _base_sonarr_item(**overrides):
    item = {
        "id": 1,
        "title": "Test Show",
        "year": 2020,
        "sonarr_id": 100,
        "tmdb_id": 555,
        "size_bytes": 10 * 1024 ** 3,
        "total_episodes": 10,
        "seasons": [],
    }
    item.update(overrides)
    return item


def test_build_shows_basic_no_extra_data():
    items = [_base_sonarr_item()]
    result = build_shows(items, {}, [], {})
    assert len(result) == 1
    show = result[0]
    assert show["title"] == "Test Show"
    assert show["slug"] == "test-show-2020"
    assert show["tmdb_id"] == 555
    assert show["size_gb"] == 10.0
    assert show["request"]["requested"] is False
    assert show["watch_data"] == {}
    assert show["any_watched"] is False
    assert show["on_watchlist"] is False


def test_build_shows_attaches_request_and_watch_data():
    items = [_base_sonarr_item()]
    requests = [{
        "media_type": "tv", "tmdb_id": 555, "requester_id": 9,
        "requester_name": "alice", "requested_at": "2024-01-01",
    }]
    tautulli_tv = {
        555: {
            "_plex_key": "abc123",
            "alice": {
                "plays": 3, "duration_seconds": 1000,
                "last_watched": "2024-02-01", "unique_episodes_watched": 5,
            },
        }
    }
    result = build_shows(items, {}, requests, tautulli_tv)
    show = result[0]
    assert show["request"]["requested"] is True
    assert show["request"]["requester_name"] == "alice"
    assert show["plex_key"] == "abc123"
    assert show["watch_data"]["alice"]["plays"] == 3
    assert show["watch_data"]["alice"]["completion_pct"] == 50.0  # 5/10 * 100
    assert show["requester_status"]["watched"] is True
    assert show["any_watched"] is True
    assert show["total_plays"] == 3


def test_build_shows_most_recent_request_wins():
    items = [_base_sonarr_item()]
    requests = [
        {"media_type": "tv", "tmdb_id": 555, "requester_id": 1,
         "requester_name": "old", "requested_at": "2023-01-01"},
        {"media_type": "tv", "tmdb_id": 555, "requester_id": 2,
         "requester_name": "new", "requested_at": "2024-01-01"},
    ]
    result = build_shows(items, {}, requests, {})
    assert result[0]["request"]["requester_name"] == "new"


def test_build_shows_seasons_aggregate_watch_data():
    item = _base_sonarr_item(seasons=[
        {"season_number": 1, "monitored": True, "episode_count": 5,
         "total_episodes": 5, "size_bytes": 5 * 1024 ** 3},
    ])
    season_watch = {
        555: {1: {"alice": {"plays": 2, "unique_episodes_watched": 4, "last_watched": "2024-01-01"}}}
    }
    result = build_shows([item], {}, [], {}, season_watch=season_watch)
    season = result[0]["seasons"][0]
    assert season["watch_data"]["alice"]["completion_pct"] == 80.0
    assert season["any_watched"] is True
    assert season["total_plays"] == 2
    assert season["size_gb"] == 5.0


def test_build_shows_on_watchlist():
    items = [_base_sonarr_item()]
    requests = [{"media_type": "tv", "tmdb_id": 555, "requester_id": 9,
                 "requester_name": "alice", "requested_at": "2024-01-01"}]
    watchlist = {555: {9}}
    result = build_shows(items, {}, requests, {}, watchlist=watchlist)
    assert result[0]["on_watchlist"] is True


def test_build_shows_sorted_by_size_descending():
    small = _base_sonarr_item(id=1, tmdb_id=1, title="Small", size_bytes=1 * 1024 ** 3)
    large = _base_sonarr_item(id=2, tmdb_id=2, title="Large", size_bytes=100 * 1024 ** 3)
    result = build_shows([small, large], {}, [], {})
    assert [s["title"] for s in result] == ["Large", "Small"]


# ── build_movies ──────────────────────────────────────────────────────────────

def _base_radarr_item(**overrides):
    item = {
        "id": 1,
        "title": "Test Movie",
        "year": 2021,
        "radarr_id": 200,
        "tmdb_id": 777,
        "has_file": True,
        "size_bytes": 5 * 1024 ** 3,
    }
    item.update(overrides)
    return item


def test_build_movies_skips_items_without_file():
    items = [_base_radarr_item(has_file=False)]
    result = build_movies(items, {}, [], {})
    assert result == []


def test_build_movies_basic():
    items = [_base_radarr_item()]
    result = build_movies(items, {}, [], {})
    assert len(result) == 1
    movie = result[0]
    assert movie["slug"] == "test-movie-2021"
    assert movie["type"] == "movie"
    assert movie["size_gb"] == 5.0
    assert movie["any_watched"] is False


def test_build_movies_watch_data_and_completion_defaults():
    items = [_base_radarr_item()]
    tautulli_movies = {
        777: {
            "_plex_key": "xyz",
            "bob": {"plays": 2, "duration_seconds": 500, "last_watched": "2024-01-01"},
        }
    }
    result = build_movies(items, {}, [], tautulli_movies)
    movie = result[0]
    assert movie["plex_key"] == "xyz"
    # No completion_pct provided -> defaults to 100 because plays >= 1
    assert movie["watch_data"]["bob"]["completion_pct"] == 100
    assert movie["any_watched"] is True
    assert movie["total_plays"] == 2


def test_build_movies_on_watchlist_requires_request():
    items = [_base_radarr_item()]
    watchlist = {777: {9}}
    # No request present -> on_watchlist must be False even though tmdb_id is in watchlist
    result = build_movies(items, {}, [], {}, watchlist=watchlist)
    assert result[0]["on_watchlist"] is False


def test_build_movies_sorted_by_size_descending():
    small = _base_radarr_item(id=1, tmdb_id=1, title="Small", size_bytes=1 * 1024 ** 3)
    large = _base_radarr_item(id=2, tmdb_id=2, title="Large", size_bytes=50 * 1024 ** 3)
    result = build_movies([small, large], {}, [], {})
    assert [m["title"] for m in result] == ["Large", "Small"]
