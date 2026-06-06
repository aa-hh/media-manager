import run


# ── _remap_names ──────────────────────────────────────────────────────────────

def test_remap_names_full_map():
    watch_data = {
        "tv": {123: {"old_alice": {"plays": 1}}},
        "movie": {456: {"old_bob": {"plays": 2}}},
        "tv_seasons": {123: {1: {"old_alice": {"plays": 1}}}},
        "users": [{"friendly_name": "old_alice", "id": 1}],
    }
    name_map = {"old_alice": "alice", "old_bob": "bob"}
    result = run._remap_names(watch_data, name_map)

    assert result["tv"][123] == {"alice": {"plays": 1}}
    assert result["movie"][456] == {"bob": {"plays": 2}}
    assert result["tv_seasons"][123][1] == {"alice": {"plays": 1}}
    assert result["users"][0]["friendly_name"] == "alice"


def test_remap_names_partial_map_leaves_unmapped_names():
    watch_data = {
        "tv": {123: {"alice": {"plays": 1}, "unmapped_user": {"plays": 5}}},
        "movie": {},
        "tv_seasons": {},
        "users": [],
    }
    result = run._remap_names(watch_data, {"alice": "Alice Smith"})
    assert result["tv"][123] == {"Alice Smith": {"plays": 1}, "unmapped_user": {"plays": 5}}


def test_remap_names_empty_map_is_identity():
    watch_data = {
        "tv": {1: {"x": {"plays": 1}}},
        "movie": {2: {"y": {"plays": 2}}},
        "tv_seasons": {1: {1: {"x": {"plays": 1}}}},
        "users": [{"friendly_name": "x"}],
    }
    result = run._remap_names(watch_data, {})
    assert result == watch_data


def test_remap_names_handles_missing_keys_gracefully():
    result = run._remap_names({}, {"a": "b"})
    assert result == {"tv": {}, "movie": {}, "tv_seasons": {}, "users": []}


# ── _merge_transcode ──────────────────────────────────────────────────────────

def test_merge_transcode_disjoint_ids_combined():
    base = {1: {"direct": 1, "transcode": 0, "copy": 0, "total": 1, "transcode_qualities": {}}}
    overlay = {2: {"direct": 0, "transcode": 1, "copy": 0, "total": 1, "transcode_qualities": {"1080p": 1}}}
    merged = run._merge_transcode(base, overlay)
    assert merged[1]["direct"] == 1
    assert merged[2]["transcode"] == 1
    assert merged[2]["transcode_qualities"] == {"1080p": 1}


def test_merge_transcode_overlapping_ids_summed():
    base = {1: {"direct": 2, "transcode": 1, "copy": 0, "total": 3,
                "transcode_qualities": {"1080p": 1}, "avg_watch_pct": 50}}
    overlay = {1: {"direct": 1, "transcode": 2, "copy": 1, "total": 4,
                   "transcode_qualities": {"1080p": 2, "720p": 1}, "avg_watch_pct": None}}
    merged = run._merge_transcode(base, overlay)
    rec = merged[1]
    assert rec["direct"] == 3
    assert rec["transcode"] == 3
    assert rec["copy"] == 1
    assert rec["total"] == 7
    assert rec["transcode_qualities"] == {"1080p": 3, "720p": 1}
    # avg_watch_pct is preserved from the existing (base) record
    assert rec["avg_watch_pct"] == 50


def test_merge_transcode_empty_base():
    overlay = {1: {"direct": 1, "transcode": 0, "copy": 0, "total": 1, "transcode_qualities": {}}}
    merged = run._merge_transcode({}, overlay)
    assert merged == overlay
    assert merged is not overlay  # not mutating overlay's dict structure for new keys... value object identity ok


# ── _merge_watch_data ─────────────────────────────────────────────────────────

def test_merge_watch_data_disjoint_sources_combined():
    plex = {"tv": {"100": {"alice": {"plays": 1, "last_watched": "2024-01-01"}}}, "users": []}
    taut = {"tv": {"200": {"bob": {"plays": 2, "last_watched": "2024-02-01"}}}, "users": []}
    merged = run._merge_watch_data(plex, taut)
    assert set(merged["tv"].keys()) == {100, 200}
    assert merged["tv"][100]["alice"]["plays"] == 1
    assert merged["tv"][200]["bob"]["plays"] == 2


def test_merge_watch_data_picks_higher_play_count_but_keeps_latest_date():
    plex = {"tv": {"100": {"alice": {"plays": 5, "last_watched": "2024-03-01"}}}, "users": []}
    taut = {"tv": {"100": {"alice": {"plays": 2, "last_watched": "2024-05-01"}}}, "users": []}
    merged = run._merge_watch_data(plex, taut)
    entry = merged["tv"][100]["alice"]
    # plex has more plays so it "wins" the base record...
    assert entry["plays"] == 5
    # ...but the most recent last_watched across both sources is kept
    assert entry["last_watched"] == "2024-05-01"


def test_merge_watch_data_tautulli_wins_on_higher_plays():
    plex = {"tv": {"100": {"alice": {"plays": 1, "last_watched": "2024-01-01"}}}, "users": []}
    taut = {"tv": {"100": {"alice": {"plays": 9, "last_watched": "2024-01-02"}}}, "users": []}
    merged = run._merge_watch_data(plex, taut)
    assert merged["tv"][100]["alice"]["plays"] == 9


def test_merge_watch_data_preserves_plex_key():
    plex = {"tv": {"100": {"_plex_key": "plexkey1"}}, "users": []}
    taut = {"tv": {"100": {"bob": {"plays": 1, "last_watched": "2024-01-01"}}}, "users": []}
    merged = run._merge_watch_data(plex, taut)
    assert merged["tv"][100]["_plex_key"] == "plexkey1"


def test_merge_watch_data_dedupes_users_by_friendly_name():
    plex = {"tv": {}, "users": [{"friendly_name": "alice", "id": 1}]}
    taut = {"tv": {}, "users": [{"friendly_name": "alice", "id": 1}, {"friendly_name": "bob", "id": 2}]}
    merged = run._merge_watch_data(plex, taut)
    names = [u["friendly_name"] for u in merged["users"]]
    assert names == ["alice", "bob"]


def test_merge_watch_data_merges_seasons_with_int_keys():
    plex = {"tv_seasons": {"100": {"1": {"alice": {"plays": 1, "last_watched": "2024-01-01"}}}}, "users": []}
    taut = {"tv_seasons": {"100": {"1": {"alice": {"plays": 3, "last_watched": "2024-02-01"}}}}, "users": []}
    merged = run._merge_watch_data(plex, taut)
    entry = merged["tv_seasons"][100][1]["alice"]
    assert entry["plays"] == 3
    assert entry["last_watched"] == "2024-02-01"
    # keys should be ints, not strings (JSON round trip normalisation)
    assert list(merged["tv_seasons"].keys()) == [100]
    assert list(merged["tv_seasons"][100].keys()) == [1]


# ── _build_users ──────────────────────────────────────────────────────────────

def _item(id, requester=None, watch_data=None, size_gb=10, watched=False):
    return {
        "id": id,
        "size_gb": size_gb,
        "request": {"requester_name": requester} if requester else {},
        "watch_data": watch_data or {},
        "requester_status": {"watched": watched},
    }


def test_build_users_basic_aggregation():
    shows = [_item(1, requester="alice", watch_data={"alice": {"plays": 2}}, size_gb=10, watched=True)]
    movies = [_item(2, requester="alice", watch_data={}, size_gb=5, watched=False)]
    result = run._build_users(shows, movies, {}, [])

    assert len(result) == 1
    user = result[0]
    assert user["name"] == "alice"
    assert user["requests_made"] == 2
    assert user["requested_item_ids"] == [1, 2]
    assert user["watched_item_ids"] == [1]
    assert user["storage_requested_gb"] == 15
    assert user["storage_watched_gb"] == 10
    assert user["total_plays"] == 2
    assert user["unwatched_request_ids"] == [2]


def test_build_users_includes_watchers_who_did_not_request():
    shows = [_item(1, requester=None, watch_data={"bob": {"plays": 1}}, size_gb=20)]
    result = run._build_users(shows, [], {}, [])
    assert len(result) == 1
    assert result[0]["name"] == "bob"
    assert result[0]["requests_made"] == 0
    assert result[0]["watched_item_ids"] == [1]


def test_build_users_sorted_alphabetically():
    shows = [
        _item(1, requester="zoe", size_gb=1),
        _item(2, requester="alice", size_gb=1),
    ]
    result = run._build_users(shows, [], {}, [])
    assert [u["name"] for u in result] == ["alice", "zoe"]


def test_build_users_no_users_when_no_requests_or_watches():
    result = run._build_users([_item(1)], [_item(2)], {}, [])
    assert result == []
