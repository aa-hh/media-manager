"""
Storage forecasting.

Maintains daily snapshots in data/snapshots.json.
Calculates growth rate and projects full date.
"""
import json
from datetime import datetime, date, timedelta
from pathlib import Path


CAPACITY_GB = float("inf")  # Set via env or config; infinity = unknown


def record_snapshot(data_dir: Path, shows: list[dict], movies: list[dict]) -> None:
    snapshots_file = data_dir / "snapshots.json"
    try:
        snapshots = json.loads(snapshots_file.read_text())
    except Exception:
        snapshots = []

    today = date.today().isoformat()
    tv_gb = sum(s["size_gb"] for s in shows)
    movie_gb = sum(m["size_gb"] for m in movies)
    total_gb = round(tv_gb + movie_gb, 2)

    # Replace today's snapshot if it exists, otherwise append
    updated = [s for s in snapshots if s["date"] != today]
    updated.append({
        "date": today,
        "tv_gb": round(tv_gb, 2),
        "movie_gb": round(movie_gb, 2),
        "total_gb": total_gb,
    })
    updated.sort(key=lambda x: x["date"])

    # Keep last 730 days (2 years)
    updated = updated[-730:]
    snapshots_file.write_text(json.dumps(updated, indent=2))


def calculate(data_dir: Path, capacity_gb: float | None = None) -> dict:
    snapshots_file = data_dir / "snapshots.json"
    try:
        snapshots = json.loads(snapshots_file.read_text())
    except Exception:
        return {"snapshots": [], "growth_gb_per_month": None, "predicted_full_date": None}

    if len(snapshots) < 2:
        return {
            "snapshots": snapshots,
            "growth_gb_per_month": None,
            "predicted_full_date": None,
        }

    # Use linear regression over available snapshots to find growth rate
    # Simple approach: compare last 30-day window vs prior 30-day window
    recent = [s for s in snapshots[-30:]]
    oldest = recent[0]["total_gb"]
    newest = recent[-1]["total_gb"]
    days_span = max(1, (
        datetime.fromisoformat(recent[-1]["date"]) -
        datetime.fromisoformat(recent[0]["date"])
    ).days)

    daily_growth = (newest - oldest) / days_span
    monthly_growth = round(daily_growth * 30.44, 2)

    predicted_full = None
    if capacity_gb and daily_growth > 0:
        remaining = capacity_gb - newest
        days_to_full = remaining / daily_growth
        predicted_full = (date.today() + timedelta(days=days_to_full)).isoformat()

    return {
        "snapshots": snapshots,
        "growth_gb_per_month": monthly_growth,
        "predicted_full_date": predicted_full,
        "current_total_gb": newest,
        "capacity_gb": capacity_gb,
    }
