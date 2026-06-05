"""
Loads configuration from config/.env inside the project root.
Env vars already in the environment take precedence over the file.
Also checks HOMEPAGE_VAR_* fallback names for backwards compatibility
with existing installs that source config from Homepage dashboard.
"""
import os
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent.parent


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def load() -> None:
    _load_env_file(_PROJECT_ROOT / "config" / ".env")


def get(name: str, default: str = "") -> str:
    """Get env var. For API key vars, also checks HOMEPAGE_VAR_ prefixed name."""
    val = os.getenv(name, "")
    if val:
        return val
    # Backwards compat: Homepage dashboard used HOMEPAGE_VAR_ prefix
    if not name.startswith("HOMEPAGE_VAR_"):
        fallback = f"HOMEPAGE_VAR_{name}"
        val = os.getenv(fallback, "")
        if val:
            return val
    return default


def require(name: str) -> str:
    value = get(name)
    if not value:
        raise RuntimeError(f"Required environment variable '{name}' is not set")
    return value


def verify_ssl() -> bool:
    """Whether to verify SSL certs for internal service connections. Default True."""
    return get("VERIFY_SSL", "true").lower() not in ("false", "0", "no")
