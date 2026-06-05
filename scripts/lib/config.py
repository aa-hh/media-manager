"""
Loads configuration from ~/homepage/config/homepage.env with optional
overrides from ~/media-manager/.env.
"""
import os
from pathlib import Path


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
    _load_env_file(Path.home() / "homepage" / "config" / "homepage.env")
    _load_env_file(Path.home() / "media-manager" / ".env")


def require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Required environment variable '{name}' is not set")
    return value


def get(name: str, default: str = "") -> str:
    return os.getenv(name, default)
