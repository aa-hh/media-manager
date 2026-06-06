from lib.collectors.tautulli import _process_history


def _new_stores():
    return {}, {}, {}  # store, tv_season_watch, transcode_store


# ── movie history ─────────────────────────────────────────────────────────────

def test_process_history_movie_basic():
    store, season_watch, transcode = _new_stores()
    history = [{
        "rating_key": "1001",
        "title": "Some Movie",
        "guid": "com.plexapp.agents.themoviedb://tmdb://4242?lang=en",
        "duration": 6000,
        "date": "2024-01-01",
        "transcode_decision": "Direct Play",
    }]
    _process_history(history, "movie", "alice", store, season_watch, transcode)

    assert 4242 in store
    rec = store[4242]["alice"]
    assert rec["plays"] == 1
    assert rec["duration_seconds"] == 6000
    assert rec["last_watched"] == "2024-01-01"
    assert rec["completion_pct"] == 100
    assert rec["unique_episodes_watched"] is None

    assert transcode[4242]["direct"] == 1
    assert transcode[4242]["transcode"] == 0
    assert transcode[4242]["total"] == 1


def test_process_history_skips_entries_without_item_key():
    store, season_watch, transcode = _new_stores()
    history = [{"title": "No rating key", "guid": "tmdb://123"}]
    _process_history(history, "movie", "alice", store, season_watch, transcode)
    assert store == {}
    assert transcode == {}


def test_process_history_malformed_guid_results_in_no_tmdb_id():
    store, season_watch, transcode = _new_stores()
    history = [{
        "rating_key": "5",
        "title": "Bad GUID Movie",
        "guid": "tmdb://not-a-number",
        "date": "2024-01-01",
    }]
    _process_history(history, "movie", "alice", store, season_watch, transcode)
    # Without a resolvable tmdb_id the item is skipped from the store entirely
    assert store == {}


def test_process_history_aggregates_multiple_plays_same_movie():
    store, season_watch, transcode = _new_stores()
    history = [
        {"rating_key": "1", "title": "M", "guid": "tmdb://10", "duration": 1000,
         "date": "2024-01-01", "transcode_decision": "Direct Play"},
        {"rating_key": "1", "title": "M", "guid": "tmdb://10", "duration": 2000,
         "date": "2024-02-01", "transcode_decision": "Transcode", "quality_profile": "1080p"},
    ]
    _process_history(history, "movie", "bob", store, season_watch, transcode)

    rec = store[10]["bob"]
    assert rec["plays"] == 2
    assert rec["duration_seconds"] == 3000
    assert rec["last_watched"] == "2024-02-01"

    t = transcode[10]
    assert t["direct"] == 1
    assert t["transcode"] == 1
    assert t["transcode_qualities"] == {"1080p": 1}
    assert t["total"] == 2


def test_process_history_copy_decision_counted():
    store, season_watch, transcode = _new_stores()
    history = [{"rating_key": "1", "title": "M", "guid": "tmdb://10",
                "date": "2024-01-01", "transcode_decision": "Copy"}]
    _process_history(history, "movie", "carl", store, season_watch, transcode)
    assert transcode[10]["copy"] == 1
    assert transcode[10]["direct"] == 0
    assert transcode[10]["transcode"] == 0


def test_process_history_records_watch_percentage_average():
    store, season_watch, transcode = _new_stores()
    history = [
        {"rating_key": "1", "title": "M", "guid": "tmdb://10",
         "date": "2024-01-01", "watched_status": "0.5"},
        {"rating_key": "1", "title": "M", "guid": "tmdb://10",
         "date": "2024-01-02", "watched_status": "1.0"},
    ]
    _process_history(history, "movie", "dana", store, season_watch, transcode)
    assert transcode[10]["avg_watch_pct"] == 0.75


def test_process_history_handles_invalid_watched_status():
    store, season_watch, transcode = _new_stores()
    history = [{"rating_key": "1", "title": "M", "guid": "tmdb://10",
                "date": "2024-01-01", "watched_status": "not-a-number"}]
    _process_history(history, "movie", "erin", store, season_watch, transcode)
    assert transcode[10]["avg_watch_pct"] is None


# ── tv history ────────────────────────────────────────────────────────────────

def test_process_history_tv_uses_grandparent_fields():
    store, season_watch, transcode = _new_stores()
    history = [{
        "grandparent_rating_key": "200",
        "grandparent_title": "Some Show",
        "grandparent_guid": "com.plexapp.agents.themoviedb://tmdb://99?lang=en",
        "rating_key": "201",
        "title": "Episode 1",
        "parent_media_index": 1,
        "media_index": 1,
        "duration": 1200,
        "date": "2024-01-01",
        "transcode_decision": "Direct Play",
    }]
    _process_history(history, "tv", "alice", store, season_watch, transcode)

    assert 99 in store
    rec = store[99]["alice"]
    assert rec["plays"] == 1
    assert rec["unique_episodes_watched"] == 1
    assert rec["completion_pct"] is None  # resolved later during enrichment

    assert 99 in season_watch
    assert season_watch[99][1]["alice"]["unique_episodes_watched"] == 1
    assert season_watch[99][1]["alice"]["plays"] == 1


def test_process_history_tv_dedupes_unique_episodes_on_rewatch():
    store, season_watch, transcode = _new_stores()
    history = [
        {"grandparent_rating_key": "200", "grandparent_title": "Show",
         "grandparent_guid": "tmdb://99", "rating_key": "201",
         "parent_media_index": 1, "media_index": 1, "date": "2024-01-01"},
        {"grandparent_rating_key": "200", "grandparent_title": "Show",
         "grandparent_guid": "tmdb://99", "rating_key": "201",
         "parent_media_index": 1, "media_index": 1, "date": "2024-02-01"},
        {"grandparent_rating_key": "200", "grandparent_title": "Show",
         "grandparent_guid": "tmdb://99", "rating_key": "202",
         "parent_media_index": 1, "media_index": 2, "date": "2024-03-01"},
    ]
    _process_history(history, "tv", "alice", store, season_watch, transcode)

    rec = store[99]["alice"]
    assert rec["plays"] == 3
    # Only two distinct (season, episode) pairs watched
    assert rec["unique_episodes_watched"] == 2
    assert season_watch[99][1]["alice"]["unique_episodes_watched"] == 2
    assert season_watch[99][1]["alice"]["plays"] == 3
    assert season_watch[99][1]["alice"]["last_watched"] == "2024-03-01"


def test_process_history_tv_groups_multiple_seasons():
    store, season_watch, transcode = _new_stores()
    history = [
        {"grandparent_rating_key": "200", "grandparent_title": "Show",
         "grandparent_guid": "tmdb://99", "rating_key": "1",
         "parent_media_index": 1, "media_index": 1, "date": "2024-01-01"},
        {"grandparent_rating_key": "200", "grandparent_title": "Show",
         "grandparent_guid": "tmdb://99", "rating_key": "2",
         "parent_media_index": 2, "media_index": 1, "date": "2024-02-01"},
    ]
    _process_history(history, "tv", "alice", store, season_watch, transcode)
    assert set(season_watch[99].keys()) == {1, 2}


def test_process_history_keeps_existing_tmdb_id_if_already_known():
    store, season_watch, transcode = _new_stores()
    history = [
        {"rating_key": "1", "title": "M", "guid": "tmdb://10", "date": "2024-01-01"},
        {"rating_key": "1", "title": "M", "guid": "", "date": "2024-01-02"},
    ]
    _process_history(history, "movie", "alice", store, season_watch, transcode)
    assert list(store.keys()) == [10]
    assert store[10]["alice"]["plays"] == 2


def test_process_history_empty_history_is_noop():
    store, season_watch, transcode = _new_stores()
    _process_history([], "movie", "alice", store, season_watch, transcode)
    assert store == {} and season_watch == {} and transcode == {}


def test_process_history_merges_transcode_stats_across_calls():
    store, season_watch, transcode = _new_stores()
    h1 = [{"rating_key": "1", "title": "M", "guid": "tmdb://10",
           "date": "2024-01-01", "transcode_decision": "Direct Play"}]
    h2 = [{"rating_key": "1", "title": "M", "guid": "tmdb://10",
           "date": "2024-01-02", "transcode_decision": "Transcode", "quality_profile": "720p"}]
    _process_history(h1, "movie", "alice", store, season_watch, transcode)
    _process_history(h2, "movie", "bob", store, season_watch, transcode)

    t = transcode[10]
    assert t["direct"] == 1
    assert t["transcode"] == 1
    assert t["total"] == 2
    assert t["transcode_qualities"] == {"720p": 1}
