import sys
from datetime import datetime
from pathlib import Path

_log_file = None


def init(log_dir: Path) -> None:
    global _log_file
    log_dir.mkdir(parents=True, exist_ok=True)
    _log_file = log_dir / "media_manager.log"


def _write(level: str, message: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level}] {message}"
    print(line, file=sys.stderr if level == "ERROR" else sys.stdout)
    if _log_file:
        with open(_log_file, "a") as f:
            f.write(line + "\n")


def info(msg: str) -> None:
    _write("INFO ", msg)


def warn(msg: str) -> None:
    _write("WARN ", msg)


def error(msg: str) -> None:
    _write("ERROR", msg)
