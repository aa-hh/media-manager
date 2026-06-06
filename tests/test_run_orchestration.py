import json

import pytest

import run


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    cache_dir = tmp_path / "cache"
    config_dir = tmp_path / "config"
    for d in (data_dir, cache_dir, config_dir):
        d.mkdir()
    monkeypatch.setattr(run, "DATA_DIR", data_dir)
    monkeypatch.setattr(run, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(run, "CONFIG_DIR", config_dir)
    return {"data": data_dir, "cache": cache_dir, "config": config_dir}


# ── _load_user_identities ─────────────────────────────────────────────────────

def test_load_user_identities_missing_file(dirs):
    assert run._load_user_identities() == ({}, {})


def test_load_user_identities_builds_maps(dirs):
    (dirs["config"] / "users.json").write_text(json.dumps([
        {"name": "Alice", "plex_names": ["alice_plex", "alice2"], "seerr_id": 1},
        {"name": "Bob", "plex_names": ["bob_plex"]},
    ]))
    plex_map, seerr_map = run._load_user_identities()
    assert plex_map == {"alice_plex": "Alice", "alice2": "Alice", "bob_plex": "Bob"}
    assert seerr_map == {1: "Alice"}


# ── _record_run ───────────────────────────────────────────────────────────────

def test_record_run_creates_and_updates_file(dirs):
    run._record_run("sonarr")
    data = json.loads((dirs["data"] / "job_runs.json").read_text())
    assert "sonarr" in data

    run._record_run("radarr")
    data2 = json.loads((dirs["data"] / "job_runs.json").read_text())
    assert "sonarr" in data2 and "radarr" in data2


def test_record_run_handles_corrupt_existing_file(dirs):
    (dirs["data"] / "job_runs.json").write_text("not json")
    run._record_run("sonarr")
    data = json.loads((dirs["data"] / "job_runs.json").read_text())
    assert "sonarr" in data


# ── _load_raw ─────────────────────────────────────────────────────────────────

def test_load_raw_missing_returns_default(dirs):
    assert run._load_raw("sonarr", []) == []


def test_load_raw_reads_existing(dirs):
    (dirs["data"] / "raw_sonarr.json").write_text(json.dumps([{"id": 1}]))
    assert run._load_raw("sonarr", []) == [{"id": 1}]


def test_load_raw_handles_corrupt(dirs):
    (dirs["data"] / "raw_sonarr.json").write_text("not json")
    assert run._load_raw("sonarr", "default") == "default"


# ── _cfg ──────────────────────────────────────────────────────────────────────

def test_cfg_reads_config_values(monkeypatch):
    values = {
        "SONARR_URL": "http://a.local, http://b.local",
        "SONARR_API_KEY": "k1,k2",
        "RADARR_URL": "http://r.local",
        "PLEX_URL": "http://plex.local/",
        "PLEX_TV_SECTIONS": "1,2",
    }
    monkeypatch.setattr(run.config, "get", lambda name, default="": values.get(name, default))
    c = run._cfg()
    assert c["sonarr_urls"] == ["http://a.local", "http://b.local"]
    assert c["sonarr_keys"] == ["k1", "k2"]
    assert c["radarr_urls"] == ["http://r.local"]
    assert c["plex_url"] == "http://plex.local"
    assert c["tv_section_ids"] == ["1", "2"]
    assert c["movie_section_ids"] == ["2"]


# ── fetch_* orchestration ─────────────────────────────────────────────────────

def _patch_cfg(mocker, **overrides):
    base = {
        "sonarr_urls": [], "sonarr_keys": [], "radarr_urls": [], "radarr_keys": [],
        "seerr_url": "", "seerr_key": "", "tautulli_url": "", "tautulli_key": "",
        "plex_url": "", "plex_token": "", "tmdb_key": "",
        "tv_section_ids": ["1"], "movie_section_ids": ["2"],
    }
    base.update(overrides)
    mocker.patch.object(run, "_cfg", return_value=base)
    return base


def test_fetch_sonarr_not_configured(dirs, mocker):
    _patch_cfg(mocker)
    build_mock = mocker.patch.object(run, "build")
    run.fetch_sonarr()
    saved = json.loads((dirs["data"] / "raw_sonarr.json").read_text())
    assert saved == []
    build_mock.assert_called_once()


def test_fetch_sonarr_fetches_and_handles_errors(dirs, mocker):
    _patch_cfg(mocker, sonarr_urls=["http://a.local", "http://b.local"], sonarr_keys=["k1"])
    mocker.patch.object(run.sonarr, "fetch", side_effect=[[{"id": "tv:1"}], RuntimeError("boom")])
    build_mock = mocker.patch.object(run, "build")
    run.fetch_sonarr()
    saved = json.loads((dirs["data"] / "raw_sonarr.json").read_text())
    assert saved == [{"id": "tv:1"}]
    build_mock.assert_called_once()


def test_fetch_sonarr_skips_build_when_flag_false(dirs, mocker):
    _patch_cfg(mocker)
    build_mock = mocker.patch.object(run, "build")
    run.fetch_sonarr(_build=False)
    build_mock.assert_not_called()


def test_fetch_radarr_not_configured(dirs, mocker):
    _patch_cfg(mocker)
    mocker.patch.object(run, "build")
    run.fetch_radarr()
    saved = json.loads((dirs["data"] / "raw_radarr.json").read_text())
    assert saved == []


def test_fetch_radarr_fetches_and_handles_errors(dirs, mocker):
    _patch_cfg(mocker, radarr_urls=["http://r.local"], radarr_keys=["k1"])
    mocker.patch.object(run.radarr, "fetch", return_value=[{"id": "movie:1"}])
    mocker.patch.object(run, "build")
    run.fetch_radarr()
    saved = json.loads((dirs["data"] / "raw_radarr.json").read_text())
    assert saved == [{"id": "movie:1"}]


def test_fetch_overseerr_not_configured(dirs, mocker):
    _patch_cfg(mocker)
    mocker.patch.object(run, "build")
    run.fetch_overseerr()
    saved = json.loads((dirs["data"] / "raw_overseerr.json").read_text())
    assert saved == {"requests": [], "users": {}, "watchlist": []}


def test_fetch_overseerr_remaps_requester_names(dirs, mocker):
    _patch_cfg(mocker, seerr_url="http://seerr.local", seerr_key="key")
    mocker.patch.object(run, "_load_user_identities", return_value=({}, {42: "Alice"}))
    mocker.patch.object(run.overseerr, "fetch", return_value=(
        [{"id": 1, "requester_id": 42, "requester_name": "alice_seerr"}],
        {1: {"id": 1, "name": "alice_seerr"}},
    ))
    mocker.patch.object(run.overseerr, "fetch_watchlist", return_value={("tv", 100): {1}})
    mocker.patch.object(run, "build")
    run.fetch_overseerr()
    saved = json.loads((dirs["data"] / "raw_overseerr.json").read_text())
    assert saved["requests"][0]["requester_name"] == "Alice"
    assert saved["watchlist"] == [["tv", 100, [1]]]


def test_fetch_overseerr_handles_fetch_errors(dirs, mocker):
    _patch_cfg(mocker, seerr_url="http://seerr.local", seerr_key="key")
    mocker.patch.object(run, "_load_user_identities", return_value=({}, {}))
    mocker.patch.object(run.overseerr, "fetch", side_effect=RuntimeError("boom"))
    mocker.patch.object(run.overseerr, "fetch_watchlist", side_effect=RuntimeError("boom2"))
    mocker.patch.object(run, "build")
    run.fetch_overseerr()
    saved = json.loads((dirs["data"] / "raw_overseerr.json").read_text())
    assert saved["requests"] == []


def test_fetch_watch_history_neither_configured(dirs, mocker):
    _patch_cfg(mocker)
    mocker.patch.object(run, "build")
    run.fetch_watch_history()
    plex_saved = json.loads((dirs["data"] / "raw_plex.json").read_text())
    tautulli_saved = json.loads((dirs["data"] / "raw_tautulli.json").read_text())
    assert plex_saved["tv"] == {}
    assert tautulli_saved["tv"] == {}


def test_fetch_watch_history_fetches_both_and_remaps(dirs, mocker):
    _patch_cfg(mocker, plex_url="http://plex.local", plex_token="tok",
               tautulli_url="http://t.local", tautulli_key="key")
    mocker.patch.object(run, "_load_user_identities", return_value=({"raw": "Canonical"}, {}))
    mocker.patch.object(run.plex, "fetch", return_value={"tv": {1: {"raw": {}}}, "movie": {}, "users": [], "machine_id": "m1", "tv_seasons": {}})
    mocker.patch.object(run.tautulli, "fetch", return_value={"tv": {1: {"raw": {}}}, "movie": {}, "users": [], "tv_seasons": {}})
    mocker.patch.object(run, "build")
    run.fetch_watch_history()
    plex_saved = json.loads((dirs["data"] / "raw_plex.json").read_text())
    tautulli_saved = json.loads((dirs["data"] / "raw_tautulli.json").read_text())
    assert plex_saved["machine_id"] == "m1"
    assert tautulli_saved["tv"]["1"]["Canonical"] == {}


def test_fetch_watch_history_handles_fetch_errors(dirs, mocker):
    _patch_cfg(mocker, plex_url="http://plex.local", plex_token="tok",
               tautulli_url="http://t.local", tautulli_key="key")
    mocker.patch.object(run, "_load_user_identities", return_value=({}, {}))
    mocker.patch.object(run.plex, "fetch", side_effect=RuntimeError("boom"))
    mocker.patch.object(run.tautulli, "fetch", side_effect=RuntimeError("boom2"))
    mocker.patch.object(run, "build")
    run.fetch_watch_history()
    plex_saved = json.loads((dirs["data"] / "raw_plex.json").read_text())
    assert plex_saved["tv"] == {}


def test_fetch_tmdb_skips_when_no_key(dirs, mocker):
    _patch_cfg(mocker)
    enrich_mock = mocker.patch.object(run.tmdb, "enrich")
    run.fetch_tmdb()
    enrich_mock.assert_not_called()


def test_fetch_tmdb_enriches_when_key_present(dirs, mocker):
    _patch_cfg(mocker, tmdb_key="key")
    mocker.patch.object(run, "_load_raw", return_value=[])
    enrich_mock = mocker.patch.object(run.tmdb, "enrich")
    build_mock = mocker.patch.object(run, "build")
    run.fetch_tmdb()
    assert enrich_mock.call_count == 2
    build_mock.assert_called_once()


def test_fetch_services_writes_data(dirs, mocker):
    mocker.patch.object(run.services, "collect", return_value={"checked_at": "now", "services": []})
    run.fetch_services()
    saved = json.loads((dirs["data"] / "services.json").read_text())
    assert saved["services"] == []


# ── _load_webhook_transcode ───────────────────────────────────────────────────

def test_load_webhook_transcode_missing_db(dirs):
    assert run._load_webhook_transcode() == ({}, {})


def test_load_webhook_transcode_aggregates_rows(dirs):
    import sqlite3
    db_path = dirs["data"] / "webhook_plays.db"
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE plays (tmdb_id INTEGER, media_type TEXT, transcode_decision TEXT, event TEXT, quality_profile TEXT)")
    con.executemany("INSERT INTO plays VALUES (?, ?, ?, ?, ?)", [
        (100, "show", "Direct Play", "play", None),
        (100, "show", "Transcode", "play", "1080p"),
        (200, "movie", "Copy", "play", None),
        (None, "movie", "Direct Play", "play", None),
    ])
    con.commit()
    con.close()

    tv, movie = run._load_webhook_transcode()
    assert tv[100]["direct"] == 1
    assert tv[100]["transcode"] == 1
    assert tv[100]["transcode_qualities"] == {"1080p": 1}
    assert movie[200]["copy"] == 1


def test_load_webhook_transcode_handles_db_errors(dirs, mocker):
    db_path = dirs["data"] / "webhook_plays.db"
    db_path.write_text("not a real db")
    tv, movie = run._load_webhook_transcode()
    assert tv == {} and movie == {}


# ── build / collect / generate orchestration ─────────────────────────────────

def test_build_combines_data_and_writes_outputs(dirs, mocker):
    (dirs["data"] / "raw_sonarr.json").write_text(json.dumps([{"id": "tv:1"}]))
    (dirs["data"] / "raw_radarr.json").write_text(json.dumps([{"id": "movie:1"}]))
    mocker.patch.object(run, "_merge_watch_data", return_value={"tv": {}, "movie": {}, "users": [], "tv_seasons": {}})
    mocker.patch.object(run, "_load_webhook_transcode", return_value=({}, {}))
    mocker.patch.object(run.enrichment, "build_shows", return_value=[{"id": "tv:1"}])
    mocker.patch.object(run.enrichment, "build_movies", return_value=[{"id": "movie:1"}])
    mocker.patch.object(run.deletion, "apply", side_effect=lambda items: items)
    mocker.patch.object(run.forecasting, "record_snapshot")
    mocker.patch.object(run, "_build_users", return_value=[{"name": "alice"}])

    run.build()

    assert json.loads((dirs["data"] / "tv.json").read_text()) == [{"id": "tv:1"}]
    assert json.loads((dirs["data"] / "movies.json").read_text()) == [{"id": "movie:1"}]
    assert json.loads((dirs["data"] / "users.json").read_text()) == [{"name": "alice"}]
    runs = json.loads((dirs["data"] / "job_runs.json").read_text())
    assert "build" in runs


def test_collect_requires_sonarr_or_radarr(dirs, mocker):
    _patch_cfg(mocker)
    with pytest.raises(RuntimeError, match="SONARR_URL or RADARR_URL"):
        run.collect()


def test_collect_runs_full_pipeline(dirs, mocker):
    _patch_cfg(mocker, sonarr_urls=["http://a.local"])
    fs = mocker.patch.object(run, "fetch_sonarr")
    fr = mocker.patch.object(run, "fetch_radarr")
    ft = mocker.patch.object(run, "fetch_tmdb")
    fo = mocker.patch.object(run, "fetch_overseerr")
    fw = mocker.patch.object(run, "fetch_watch_history")
    fsv = mocker.patch.object(run, "fetch_services")
    fb = mocker.patch.object(run, "build")

    run.collect()

    for m in (fs, fr, ft, fo, fw, fsv, fb):
        m.assert_called_once()
    runs = json.loads((dirs["data"] / "job_runs.json").read_text())
    assert "collect" in runs


def test_generate_raises_when_data_missing(dirs):
    with pytest.raises(RuntimeError, match="run 'collect' first"):
        run.generate()


def test_generate_renders_with_loaded_data(dirs, mocker):
    (dirs["data"] / "tv.json").write_text(json.dumps([{"id": "tv:1"}]))
    (dirs["data"] / "movies.json").write_text(json.dumps([{"id": "movie:1"}]))
    (dirs["data"] / "users.json").write_text(json.dumps([{"name": "alice"}]))
    (dirs["data"] / "services.json").write_text(json.dumps({"services": []}))

    mocker.patch.object(run.forecasting, "calculate", return_value={"forecast": True})
    mocker.patch.object(run, "_get_capacity", return_value=1000)
    render_mock = mocker.patch("generate.render_all")

    run.generate()

    render_mock.assert_called_once()
    kwargs = render_mock.call_args.kwargs
    assert kwargs["shows"] == [{"id": "tv:1"}]
    assert kwargs["forecast"] == {"forecast": True}
    runs = json.loads((dirs["data"] / "job_runs.json").read_text())
    assert "generate" in runs


def test_generate_handles_corrupt_services_file(dirs, mocker):
    (dirs["data"] / "tv.json").write_text(json.dumps([]))
    (dirs["data"] / "movies.json").write_text(json.dumps([]))
    (dirs["data"] / "users.json").write_text(json.dumps([]))
    (dirs["data"] / "services.json").write_text("not json")

    mocker.patch.object(run.forecasting, "calculate", return_value={})
    mocker.patch.object(run, "_get_capacity", return_value=1000)
    render_mock = mocker.patch("generate.render_all")

    run.generate()
    assert render_mock.call_args.kwargs["services"] == {}
