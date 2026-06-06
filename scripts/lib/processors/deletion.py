"""
Deletion scoring engine.

Score 0-100: higher = stronger delete candidate.
Recommendation:
  >= 70  → strong_delete
  >= 45  → suggest_delete
  <  45  → keep
"""
from datetime import datetime, timezone


def _days_since(date_str: str | None) -> int | None:
    if not date_str:
        return None
    try:
        if isinstance(date_str, str):
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        else:
            dt = datetime.fromtimestamp(date_str, tz=timezone.utc)
        now = datetime.now(timezone.utc)
        return (now - dt).days
    except Exception:
        return None


def score(item: dict) -> dict:
    points = 0
    reasons = []

    size_gb = item.get("size_gb", 0)
    rating = item.get("rating") or 0
    total_plays = item.get("total_plays", 0)
    any_watched = item.get("any_watched", False)
    req = item.get("request", {})
    requested = req.get("requested", False)
    rws = item.get("requester_status", {})
    requester_watched = rws.get("watched", False)
    requester_completion = rws.get("completion_pct", 0)

    watch_data = item.get("watch_data", {})
    last_activity = None
    for wd in watch_data.values():
        lw = wd.get("last_watched")
        if lw and (last_activity is None or lw > last_activity):
            last_activity = lw

    days_inactive = _days_since(last_activity)
    days_since_request = _days_since(req.get("requested_at"))

    # Grace period: use the most recent of added_at / requested_at as the anchor
    added_at = item.get("added_at")
    days_since_added = _days_since(added_at)
    days_since_arrived = min(
        d for d in [days_since_added, days_since_request] if d is not None
    ) if any(d is not None for d in [days_since_added, days_since_request]) else None
    in_grace_period = days_since_arrived is not None and days_since_arrived < 30

    # Size contribution (up to 25 pts)
    if size_gb >= 500:
        points += 25
        reasons.append(f"Very large ({size_gb:.0f} GB)")
    elif size_gb >= 200:
        points += 18
        reasons.append(f"Large ({size_gb:.0f} GB)")
    elif size_gb >= 50:
        points += 12
        reasons.append(f"Large ({size_gb:.0f} GB)")
    elif size_gb >= 20:
        points += 8
    elif size_gb >= 5:
        points += 4

    # Never watched (up to 30 pts) — skipped during grace period
    if not in_grace_period:
        if total_plays == 0:
            points += 30
            reasons.append("Never watched by anyone")
        elif not any_watched:
            points += 20
            reasons.append("No user has watched 25%+")

        # Requester never watched (up to 20 pts)
        if requested and not requester_watched:
            points += 20
            reasons.append(f"Requester ({req.get('requester_name', 'unknown')}) never watched")
        elif requested and requester_completion < 25:
            points += 10
            reasons.append(f"Requester watched only {requester_completion:.0f}%")
    else:
        reasons.append(f"Recently added ({days_since_arrived}d ago — grace period)")

    # Inactivity: starting 3 months (90 days) after the last activity, the score climbs
    # by a per-week rate that resets on new activity. Watchlisted items climb slower
    # (0.25 pt/week) since someone still intends to watch them; others at 0.5 pt/week.
    # For never-watched items, use days_since_added as the inactivity proxy
    on_watchlist = item.get("on_watchlist", False)
    effective_inactive = days_inactive if days_inactive is not None else (days_since_added if total_plays == 0 else None)
    if effective_inactive is not None and effective_inactive > 90:
        weeks_over = (effective_inactive - 90) // 7
        rate = 0.25 if on_watchlist else 0.5
        inactivity_points = weeks_over * rate
        points += inactivity_points
        reasons.append(f"Unwatched for {effective_inactive} days (+{inactivity_points:.2f} inactivity pts)")

    # Low rating (up to 10 pts)
    if rating and rating < 5.0:
        points += 10
        reasons.append(f"Low TMDB rating ({rating:.1f})")
    elif rating and rating < 6.5:
        points += 4

    score_val = min(100, points)

    if score_val >= 70:
        recommendation = "strong_delete"
    elif score_val >= 45:
        recommendation = "suggest_delete"
    else:
        recommendation = "keep"

    return {
        "score": score_val,
        "recommendation": recommendation,
        "reasons": reasons,
    }


def score_season(season: dict, show: dict) -> dict:
    """
    Scores an individual season for deletion.
    Inherits show-level context (added_at, rating) but uses season watch data.
    """
    points = 0
    reasons = []

    size_gb = season.get("size_gb", 0)
    total_plays = season.get("total_plays", 0)
    any_watched = season.get("any_watched", False)
    watch_data = season.get("watch_data", {})

    last_activity = None
    for wd in watch_data.values():
        lw = wd.get("last_watched")
        if lw and (last_activity is None or lw > last_activity):
            last_activity = lw

    days_inactive = _days_since(last_activity)

    # Grace period from show
    added_at = show.get("added_at")
    req_at = show.get("request", {}).get("requested_at")
    days_since_added = _days_since(added_at)
    days_since_request = _days_since(req_at)
    days_since_arrived = min(
        d for d in [days_since_added, days_since_request] if d is not None
    ) if any(d is not None for d in [days_since_added, days_since_request]) else None
    in_grace_period = days_since_arrived is not None and days_since_arrived < 30

    # Size
    if size_gb >= 100:
        points += 25
        reasons.append(f"Large season ({size_gb:.0f} GB)")
    elif size_gb >= 40:
        points += 15
    elif size_gb >= 10:
        points += 6

    # Watch status
    if not in_grace_period:
        if total_plays == 0:
            points += 30
            reasons.append("Never watched")
        elif not any_watched:
            points += 20
            reasons.append("No user has watched 25%+ of season")
    else:
        reasons.append(f"Show recently added ({days_since_arrived}d ago — grace period)")

    # Inactivity — use show's added_at as proxy for never-watched seasons.
    # Starting 90 days after the last activity, +0.5 pt per additional week of inactivity;
    # resets whenever there's new activity.
    effective_inactive = days_inactive if days_inactive is not None else (days_since_arrived if total_plays == 0 else None)
    if effective_inactive is not None and effective_inactive > 90:
        weeks_over = (effective_inactive - 90) // 7
        inactivity_points = weeks_over * 0.5
        points += inactivity_points
        reasons.append(f"Unwatched for {effective_inactive} days (+{inactivity_points:.1f} inactivity pts)")

    # Low show rating
    rating = show.get("rating") or 0
    if rating and rating < 5.0:
        points += 10
    elif rating and rating < 6.5:
        points += 4

    score_val = min(100, points)
    if score_val >= 70:
        recommendation = "strong_delete"
    elif score_val >= 45:
        recommendation = "suggest_delete"
    else:
        recommendation = "keep"

    return {"score": score_val, "recommendation": recommendation, "reasons": reasons}


def apply(items: list[dict]) -> list[dict]:
    for item in items:
        item["deletion"] = score(item)
        if item.get("type") == "show":
            for season in item.get("seasons", []):
                if season.get("size_bytes", 0) > 0:
                    season["deletion"] = score_season(season, item)
    return items
