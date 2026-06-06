from lib.collectors import services


# ── _parse_github_body ────────────────────────────────────────────────────────

def test_parse_github_body_buckets_new_and_fixed():
    body = (
        "# New Features\n"
        "- Added dark mode ([#1](http://x))\n"
        "- *Author* Improved search experience\n"
        "# Bug Fixes\n"
        "- Fixed crash on startup\n"
        "- abcd\n"
    )
    result = services._parse_github_body(body)
    assert "Added dark mode (#1)" in result["new"]
    assert "Improved search experience" in result["new"]
    assert "Fixed crash on startup" in result["fixed"]
    assert "abcd" not in result["fixed"]


def test_parse_github_body_handles_empty():
    assert services._parse_github_body("") == {"new": [], "fixed": []}


def test_parse_github_body_caps_at_six_items():
    body = "# New\n" + "\n".join(f"- item number {i} here" for i in range(10))
    result = services._parse_github_body(body)
    assert len(result["new"]) == 6


# ── _get / _get_external ──────────────────────────────────────────────────────

def test_get_returns_json_on_success(mocker):
    class _R:
        def raise_for_status(self): pass
        def json(self): return {"ok": True}
    mocker.patch.object(services.requests, "get", return_value=_R())
    assert services._get("http://x") == {"ok": True}


def test_get_returns_none_on_exception(mocker):
    mocker.patch.object(services.requests, "get", side_effect=RuntimeError("boom"))
    assert services._get("http://x") is None


def test_get_external_returns_none_on_http_error(mocker):
    class _R:
        def raise_for_status(self): raise RuntimeError("502")
        def json(self): return {}
    mocker.patch.object(services.requests, "get", return_value=_R())
    assert services._get_external("http://x") is None


# ── _github_latest ────────────────────────────────────────────────────────────

def test_github_latest_returns_none_when_no_data(mocker):
    mocker.patch.object(services, "_get_external", return_value=None)
    version, changes = services._github_latest("org/repo")
    assert version is None and changes == {}


def test_github_latest_strips_v_prefix(mocker):
    mocker.patch.object(services, "_get_external", return_value={"tag_name": "v1.2.3", "body": ""})
    version, changes = services._github_latest("org/repo")
    assert version == "1.2.3"


# ── _check_arr ────────────────────────────────────────────────────────────────

def test_check_arr_not_configured():
    result = services._check_arr("Sonarr", "", "")
    assert result == {"name": "Sonarr", "url": "", "reachable": False, "not_configured": True}


def test_check_arr_unreachable(mocker):
    mocker.patch.object(services, "_get", return_value=None)
    result = services._check_arr("Sonarr", "http://sonarr.local", "key")
    assert result == {"name": "Sonarr", "url": "http://sonarr.local", "reachable": False}


def test_check_arr_reports_update_available(mocker):
    mocker.patch.object(services, "_get", side_effect=[
        {"version": "1.0.0"},
        [{"latest": True, "version": "1.1.0", "installable": True, "installed": False, "changes": {"new": ["x"]}}],
    ])
    result = services._check_arr("Sonarr", "http://sonarr.local", "key")
    assert result["reachable"] is True
    assert result["current_version"] == "1.0.0"
    assert result["latest_version"] == "1.1.0"
    assert result["update_available"] is True
    assert result["installable"] is True
    assert "trigger_cmd" in result


def test_check_arr_no_update_entry_uses_current_as_latest(mocker):
    mocker.patch.object(services, "_get", side_effect=[{"version": "1.0.0"}, []])
    result = services._check_arr("Radarr", "http://radarr.local", "key")
    assert result["latest_version"] == "1.0.0"
    assert result["update_available"] is False


# ── _check_sonarr_instances / _check_radarr_instances ────────────────────────

def test_check_sonarr_instances_not_configured(mocker):
    mocker.patch.object(services, "cfg", return_value="")
    result = services._check_sonarr_instances()
    assert result == [{"name": "Sonarr", "url": "", "reachable": False, "not_configured": True}]


def test_check_sonarr_instances_multiple_urls(mocker):
    def fake_cfg(name, default=""):
        return {"SONARR_URL": "http://a.local, http://b.local", "SONARR_API_KEY": "key1"}.get(name, default)
    mocker.patch.object(services, "cfg", side_effect=fake_cfg)
    check_mock = mocker.patch.object(services, "_check_arr", side_effect=lambda n, u, k: {"name": n, "url": u, "key": k})
    results = services._check_sonarr_instances()
    assert [r["name"] for r in results] == ["Sonarr (1)", "Sonarr (2)"]
    assert results[0]["key"] == "key1"
    assert results[1]["key"] == ""


def test_check_radarr_instances_single_url(mocker):
    def fake_cfg(name, default=""):
        return {"RADARR_URL": "http://radarr.local", "RADARR_API_KEY": "key1"}.get(name, default)
    mocker.patch.object(services, "cfg", side_effect=fake_cfg)
    mocker.patch.object(services, "_check_arr", side_effect=lambda n, u, k: {"name": n, "url": u, "key": k})
    results = services._check_radarr_instances()
    assert results[0]["name"] == "Radarr"


# ── _check_overseerr ──────────────────────────────────────────────────────────

def test_check_overseerr_not_configured(mocker):
    mocker.patch.object(services, "cfg", return_value="")
    result = services._check_overseerr()
    assert result["not_configured"] is True


def test_check_overseerr_unreachable(mocker):
    def fake_cfg(name, default=""):
        return {"SEERR_URL": "http://seerr.local", "SEERR_API_KEY": "key"}.get(name, default)
    mocker.patch.object(services, "cfg", side_effect=fake_cfg)
    mocker.patch.object(services, "_get", return_value=None)
    result = services._check_overseerr()
    assert result["reachable"] is False
    assert "not_configured" not in result


def test_check_overseerr_detects_update_via_version_compare(mocker):
    def fake_cfg(name, default=""):
        return {"SEERR_URL": "http://seerr.local", "SEERR_API_KEY": "key"}.get(name, default)
    mocker.patch.object(services, "cfg", side_effect=fake_cfg)
    mocker.patch.object(services, "_get", return_value={
        "version": "1.0.0", "updateAvailable": False, "commitsBehind": 2, "commitTag": "abc",
    })
    mocker.patch.object(services, "_github_latest", return_value=("1.2.0", {"new": ["feature"]}))
    result = services._check_overseerr()
    assert result["update_available"] is True
    assert result["latest_version"] == "1.2.0"
    assert result["changes"] == {"new": ["feature"]}


# ── _check_tautulli ───────────────────────────────────────────────────────────

def test_check_tautulli_not_configured(mocker):
    mocker.patch.object(services, "cfg", return_value="")
    assert services._check_tautulli()["not_configured"] is True


def test_check_tautulli_unreachable_on_bad_response(mocker):
    def fake_cfg(name, default=""):
        return {"TAUTULLI_URL": "http://t.local", "TAUTULLI_API_KEY": "key"}.get(name, default)
    mocker.patch.object(services, "cfg", side_effect=fake_cfg)
    mocker.patch.object(services, "_get", return_value={"response": {"result": "error"}})
    result = services._check_tautulli()
    assert result["reachable"] is False
    assert "not_configured" not in result


def test_check_tautulli_reports_version_and_update(mocker):
    def fake_cfg(name, default=""):
        return {"TAUTULLI_URL": "http://t.local", "TAUTULLI_API_KEY": "key"}.get(name, default)
    mocker.patch.object(services, "cfg", side_effect=fake_cfg)
    mocker.patch.object(services, "_get", return_value={
        "response": {"result": "success", "data": {"tautulli_version": "v2.0.0", "tautulli_install_type": "git"}}
    })
    mocker.patch.object(services, "_github_latest", return_value=("2.1.0", {}))
    result = services._check_tautulli()
    assert result["current_version"] == "2.0.0"
    assert result["latest_version"] == "2.1.0"
    assert result["update_available"] is True
    assert result["install_type"] == "git"


# ── _check_plex ───────────────────────────────────────────────────────────────

def test_check_plex_not_configured(mocker):
    mocker.patch.object(services, "cfg", return_value="")
    assert services._check_plex()["not_configured"] is True


def test_check_plex_reachable(mocker):
    def fake_cfg(name, default=""):
        return {"PLEX_TOKEN": "tok", "PLEX_URL": "http://plex.local"}.get(name, default)
    mocker.patch.object(services, "cfg", side_effect=fake_cfg)
    mocker.patch.object(services, "_get", return_value={"MediaContainer": {"version": "1.30.0"}})
    result = services._check_plex()
    assert result["reachable"] is True
    assert result["current_version"] == "1.30.0"
    assert result["update_available"] is None


# ── _check_tmdb ───────────────────────────────────────────────────────────────

def test_check_tmdb_not_configured(mocker):
    mocker.patch.object(services, "cfg", return_value="")
    assert services._check_tmdb()["not_configured"] is True


def test_check_tmdb_reachable(mocker):
    mocker.patch.object(services, "cfg", return_value="key")
    mocker.patch.object(services, "_get_external", return_value={"images": {}})
    result = services._check_tmdb()
    assert result["reachable"] is True


def test_check_tmdb_unreachable(mocker):
    mocker.patch.object(services, "cfg", return_value="key")
    mocker.patch.object(services, "_get_external", return_value=None)
    result = services._check_tmdb()
    assert result["reachable"] is False


# ── collect ───────────────────────────────────────────────────────────────────

def test_collect_aggregates_services(mocker):
    mocker.patch.object(services, "_check_sonarr_instances", return_value=[{"name": "Sonarr", "reachable": True}])
    mocker.patch.object(services, "_check_radarr_instances", return_value=[{"name": "Radarr", "reachable": False, "not_configured": True}])
    mocker.patch.object(services, "_check_overseerr", return_value={"name": "Overseerr", "reachable": True, "update_available": True})
    mocker.patch.object(services, "_check_tautulli", return_value={"name": "Tautulli", "reachable": True})
    mocker.patch.object(services, "_check_plex", return_value={"name": "Plex", "reachable": True})
    mocker.patch.object(services, "_check_tmdb", return_value={"name": "Tmdb", "reachable": True})

    result = services.collect()
    names = [s["name"] for s in result["services"]]
    assert names == ["Sonarr", "Radarr", "Overseerr", "Tautulli", "Plex", "Tmdb"]
    assert "checked_at" in result


def test_collect_handles_check_exception_gracefully(mocker):
    """A failing health-check must be logged and replaced with a 'reachable: False'
    placeholder rather than crashing collect() (regression test for log.warn vs
    the non-existent log.warning)."""
    mocker.patch.object(services, "_check_sonarr_instances", return_value=[])
    mocker.patch.object(services, "_check_radarr_instances", return_value=[])
    def _check_overseerr():
        raise RuntimeError("boom")
    mocker.patch.object(services, "_check_overseerr", _check_overseerr)
    mocker.patch.object(services, "_check_tautulli", return_value={"name": "Tautulli", "reachable": True})
    mocker.patch.object(services, "_check_plex", return_value={"name": "Plex", "reachable": True})
    mocker.patch.object(services, "_check_tmdb", return_value={"name": "Tmdb", "reachable": True})
    warn_mock = mocker.patch.object(services.log, "warn")

    result = services.collect()

    by_name = {s["name"]: s for s in result["services"]}
    assert by_name["Overseerr"]["reachable"] is False
    warn_mock.assert_called_once()
    assert "Service check failed for _check_overseerr" in warn_mock.call_args[0][0]
