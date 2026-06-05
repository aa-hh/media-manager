"""
Static site generator: reads data/*.json, renders Jinja2 templates → public/.
"""
import json
import math
import shutil
from pathlib import Path
from datetime import datetime, timezone

import os
from jinja2 import Environment, FileSystemLoader, select_autoescape


def _days_since(date_str: str | None) -> int | None:
    if not date_str:
        return None
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).days
    except Exception:
        return None


def content_value_score(item: dict) -> float:
    """Breadth × rewatch: rewards content watched by multiple users and rewatched."""
    watch_data = item.get("watch_data", {})
    user_count = len(watch_data)
    total_plays = item.get("total_plays", 0)
    if user_count == 0 or total_plays == 0:
        return 0.0
    breadth = math.log(1 + user_count)
    if item.get("type") == "show":
        rewatch_ratio = total_plays / max(item.get("total_episodes", 1), 1)
    else:
        rewatch_ratio = total_plays / user_count
    return round(breadth * math.log(1 + rewatch_ratio), 4)


def content_waste_score(item: dict, watchlist_ids: set) -> float:
    """Large + old + unwatched + not on any watchlist = high waste score."""
    added_at = item.get("added_at") or item.get("request", {}).get("requested_at")
    days = _days_since(added_at)
    age_factor = min(1.0, (days or 0) / 90)

    tmdb_id = item.get("tmdb_id")
    media_type = "tv" if item.get("type") == "show" else "movie"
    on_watchlist = bool(tmdb_id and (media_type, tmdb_id) in watchlist_ids)
    watchlist_factor = 0.2 if on_watchlist else 1.0

    value = content_value_score(item)
    return round(item.get("size_gb", 0) * age_factor * (1 / (1 + value)) * watchlist_factor, 4)


def _make_env(templates_dir: Path) -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html"]),
    )

    def gb(val):
        if val is None:
            return "—"
        return f"{val:,.1f} GB"

    def fmt_date(val):
        if not val:
            return "—"
        try:
            dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
            return dt.strftime("%b %d, %Y")
        except Exception:
            return str(val)

    def pct(val):
        if val is None:
            return "—"
        return f"{val:.0f}%"

    def requester_label(item):
        req = item.get("request", {})
        if req.get("requested"):
            return req.get("requester_name") or "Unknown"
        return None

    def requester_status_label(item):
        req = item.get("request", {})
        if not req.get("requested"):
            return None
        rws = item.get("requester_status", {})
        name = req.get("requester_name", "Requester")
        if rws.get("completed"):
            return ("completed", f"{name} completed")
        elif rws.get("watched"):
            c = rws.get("completion_pct", 0)
            return ("watched", f"{name} watched {c:.0f}%")
        else:
            return ("never", f"{name} never watched")

    def file_format_tags(item):
        """Returns list of (label, css_class) tuples for file format badges."""
        fi = item.get("file_info") or {}
        tags = []
        codec = fi.get("video_codec") or ""
        res   = fi.get("resolution") or ""
        hdr   = fi.get("hdr", False)
        depth = fi.get("bit_depth")
        if codec:
            cls = "codec-h265" if codec in ("H.265", "AV1") else "codec-h264" if codec == "H.264" else "codec-other"
            tags.append((codec, cls))
        if res:
            tags.append((res, "res-4k" if "4K" in res else "res-hd"))
        if hdr:
            tags.append(("HDR", "tag-hdr"))
        if depth and int(depth) >= 10:
            tags.append(("10-bit", "tag-bit"))
        # TV shows: use quality profile name
        qp = item.get("quality_profile_name") or ""
        if not tags and qp:
            tags.append((qp, "codec-other"))
        return tags

    def transcode_label(item):
        """Returns (css_class, label) or None if no transcode data."""
        ts = item.get("transcode_stats")
        if not ts or not ts.get("total"):
            return None
        total = ts["total"]
        direct_pct = round(ts.get("direct", 0) / total * 100)
        transcode_pct = round(ts.get("transcode", 0) / total * 100)
        if direct_pct >= 90:
            return ("transcode-direct", f"Direct Play")
        if transcode_pct >= 50:
            return ("transcode-bad", f"Transcodes {transcode_pct}%")
        return ("transcode-mixed", f"{direct_pct}% Direct")

    def deletion_badge(item):
        d = item.get("deletion", {})
        rec = d.get("recommendation", "keep")
        labels = {
            "strong_delete": ("danger", "Strong Delete"),
            "suggest_delete": ("warning", "Consider Delete"),
            "keep": ("safe", "Keep"),
        }
        return labels.get(rec, ("safe", "Keep"))

    def top_genres(items, n=8):
        from collections import Counter
        c = Counter()
        for item in items:
            for g in item.get("genres", []):
                c[g] += 1
        return [g for g, _ in c.most_common(n)]

    env.filters["gb"] = gb
    env.filters["fmt_date"] = fmt_date
    env.filters["pct"] = pct
    env.globals["requester_label"] = requester_label
    env.globals["requester_status_label"] = requester_status_label
    env.globals["deletion_badge"] = deletion_badge
    env.globals["top_genres"] = top_genres
    env.globals["file_format_tags"] = file_format_tags
    env.globals["transcode_label"] = transcode_label
    _now = datetime.now()
    env.globals["now"] = _now.strftime("%Y-%m-%d %H:%M")
    env.globals["now_ts"] = int(_now.timestamp())
    return env


def _color_for_rec(rec: str) -> str:
    if rec == "strong_delete":
        return "#e05252"
    elif rec == "suggest_delete":
        return "#e0b252"
    return "#52a0e0"


def _chart_data_top_shows_by_size(shows: list[dict], n: int = 15) -> dict:
    sorted_shows = sorted(shows, key=lambda x: x.get("size_gb", 0), reverse=True)[:n]
    return {
        "labels": [s["title"][:30] for s in sorted_shows],
        "data": [round(s.get("size_gb", 0), 2) for s in sorted_shows],
        "colors": [_color_for_rec(s.get("deletion", {}).get("recommendation", "keep")) for s in sorted_shows],
    }


def _chart_data_top_movies_by_size(movies: list[dict], n: int = 15) -> dict:
    sorted_movies = sorted(movies, key=lambda x: x.get("size_gb", 0), reverse=True)[:n]
    return {
        "labels": [m["title"][:30] for m in sorted_movies],
        "data": [round(m.get("size_gb", 0), 2) for m in sorted_movies],
        "colors": [_color_for_rec(m.get("deletion", {}).get("recommendation", "keep")) for m in sorted_movies],
    }


def _chart_data_storage_by_deletion(all_items: list[dict]) -> dict:
    buckets = {"strong_delete": 0.0, "suggest_delete": 0.0, "keep": 0.0}
    for item in all_items:
        rec = item.get("deletion", {}).get("recommendation", "keep")
        if rec not in buckets:
            rec = "keep"
        buckets[rec] += item.get("size_gb", 0)
    return {
        "labels": ["Strong Delete", "Suggest Delete", "Keep"],
        "data": [round(buckets["strong_delete"], 2), round(buckets["suggest_delete"], 2), round(buckets["keep"], 2)],
        "colors": ["#e05252", "#e0b252", "#52a0e0"],
    }


def _chart_data_storage_by_requester(items: list[dict]) -> dict:
    from collections import defaultdict
    req_gb: dict[str, float] = defaultdict(float)
    for item in items:
        req = item.get("request", {})
        name = req.get("requester_name") if req.get("requested") else "Library"
        req_gb[name] += item.get("size_gb", 0)
    sorted_req = sorted(req_gb.items(), key=lambda x: x[1], reverse=True)
    return {
        "labels": [r for r, _ in sorted_req],
        "data": [round(v, 1) for _, v in sorted_req],
    }


def _chart_data_growth(snapshots: list[dict]) -> dict:
    return {
        "labels": [s["date"] for s in snapshots],
        "tv": [s["tv_gb"] for s in snapshots],
        "movie": [s["movie_gb"] for s in snapshots],
        "total": [s["total_gb"] for s in snapshots],
    }


def _build_sparkline_points(snapshots):
    """Return normalized SVG polyline points string from snapshots."""
    if len(snapshots) < 2:
        return ""
    values = [s.get("total_gb", 0) for s in snapshots]
    min_v, max_v = min(values), max(values)
    span = max_v - min_v or 1
    w, h = 60, 20
    points = []
    for i, v in enumerate(values):
        x = round(i / (len(values) - 1) * w, 1)
        y = round(h - (v - min_v) / span * h, 1)
        points.append(f"{x},{y}")
    return " ".join(points)


def _build_dashboard_context(shows, movies, users, forecast, watchlist_ids=None):
    all_items = shows + movies
    total_tv_gb = sum(s["size_gb"] for s in shows)
    total_movie_gb = sum(m["size_gb"] for m in movies)
    total_gb = total_tv_gb + total_movie_gb

    delete_candidates = sorted(
        [i for i in all_items if i.get("deletion", {}).get("recommendation") in ("strong_delete", "suggest_delete")],
        key=lambda x: x["size_gb"], reverse=True
    )
    potential_recovery = sum(i["size_gb"] for i in delete_candidates)

    most_active_user = None
    if users:
        most_active_user = max(users, key=lambda u: u.get("total_plays", 0), default=None)

    most_requested_user = None
    if users:
        most_requested_user = max(users, key=lambda u: u.get("requests_made", 0), default=None)

    # Recommendation cards
    unwatched_requests = sorted(
        [i for i in all_items if i.get("request", {}).get("requested") and not i.get("any_watched")],
        key=lambda x: x["size_gb"], reverse=True
    )[:10]

    strong_deletes = sorted(
        [i for i in all_items if i.get("deletion", {}).get("recommendation") == "strong_delete"],
        key=lambda x: x["size_gb"], reverse=True
    )[:10]

    wl = watchlist_ids or set()

    most_valuable = sorted(
        [i for i in all_items if i.get("total_plays", 0) > 0],
        key=content_value_score,
        reverse=True,
    )[:10]

    least_valuable = sorted(
        all_items,
        key=lambda x: content_waste_score(x, wl),
        reverse=True,
    )[:10]

    return {
        "total_tv_gb": total_tv_gb,
        "total_movie_gb": total_movie_gb,
        "total_gb": total_gb,
        "potential_recovery_gb": round(potential_recovery, 1),
        "show_count": len(shows),
        "movie_count": len(movies),
        "most_active_user": most_active_user,
        "most_requested_user": most_requested_user,
        "forecast": forecast,
        "charts": {
            "top_shows": _chart_data_top_shows_by_size(shows),
            "top_movies": _chart_data_top_movies_by_size(movies),
            "deletion_buckets": _chart_data_storage_by_deletion(all_items),
            "requester": _chart_data_storage_by_requester(all_items),
            "growth": _chart_data_growth(forecast.get("snapshots", [])),
        },
        "treemap_data": [
            {
                "title": item["title"],
                "size_gb": round(item.get("size_gb", 0), 2),
                "type": item["type"],
                "slug": item.get("slug", ""),
                "deletion_score": round(item.get("deletion", {}).get("score", 0)),
                "total_plays": item.get("total_plays", 0),
            }
            for item in sorted(all_items, key=lambda x: x.get("size_gb", 0), reverse=True)
            if item.get("size_gb", 0) > 0
        ][:100],
        "sparkline_points": _build_sparkline_points(forecast.get("snapshots", [])),
        "unwatched_requests": unwatched_requests,
        "strong_deletes": strong_deletes,
        "most_valuable": most_valuable,
        "least_valuable": least_valuable,
        "shows": shows,
        "movies": movies,
        "users": users,
    }


def render_api(
    shows: list[dict],
    movies: list[dict],
    users: list[dict],
    forecast: dict,
    services: dict,
    requests_data: list[dict],
    public_dir: Path,
) -> None:
    """Write small, targeted JSON files to public/api/ for homepage widgets."""
    api_dir = public_dir / "api"
    api_dir.mkdir(parents=True, exist_ok=True)

    all_items = shows + movies
    tv_gb = round(sum(s.get("size_gb", 0) for s in shows), 2)
    movie_gb = round(sum(m.get("size_gb", 0) for m in movies), 2)
    total_gb = round(tv_gb + movie_gb, 2)

    capacity_gb = forecast.get("capacity_gb")
    used_pct = round(total_gb / capacity_gb * 100) if capacity_gb else None

    strong_delete_items = [i for i in all_items if i.get("deletion", {}).get("recommendation") == "strong_delete"]
    recoverable_gb = round(sum(i.get("size_gb", 0) for i in strong_delete_items), 2)

    growth = forecast.get("growth_gb_per_month")
    predicted_full = forecast.get("predicted_full_date")
    days_until_full = None
    if predicted_full:
        try:
            full_dt = datetime.fromisoformat(predicted_full)
            days_until_full = (full_dt - datetime.now()).days
        except Exception:
            pass

    (api_dir / "summary.json").write_text(json.dumps({
        "tv_gb": tv_gb,
        "movie_gb": movie_gb,
        "total_gb": total_gb,
        "capacity_gb": round(capacity_gb, 2) if capacity_gb else None,
        "used_pct": used_pct,
        "shows": len(shows),
        "movies": len(movies),
        "growth_gb_per_month": round(growth, 2) if growth else None,
        "days_until_full": days_until_full,
        "recoverable_gb": recoverable_gb,
    }, indent=2))

    total_plays = sum(u.get("total_plays", 0) for u in users)
    (api_dir / "activity.json").write_text(json.dumps({
        "total_plays": total_plays,
        "users": sorted([
            {
                "name": u["name"],
                "plays": u.get("total_plays", 0),
                "watched_gb": round(u.get("storage_watched_gb", 0), 2),
            }
            for u in users
        ], key=lambda u: u["plays"], reverse=True),
    }, indent=2))

    status_counts = {1: 0, 2: 0, 5: 0}
    for r in requests_data:
        s = r.get("status")
        if s in status_counts:
            status_counts[s] += 1
    unwatched = [i for i in all_items if i.get("request", {}).get("requested") and not i.get("any_watched")]
    top_requester = None
    if users:
        top = max(users, key=lambda u: u.get("requests_made", 0), default=None)
        if top and top.get("requests_made", 0) > 0:
            top_requester = top["name"]
    (api_dir / "requests.json").write_text(json.dumps({
        "total": len(requests_data),
        "pending": status_counts[1],
        "approved": status_counts[2],
        "available": status_counts[5],
        "unwatched_count": len(unwatched),
        "unwatched_gb": round(sum(i.get("size_gb", 0) for i in unwatched), 2),
        "top_requester": top_requester,
    }, indent=2))

    suggest_delete_items = [i for i in all_items if i.get("deletion", {}).get("recommendation") == "suggest_delete"]
    top_candidates = sorted(strong_delete_items, key=lambda x: x.get("size_gb", 0), reverse=True)[:5]
    (api_dir / "deletions.json").write_text(json.dumps({
        "strong_delete_count": len(strong_delete_items),
        "suggest_delete_count": len(suggest_delete_items),
        "strong_delete_gb": recoverable_gb,
        "suggest_delete_gb": round(sum(i.get("size_gb", 0) for i in suggest_delete_items), 2),
        "top_candidates": [
            {
                "title": i["title"],
                "type": i.get("type"),
                "size_gb": round(i.get("size_gb", 0), 2),
                "score": i.get("deletion", {}).get("score"),
                "slug": i.get("slug", ""),
            }
            for i in top_candidates
        ],
    }, indent=2))

    svc_list = services.get("services", []) if isinstance(services, dict) else []
    (api_dir / "services.json").write_text(json.dumps({
        "all_healthy": all(s.get("reachable", False) for s in svc_list) if svc_list else None,
        "updates_available": sum(1 for s in svc_list if s.get("update_available")),
        "checked_at": services.get("checked_at") if isinstance(services, dict) else None,
        "services": [
            {
                "name": s.get("name"),
                "reachable": s.get("reachable"),
                "update_available": s.get("update_available", False),
                "current_version": s.get("current_version"),
            }
            for s in svc_list
        ],
    }, indent=2))


def render_all(
    shows: list[dict],
    movies: list[dict],
    users: list[dict],
    forecast: dict,
    public_dir: Path,
    templates_dir: Path,
    assets_dir: Path,
    services: dict | None = None,
) -> None:
    env = _make_env(templates_dir)

    # Load watchlist — set of (media_type, tmdb_id) tuples
    watchlist_file = public_dir.parent / "data" / "watchlist.json"
    try:
        raw = json.loads(watchlist_file.read_text())
        watchlist_ids = {(mt, tid) for mt, tid in raw}
    except Exception:
        watchlist_ids = set()

    # Copy assets
    public_dir.mkdir(parents=True, exist_ok=True)
    assets_out = public_dir / "assets"
    if assets_dir.exists():
        shutil.copytree(str(assets_dir), str(assets_out), dirs_exist_ok=True)

    # Dashboard
    ctx = _build_dashboard_context(shows, movies, users, forecast, watchlist_ids)
    _render(env, "dashboard.html", public_dir / "index.html", ctx)

    # TV Library
    (public_dir / "tv").mkdir(exist_ok=True)
    _render(env, "tv_library.html", public_dir / "tv" / "index.html", {
        "shows": shows, "users": users
    })

    service_urls = {
        "sonarr": os.getenv("SONARR_URL", "").rstrip("/"),
        "radarr": os.getenv("RADARR_URL", "").rstrip("/"),
        "seerr":  os.getenv("SEERR_URL",  "").rstrip("/"),
    }
    plex_meta_file = public_dir.parent / "data" / "plex_meta.json"
    try:
        machine_id = json.loads(plex_meta_file.read_text()).get("machine_id", "")
    except Exception:
        machine_id = ""
    plex_base = f"https://app.plex.tv/desktop/#!/server/{machine_id}" if machine_id else ""

    # TV Detail pages
    for show in shows:
        slug = show["slug"]
        show_dir = public_dir / "tv" / slug
        show_dir.mkdir(parents=True, exist_ok=True)
        _render(env, "tv_detail.html", show_dir / "index.html", {
            "item": show, "users": users, "service_urls": service_urls, "plex_base": plex_base,
        })

    # Movie Library
    (public_dir / "movies").mkdir(exist_ok=True)
    _render(env, "movie_library.html", public_dir / "movies" / "index.html", {
        "movies": movies, "users": users
    })

    # Movie Detail pages
    for movie in movies:
        slug = movie["slug"]
        movie_dir = public_dir / "movies" / slug
        movie_dir.mkdir(parents=True, exist_ok=True)
        _render(env, "movie_detail.html", movie_dir / "index.html", {
            "item": movie, "users": users, "service_urls": service_urls, "plex_base": plex_base,
        })

    # Users index
    users_dir = public_dir / "users"
    users_dir.mkdir(exist_ok=True)
    _render(env, "users_index.html", users_dir / "index.html", {"users": users})

    # User profiles
    all_items = shows + movies
    items_by_id = {i["id"]: i for i in all_items}
    for user in users:
        user_dir = users_dir / user["name"].lower().replace(" ", "-")
        user_dir.mkdir(parents=True, exist_ok=True)
        requested_items = [items_by_id[i] for i in user.get("requested_item_ids", []) if i in items_by_id]
        watched_items = [items_by_id[i] for i in user.get("watched_item_ids", []) if i in items_by_id]
        _render(env, "user_profile.html", user_dir / "index.html", {
            "user": user,
            "requested_items": requested_items,
            "watched_items": watched_items,
            "users": users,
        })

    # Services page
    services_dir = public_dir / "services"
    services_dir.mkdir(exist_ok=True)
    _render(env, "services.html", services_dir / "index.html", {
        "services": services or {},
    })

    # API endpoints for homepage widgets
    requests_file = public_dir.parent / "data" / "requests.json"
    try:
        requests_data = json.loads(requests_file.read_text())
    except Exception:
        requests_data = []
    render_api(shows, movies, users, forecast, services or {}, requests_data, public_dir)

    print(f"Generated: {public_dir}")
    print(f"  {len(shows)} TV pages, {len(movies)} movie pages, {len(users)} user pages")


def _render(env: Environment, template_name: str, output_path: Path, context: dict) -> None:
    tmpl = env.get_template(template_name)
    output_path.write_text(tmpl.render(**context))
