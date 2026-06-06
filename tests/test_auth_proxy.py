import json
import os
import sys
import importlib

import pytest


@pytest.fixture
def app_module(tmp_path, monkeypatch):
    """Import (or re-import) auth_proxy.app with isolated config/data dirs and a fixed secret key."""
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    public_dir = tmp_path / "public"
    project_dir = tmp_path / "project"
    for d in (config_dir, data_dir, public_dir, project_dir):
        d.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("AUTH_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("AUTH_API_KEY", "test-api-key")
    monkeypatch.setenv("CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("PUBLIC_DIR", str(public_dir))
    monkeypatch.setenv("PROJECT_DIR", str(project_dir))
    monkeypatch.delenv("PLEX_URL", raising=False)
    monkeypatch.delenv("PLEX_TOKEN", raising=False)
    monkeypatch.delenv("SETUP_COMPLETE", raising=False)

    sys.modules.pop("auth_proxy.app", None)
    sys.modules.pop("auth_proxy", None)
    mod = importlib.import_module("auth_proxy.app")
    yield mod
    sys.modules.pop("auth_proxy.app", None)
    sys.modules.pop("auth_proxy", None)


@pytest.fixture
def client(app_module):
    from starlette.testclient import TestClient
    return TestClient(app_module.app)


# ── _is_setup_complete ────────────────────────────────────────────────────────

def test_is_setup_complete_false_by_default(app_module):
    assert app_module._is_setup_complete() is False


def test_is_setup_complete_true_via_env(app_module, monkeypatch):
    monkeypatch.setenv("SETUP_COMPLETE", "true")
    assert app_module._is_setup_complete() is True


def test_is_setup_complete_true_via_config_file(app_module):
    app_module.CONFIG_ENV_PATH.write_text("FOO=bar\nSETUP_COMPLETE=TRUE\n")
    assert app_module._is_setup_complete() is True


def test_is_setup_complete_true_via_config_file_numeric(app_module):
    app_module.CONFIG_ENV_PATH.write_text("SETUP_COMPLETE=1\n")
    assert app_module._is_setup_complete() is True


def test_is_setup_complete_false_when_other_keys_present(app_module):
    app_module.CONFIG_ENV_PATH.write_text("FOO=bar\nSETUP_COMPLETE=false\n")
    assert app_module._is_setup_complete() is False


# ── _read_config_env / _write_config_env ──────────────────────────────────────

def test_read_config_env_parses_lines(app_module):
    app_module.CONFIG_ENV_PATH.write_text('# comment\nFOO=bar\nQUOTED="value"\nNO_EQUALS\n')
    result = app_module._read_config_env()
    assert result == {"FOO": "bar", "QUOTED": "value"}


def test_read_config_env_missing_file_returns_empty(app_module):
    assert app_module._read_config_env() == {}


def test_write_config_env_merges_with_existing(app_module):
    app_module.CONFIG_ENV_PATH.write_text("EXISTING=old\n")
    app_module._write_config_env({"NEW_KEY": "value"})
    result = app_module._read_config_env()
    assert result["EXISTING"] == "old"
    assert result["NEW_KEY"] == "value"


def test_write_config_env_validates_url_keys(app_module):
    with pytest.raises(ValueError, match="SONARR_URL"):
        app_module._write_config_env({"SONARR_URL": "not-a-url"})


def test_write_config_env_allows_valid_urls(app_module):
    app_module._write_config_env({"SONARR_URL": "http://sonarr.local:8989"})
    assert app_module._read_config_env()["SONARR_URL"] == "http://sonarr.local:8989"


# ── user mappings ─────────────────────────────────────────────────────────────

def test_read_user_mappings_missing_file_returns_empty(app_module):
    assert app_module._read_user_mappings() == []


def test_write_then_read_user_mappings_roundtrip(app_module):
    mappings = [{"name": "alice", "plex_names": ["alice_plex"]}]
    app_module._write_user_mappings(mappings)
    assert app_module._read_user_mappings() == mappings


# ── basic routes ──────────────────────────────────────────────────────────────

def test_login_page_renders_with_client_id(client, app_module):
    resp = client.get("/auth/login", follow_redirects=False)
    assert resp.status_code == 200
    assert app_module.CLIENT_ID in resp.text


def test_waiting_page_renders(client):
    resp = client.get("/auth/waiting", follow_redirects=False)
    assert resp.status_code == 200
    assert "closing" in resp.text.lower()


def test_validate_requires_token(client):
    resp = client.post("/auth/validate", json={})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Missing token."


def test_validate_sets_session_cookie_when_no_machine_id(client, app_module):
    # server_machine_id is None (Plex not configured) -> token accepted without verification
    assert app_module.server_machine_id is None
    resp = client.post("/auth/validate", json={"token": "plex-token-abc"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert app_module.COOKIE_SESSION in resp.cookies


def test_logout_redirects_and_clears_cookie(client):
    resp = client.get("/auth/logout", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/"


# ── middleware: setup-not-complete redirect ───────────────────────────────────

def test_middleware_redirects_to_setup_when_incomplete(client):
    resp = client.get("/dashboard", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/setup"


def test_middleware_allows_setup_routes_when_incomplete(client):
    resp = client.get("/setup", follow_redirects=False)
    assert resp.status_code != 302


def test_middleware_allows_auth_routes_when_setup_incomplete(client):
    resp = client.get("/auth/login", follow_redirects=False)
    assert resp.status_code == 200


def test_middleware_skips_auth_for_tautulli_webhook_even_when_incomplete(client, mocker):
    # the webhook handler itself may fail on bad payloads, but the middleware
    # should not redirect it to /setup
    resp = client.post("/api/tautulli/webhook", data={})
    assert resp.status_code != 302


# ── middleware: auth check once setup is complete ────────────────────────────

def test_middleware_redirects_to_login_without_session_or_key(client, app_module):
    app_module.CONFIG_ENV_PATH.write_text("SETUP_COMPLETE=TRUE\n")
    resp = client.get("/dashboard", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/auth/login"


def test_middleware_authenticates_with_valid_session_cookie(client, app_module):
    app_module.CONFIG_ENV_PATH.write_text("SETUP_COMPLETE=TRUE\n")
    cookie_value = app_module.signer.dumps("plex-token")
    client.cookies.set(app_module.COOKIE_SESSION, cookie_value)
    resp = client.get("/dashboard", follow_redirects=False)
    # Not redirected to login (404 because no such route/file, but auth passed)
    assert resp.status_code != 302 or resp.headers.get("location") != "/auth/login"


def test_middleware_rejects_tampered_session_cookie(client, app_module):
    app_module.CONFIG_ENV_PATH.write_text("SETUP_COMPLETE=TRUE\n")
    client.cookies.set(app_module.COOKIE_SESSION, "tampered.invalid.signature")
    resp = client.get("/dashboard", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/auth/login"


def test_middleware_authenticates_with_valid_api_key_query_param(client, app_module):
    app_module.CONFIG_ENV_PATH.write_text("SETUP_COMPLETE=TRUE\n")
    resp = client.get("/dashboard?api_key=test-api-key", follow_redirects=False)
    assert resp.status_code != 302 or resp.headers.get("location") != "/auth/login"


def test_middleware_authenticates_with_bearer_token(client, app_module):
    app_module.CONFIG_ENV_PATH.write_text("SETUP_COMPLETE=TRUE\n")
    resp = client.get("/dashboard", headers={"Authorization": "Bearer test-api-key"}, follow_redirects=False)
    assert resp.status_code != 302 or resp.headers.get("location") != "/auth/login"


def test_middleware_rejects_invalid_api_key(client, app_module):
    app_module.CONFIG_ENV_PATH.write_text("SETUP_COMPLETE=TRUE\n")
    resp = client.get("/dashboard?api_key=wrong-key", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/auth/login"


# ── helper to authenticate a client ───────────────────────────────────────────

def _authenticate(client, app_module):
    app_module.CONFIG_ENV_PATH.write_text("SETUP_COMPLETE=TRUE\n")
    cookie_value = app_module.signer.dumps("plex-token")
    client.cookies.set(app_module.COOKIE_SESSION, cookie_value)


# ── settings: user mappings API ───────────────────────────────────────────────

def test_settings_data_returns_mappings_and_plex_names(client, app_module):
    _authenticate(client, app_module)
    app_module._write_user_mappings([{"name": "alice", "plex_names": ["alice_plex"]}])
    resp = client.get("/api/settings/data")
    assert resp.status_code == 200
    data = resp.json()
    assert data["user_mappings"][0]["name"] == "alice"
    assert data["seerr_users"] == []  # SEERR not configured


def test_settings_save_users_rejects_non_list(client, app_module):
    _authenticate(client, app_module)
    resp = client.post("/api/settings/users", json={"not": "a list"})
    assert resp.status_code == 400


def test_settings_save_users_rejects_too_many(client, app_module):
    _authenticate(client, app_module)
    resp = client.post("/api/settings/users", json=[{"name": f"u{i}"} for i in range(501)])
    assert resp.status_code == 400


def test_settings_save_users_rejects_non_dict_entries(client, app_module):
    _authenticate(client, app_module)
    resp = client.post("/api/settings/users", json=["not-a-dict"])
    assert resp.status_code == 400


def test_settings_save_users_persists(client, app_module):
    _authenticate(client, app_module)
    resp = client.post("/api/settings/users", json=[{"name": "bob", "plex_names": ["bob_plex"]}])
    assert resp.status_code == 200
    assert app_module._read_user_mappings() == [{"name": "bob", "plex_names": ["bob_plex"]}]


# ── pages ─────────────────────────────────────────────────────────────────────

def test_settings_page_returns_html(client, app_module):
    _authenticate(client, app_module)
    resp = client.get("/settings")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_setup_page_redirects_to_root_when_already_complete(client, app_module):
    app_module.CONFIG_ENV_PATH.write_text("SETUP_COMPLETE=TRUE\n")
    cookie_value = app_module.signer.dumps("plex-token")
    client.cookies.set(app_module.COOKIE_SESSION, cookie_value)
    resp = client.get("/setup", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/"


def test_setup_page_returns_html_when_not_complete(client):
    resp = client.get("/setup")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


# ── services / jobs / schedule / config ───────────────────────────────────────

def test_settings_get_services_returns_config_values(client, app_module):
    _authenticate(client, app_module)
    app_module._write_config_env({"SONARR_URL": "http://sonarr.local", "TMDB_API_KEY": "abc"})
    resp = client.get("/api/settings/services")
    data = resp.json()
    assert data["SONARR_URL"] == "http://sonarr.local"
    assert data["TMDB_API_KEY"] == "abc"
    assert data["VERIFY_SSL"] == "true"


def test_load_job_runs_missing_and_corrupt(app_module):
    assert app_module._load_job_runs() == {}
    (app_module.DATA_DIR / "job_runs.json").write_text("not json")
    assert app_module._load_job_runs() == {}


def test_settings_get_jobs_lists_pipeline_jobs(client, app_module):
    _authenticate(client, app_module)
    (app_module.DATA_DIR / "job_runs.json").write_text(json.dumps({"sonarr": "2024-01-01T00:00:00Z"}))
    resp = client.get("/api/settings/jobs")
    jobs = resp.json()
    by_id = {j["id"]: j for j in jobs}
    assert by_id["sonarr"]["lastRun"] == "2024-01-01T00:00:00Z"
    assert by_id["sonarr"]["running"] is False
    assert by_id["radarr"]["lastRun"] is None


def test_settings_run_job_rejects_unknown_job(client, app_module):
    _authenticate(client, app_module)
    resp = client.post("/api/settings/jobs/bogus/run")
    assert resp.status_code == 404


def test_settings_run_job_rejects_when_already_running(client, app_module):
    _authenticate(client, app_module)
    app_module._running_jobs["sonarr"] = True
    try:
        resp = client.post("/api/settings/jobs/sonarr/run")
        assert resp.status_code == 409
    finally:
        app_module._running_jobs.pop("sonarr", None)


def test_settings_get_schedule_returns_default_cron(client, app_module):
    _authenticate(client, app_module)
    resp = client.get("/api/settings/schedule")
    assert resp.json()["cron"] == app_module.DEFAULT_CRON


def test_settings_save_schedule_validates_cron_expression(client, app_module):
    _authenticate(client, app_module)
    resp = client.post("/api/settings/schedule", json={"cron": "not a cron"})
    assert resp.status_code == 400
    assert "Invalid cron expression" in resp.json()["error"]


def test_settings_save_schedule_persists_valid_cron(client, app_module):
    _authenticate(client, app_module)
    resp = client.post("/api/settings/schedule", json={"cron": "0 0 * * *"})
    assert resp.status_code == 200
    assert app_module._read_config_env()["CRON_SCHEDULE"] == "0 0 * * *"


def test_settings_save_schedule_empty_resets_to_default(client, app_module):
    _authenticate(client, app_module)
    resp = client.post("/api/settings/schedule", json={"cron": "  "})
    assert resp.status_code == 200
    assert app_module._read_config_env()["CRON_SCHEDULE"] == app_module.DEFAULT_CRON


def test_settings_save_config_filters_disallowed_keys(client, app_module):
    _authenticate(client, app_module)
    resp = client.post("/api/settings/config", json={"SONARR_URL": "http://x.local", "EVIL_KEY": "nope"})
    assert resp.status_code == 200
    saved = app_module._read_config_env()
    assert saved["SONARR_URL"] == "http://x.local"
    assert "EVIL_KEY" not in saved


def test_settings_save_config_rejects_invalid_url(client, app_module):
    _authenticate(client, app_module)
    resp = client.post("/api/settings/config", json={"PLEX_URL": "not-a-url"})
    assert resp.status_code == 400


# ── tautulli webhook ──────────────────────────────────────────────────────────

@pytest.fixture
def webhook_db(app_module):
    import asyncio
    asyncio.run(app_module._init_plays_db())


def test_webhook_rejects_invalid_json(client, webhook_db):
    resp = client.post("/api/tautulli/webhook", data=b"not-json", headers={"Content-Type": "application/json"})
    assert resp.status_code == 400


def test_webhook_stores_play_record(client, app_module, webhook_db):
    payload = {
        "event": "play",
        "event_at": 1700000000,
        "media": {"rating_key": "123", "tmdb_id": "555", "media_type": "movie"},
        "playback": {"transcode_decision": "Direct Play"},
        "source_quality": {"video_codec": "h264", "video_resolution": "1080p"},
        "stream_quality": {"stream_video_codec": "h264"},
        "client": {"user": "alice", "friendly_name": "Alice", "platform": "Roku"},
    }
    resp = client.post("/api/tautulli/webhook", json=payload)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    import asyncio, aiosqlite

    async def _fetch():
        async with aiosqlite.connect(app_module.PLAYS_DB) as db:
            cur = await db.execute("SELECT tmdb_id, client_friendly_name FROM plays")
            return await cur.fetchall()

    rows = asyncio.run(_fetch())
    assert rows == [(555, "Alice")]


def test_webhook_handles_invalid_tmdb_id(client, app_module, webhook_db):
    payload = {"event": "play", "media": {"rating_key": "1", "tmdb_id": "not-a-number"}}
    resp = client.post("/api/tautulli/webhook", json=payload)
    assert resp.status_code == 200

    import asyncio, aiosqlite

    async def _fetch():
        async with aiosqlite.connect(app_module.PLAYS_DB) as db:
            cur = await db.execute("SELECT tmdb_id FROM plays")
            return await cur.fetchall()

    rows = asyncio.run(_fetch())
    assert rows == [(None,)]


def test_plex_hdr_type_returns_none_when_unconfigured(app_module):
    import asyncio
    assert asyncio.run(app_module._plex_hdr_type("123")) is None
    assert asyncio.run(app_module._plex_hdr_type("")) is None


# ── static file serving & security headers ───────────────────────────────────

def test_serve_static_returns_index_html(client, app_module):
    _authenticate(client, app_module)
    (app_module.PUBLIC_DIR / "index.html").write_text("<h1>Home</h1>")
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Home" in resp.text


def test_serve_static_404_for_missing_file(client, app_module):
    _authenticate(client, app_module)
    resp = client.get("/does-not-exist.html")
    assert resp.status_code == 404


def test_serve_static_blocks_path_traversal(client, app_module):
    _authenticate(client, app_module)
    (app_module.PUBLIC_DIR / "index.html").write_text("home")
    resp = client.get("/../../etc/passwd")
    assert resp.status_code in (404, 200)
    # Should never escape the public dir — confirm passwd contents are not served
    assert "root:" not in resp.text


def test_security_headers_present(client, app_module):
    _authenticate(client, app_module)
    (app_module.PUBLIC_DIR / "index.html").write_text("<h1>Home</h1>")
    resp = client.get("/")
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"


# ── _fetch_seerr_users / _fetch_plex_users / _get_known_plex_names ───────────

def test_fetch_seerr_users_returns_empty_when_unconfigured(app_module):
    import asyncio
    assert asyncio.run(app_module._fetch_seerr_users()) == []


def test_fetch_plex_users_returns_empty_when_no_token(app_module):
    import asyncio
    assert asyncio.run(app_module._fetch_plex_users()) == []


def test_get_known_plex_names_combines_mappings_and_watch_data(app_module):
    import asyncio
    app_module._write_user_mappings([{"name": "alice", "plex_names": ["alice_plex"]}])
    (app_module.DATA_DIR / "tv.json").write_text(json.dumps([{"watch_data": {"bob_plex": {}}}]))
    (app_module.DATA_DIR / "movies.json").write_text(json.dumps([{"watch_data": {"carl_plex": {}}}]))
    names = asyncio.run(app_module._get_known_plex_names())
    assert names == ["alice_plex", "bob_plex", "carl_plex"]


def test_schedule_pipeline_registers_job(app_module):
    app_module._schedule_pipeline("0 0 * * *")
    job = app_module._scheduler.get_job("pipeline")
    assert job is not None


def test_schedule_pipeline_handles_invalid_cron_gracefully(app_module, capsys):
    app_module._schedule_pipeline("not a valid cron")
    captured = capsys.readouterr()
    assert "invalid cron expression" in captured.out.lower()
