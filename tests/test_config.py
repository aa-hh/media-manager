import pytest

from lib import config


# ── get ───────────────────────────────────────────────────────────────────────

def test_get_returns_value_when_present(monkeypatch):
    monkeypatch.setenv("MY_VAR", "hello")
    assert config.get("MY_VAR") == "hello"


def test_get_returns_default_when_missing(monkeypatch):
    monkeypatch.delenv("MISSING_VAR", raising=False)
    monkeypatch.delenv("HOMEPAGE_VAR_MISSING_VAR", raising=False)
    assert config.get("MISSING_VAR", "fallback") == "fallback"


def test_get_returns_empty_string_default():
    assert config.get("DEFINITELY_NOT_SET_XYZ") == ""


def test_get_falls_back_to_homepage_var_prefix(monkeypatch):
    monkeypatch.delenv("PLEX_TOKEN", raising=False)
    monkeypatch.setenv("HOMEPAGE_VAR_PLEX_TOKEN", "abc123")
    assert config.get("PLEX_TOKEN") == "abc123"


def test_get_direct_value_wins_over_homepage_prefix(monkeypatch):
    monkeypatch.setenv("PLEX_TOKEN", "direct-value")
    monkeypatch.setenv("HOMEPAGE_VAR_PLEX_TOKEN", "fallback-value")
    assert config.get("PLEX_TOKEN") == "direct-value"


def test_get_does_not_apply_prefix_fallback_for_already_prefixed_names(monkeypatch):
    monkeypatch.delenv("HOMEPAGE_VAR_FOO", raising=False)
    monkeypatch.delenv("HOMEPAGE_VAR_HOMEPAGE_VAR_FOO", raising=False)
    monkeypatch.setenv("HOMEPAGE_VAR_HOMEPAGE_VAR_FOO", "double-prefixed")
    # Asking for "HOMEPAGE_VAR_FOO" should NOT look up "HOMEPAGE_VAR_HOMEPAGE_VAR_FOO"
    assert config.get("HOMEPAGE_VAR_FOO", "default") == "default"


# ── require ───────────────────────────────────────────────────────────────────

def test_require_returns_value_when_present(monkeypatch):
    monkeypatch.setenv("REQUIRED_VAR", "value")
    assert config.require("REQUIRED_VAR") == "value"


def test_require_raises_when_missing(monkeypatch):
    monkeypatch.delenv("REQUIRED_MISSING", raising=False)
    monkeypatch.delenv("HOMEPAGE_VAR_REQUIRED_MISSING", raising=False)
    with pytest.raises(RuntimeError, match="REQUIRED_MISSING"):
        config.require("REQUIRED_MISSING")


# ── verify_ssl ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [
    ("true", True),
    ("True", True),
    ("TRUE", True),
    ("false", False),
    ("False", False),
    ("0", False),
    ("no", False),
    ("1", True),
    ("yes", True),
])
def test_verify_ssl_parses_values(monkeypatch, value, expected):
    monkeypatch.setenv("VERIFY_SSL", value)
    assert config.verify_ssl() is expected


def test_verify_ssl_defaults_to_true_when_unset(monkeypatch):
    monkeypatch.delenv("VERIFY_SSL", raising=False)
    monkeypatch.delenv("HOMEPAGE_VAR_VERIFY_SSL", raising=False)
    assert config.verify_ssl() is True


# ── _load_env_file ────────────────────────────────────────────────────────────

def test_load_env_file_sets_new_vars(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("FOO=bar\nBAZ=qux\n")
    monkeypatch.delenv("FOO", raising=False)
    monkeypatch.delenv("BAZ", raising=False)
    config._load_env_file(env_file)
    assert __import__("os").environ["FOO"] == "bar"
    assert __import__("os").environ["BAZ"] == "qux"


def test_load_env_file_skips_comments_and_blank_lines(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("# a comment\n\nGOOD=value\n   # indented comment\n")
    monkeypatch.delenv("GOOD", raising=False)
    config._load_env_file(env_file)
    assert __import__("os").environ["GOOD"] == "value"


def test_load_env_file_does_not_override_existing_env(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("EXISTING=from_file\n")
    monkeypatch.setenv("EXISTING", "from_environment")
    config._load_env_file(env_file)
    assert __import__("os").environ["EXISTING"] == "from_environment"


def test_load_env_file_strips_quotes(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text('QUOTED="quoted value"\nSINGLE=\'single value\'\n')
    monkeypatch.delenv("QUOTED", raising=False)
    monkeypatch.delenv("SINGLE", raising=False)
    config._load_env_file(env_file)
    import os
    assert os.environ["QUOTED"] == "quoted value"
    assert os.environ["SINGLE"] == "single value"


def test_load_env_file_ignores_lines_without_equals(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("NOT_A_VAR_LINE\nVALID=yes\n")
    monkeypatch.delenv("VALID", raising=False)
    config._load_env_file(env_file)
    import os
    assert os.environ["VALID"] == "yes"
    assert "NOT_A_VAR_LINE" not in os.environ


def test_load_env_file_missing_file_is_noop(tmp_path):
    missing = tmp_path / "does_not_exist.env"
    # Should not raise
    config._load_env_file(missing)
