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


def _compute_format_metrics(db_path: Path) -> dict:
    """
    Query webhook_plays.db and group by source file format.
    Returns {"rows": [...], "quality_profiles": [...ordered list of profile names...]}.
    Each row has per-quality-profile percentage breakdowns; direct plays and
    copies both count toward the "Original" bucket.
    """
    import sqlite3, re
    from collections import defaultdict

    if not db_path.exists():
        return {}

    def _norm_res(r: str) -> str:
        r = (r or "").lower().strip()
        if r in ("4k", "2160", "2160p"):   return "4K"
        if r in ("1080", "1080p"):          return "1080p"
        if r in ("720", "720p"):            return "720p"
        if r in ("576", "576p"):            return "576p"
        if r in ("480", "480p"):            return "480p"
        return r.upper() if r else "?"

    try:
        con = sqlite3.connect(db_path)
        rows = con.execute("""
            SELECT
                src_video_codec,
                src_video_resolution,
                src_hdr_type,
                video_decision,
                stream_video_resolution
            FROM plays
            WHERE event = 'play'
        """).fetchall()
        con.close()
    except Exception:
        return {}

    if not rows:
        return {}

    groups: dict = defaultdict(lambda: {
        "direct": 0, "transcode": 0, "copy": 0,
        "quality_counts": defaultdict(int),
    })

    for codec, res, hdr, decision, stream_res in rows:
        codec = (codec or "").lower()
        res   = (res or "").lower()
        hdr   = (hdr or "").strip()

        if "hevc" in codec or "h265" in codec or "h.265" in codec:
            codec_label = "H.265"
        elif "h264" in codec or "avc" in codec or "h.264" in codec:
            codec_label = "H.264"
        elif "av1" in codec:
            codec_label = "AV1"
        elif "vp9" in codec:
            codec_label = "VP9"
        elif "mpeg2" in codec or "mpeg-2" in codec:
            codec_label = "MPEG-2"
        elif "vc1" in codec or "vc-1" in codec:
            codec_label = "VC-1"
        else:
            codec_label = codec.upper() if codec else "Unknown"

        if res in ("4k", "2160", "2160p"):
            res_label = "4K"
        elif res in ("1080", "1080p"):
            res_label = "1080p"
        elif res in ("720", "720p"):
            res_label = "720p"
        elif res in ("480", "480p"):
            res_label = "480p"
        elif res in ("576", "576p"):
            res_label = "576p"
        else:
            res_label = res.upper() if res else ""

        parts = [codec_label, res_label]
        if hdr:
            parts.append(hdr)
        key = " ".join(p for p in parts if p) or "Unknown"

        g = groups[key]
        vd = (decision or "").lower()
        delivered = _norm_res(stream_res) if stream_res else res_label
        if vd == "direct play":
            g["direct"] += 1
        elif vd == "copy":
            g["copy"] += 1
        elif vd == "transcode":
            g["transcode"] += 1
        else:
            continue
        g["quality_counts"][delivered] += 1

    def _fmt_rank(fmt: str) -> tuple:
        s = fmt.lower()
        if "4k" in s or "2160" in s:
            res = 2160
        elif "1080" in s:
            res = 1080
        elif "720" in s:
            res = 720
        elif "576" in s:
            res = 576
        elif "480" in s:
            res = 480
        else:
            m = re.search(r"(\d{3,4})", s)
            res = int(m.group(1)) if m else 0
        hdr = 1 if any(x in s for x in ("hdr", "dv", "hlg", "dolby")) else 0
        codec = 0
        if "av1" in s:      codec = 5
        elif "h.265" in s:  codec = 4
        elif "h.264" in s:  codec = 3
        elif "vp9" in s:    codec = 2
        elif "mpeg" in s or "vc-1" in s: codec = 1
        return (res, hdr, codec)

    def _quality_sort_key(qp: str) -> int:
        s = qp.lower()
        if "4k" in s or "2160" in s: return 2160
        for pat in (r"(\d{3,4})p", r"(\d{3,4})"):
            m = re.search(pat, s)
            if m:
                return int(m.group(1))
        return 0

    # Collect all quality profiles seen across every format, sorted
    all_profiles: set = set()
    for g in groups.values():
        all_profiles.update(g["quality_counts"].keys())
    quality_profiles = sorted(all_profiles, key=lambda q: -_quality_sort_key(q))

    result_rows = []
    for fmt, g in sorted(groups.items(), key=lambda x: _fmt_rank(x[0]), reverse=True):
        total = g["direct"] + g["transcode"] + g["copy"]
        if total == 0:
            continue
        quality_pcts = {
            qp: round(cnt / total * 100)
            for qp, cnt in g["quality_counts"].items()
        }
        result_rows.append({
            "format":        fmt,
            "total_plays":   total,
            "direct_pct":    round(g["direct"]    / total * 100),
            "transcode_pct": round(g["transcode"] / total * 100),
            "copy_pct":      round(g["copy"]      / total * 100),
            "quality_pcts":  quality_pcts,
        })

    return {"rows": result_rows, "quality_profiles": quality_profiles}


def _compute_user_bandwidth(db_path: Path) -> list:
    """Per-user stream_video_bitrate stats: median, avg, min, max (kbps → Mbps)."""
    import sqlite3
    from collections import defaultdict

    if not db_path.exists():
        return []
    try:
        con = sqlite3.connect(db_path)
        rows = con.execute("""
            SELECT client_friendly_name, stream_video_bitrate
            FROM plays
            WHERE event = 'play' AND stream_video_bitrate IS NOT NULL AND stream_video_bitrate > 0
        """).fetchall()
        con.close()
    except Exception:
        return []

    by_user: dict = defaultdict(list)
    for user, bw in rows:
        try:
            by_user[user or "Unknown"].append(int(bw))
        except (TypeError, ValueError):
            pass

    def _median(vals):
        s = sorted(vals)
        n = len(s)
        return (s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2)

    def _mbps(kbps):
        return round(kbps / 1000, 1)

    result = []
    for user, vals in sorted(by_user.items()):
        result.append({
            "user":   user,
            "plays":  len(vals),
            "median": _mbps(_median(vals)),
            "avg":    _mbps(sum(vals) / len(vals)),
            "min":    _mbps(min(vals)),
            "max":    _mbps(max(vals)),
        })
    return sorted(result, key=lambda r: -r["avg"])


def _compute_playback_analytics(db_path: Path) -> dict | None:
    """Query webhook_plays.db and return all analytics needed for the Playback page."""
    import sqlite3
    from collections import defaultdict

    if not db_path.exists():
        return None

    try:
        con = sqlite3.connect(db_path)
        rows = con.execute("""
            SELECT transcode_decision, video_decision, audio_decision, subtitle_decision,
                   quality_profile, src_video_codec, src_video_resolution, src_hdr_type,
                   src_audio_codec, src_audio_channels,
                   stream_video_codec, stream_video_resolution,
                   client_platform, client_friendly_name
            FROM plays WHERE event = 'play'
        """).fetchall()
        con.close()
    except Exception:
        return None

    if not rows:
        return None

    total = len(rows)
    direct = transcode = copy_ = 0

    # Transcode reason buckets
    reason_counts: dict[str, int] = {"Video": 0, "Audio": 0, "Video + Audio": 0, "Subtitle burn": 0, "Other": 0}

    # Platform: {name: {direct, transcode, copy}}
    platforms: dict[str, dict] = defaultdict(lambda: {"direct": 0, "transcode": 0, "copy": 0})

    # Audio codec transcode rate: {codec: {transcode, total}}
    audio_codecs: dict[str, dict] = defaultdict(lambda: {"transcode": 0, "total": 0})

    # HDR outcomes: {hdr_type: {direct, transcode, copy}}
    hdr_outcomes: dict[str, dict] = defaultdict(lambda: {"direct": 0, "transcode": 0, "copy": 0})

    # Top transcoders: {user: count}
    transcoders: dict[str, int] = defaultdict(int)

    for (td, vd, ad, sd, qp, src_vc, src_vr, hdr, src_ac, src_ach, stm_vc, stm_vr, platform, user) in rows:
        td = (td or "").lower()
        vd = (vd or "").lower()
        ad = (ad or "").lower()
        sd = (sd or "").lower()
        platform = (platform or "Unknown").strip() or "Unknown"
        user = (user or "Unknown").strip() or "Unknown"
        src_ac = (src_ac or "").lower()

        if td == "direct play":
            direct += 1
        elif td == "transcode":
            transcode += 1
        elif td == "copy":
            copy_ += 1

        # Platform
        p = platforms[platform]
        if td == "direct play":
            p["direct"] += 1
        elif td == "transcode":
            p["transcode"] += 1
        else:
            p["copy"] += 1

        # Transcode reason
        if td == "transcode":
            video_t = vd == "transcode"
            audio_t = ad == "transcode"
            sub_burn = sd == "burn"
            if video_t and audio_t:
                reason_counts["Video + Audio"] += 1
            elif video_t:
                reason_counts["Video"] += 1
            elif audio_t:
                reason_counts["Audio"] += 1
            elif sub_burn:
                reason_counts["Subtitle burn"] += 1
            else:
                reason_counts["Other"] += 1
            transcoders[user] += 1

        # Audio codec
        if src_ac:
            ac = audio_codecs[src_ac.upper()]
            ac["total"] += 1
            if ad == "transcode":
                ac["transcode"] += 1

        # HDR outcomes
        if hdr:
            h = hdr_outcomes[hdr]
            if td == "direct play":
                h["direct"] += 1
            elif td == "transcode":
                h["transcode"] += 1
            else:
                h["copy"] += 1

    # Build platform_rates — min 3 plays, sorted worst→best direct%
    platform_rates = []
    for name, counts in platforms.items():
        pt = counts["direct"] + counts["transcode"] + counts["copy"]
        if pt < 3:
            continue
        platform_rates.append({
            "platform": name,
            "plays": pt,
            "direct_pct": round(counts["direct"] / pt * 100),
            "transcode_pct": round(counts["transcode"] / pt * 100),
        })
    platform_rates.sort(key=lambda x: x["direct_pct"])

    # Build audio_causes — sorted by transcode% desc
    audio_causes = []
    for codec, counts in audio_codecs.items():
        if counts["total"] < 2:
            continue
        audio_causes.append({
            "codec": codec,
            "plays": counts["total"],
            "transcode_pct": round(counts["transcode"] / counts["total"] * 100),
        })
    audio_causes.sort(key=lambda x: -x["transcode_pct"])

    # HDR outcomes list
    hdr_list = []
    for hdr_type, counts in hdr_outcomes.items():
        ht = counts["direct"] + counts["transcode"] + counts["copy"]
        hdr_list.append({
            "hdr_type": hdr_type,
            "plays": ht,
            "direct_pct":    round(counts["direct"]    / ht * 100) if ht else 0,
            "transcode_pct": round(counts["transcode"] / ht * 100) if ht else 0,
            "copy_pct":      round(counts["copy"]      / ht * 100) if ht else 0,
        })
    hdr_list.sort(key=lambda x: -x["plays"])

    # Transcode reasons for doughnut (exclude zeros)
    reason_colors = {
        "Video":         "#ef4444",
        "Audio":         "#f59e0b",
        "Video + Audio": "#f97316",
        "Subtitle burn": "#6366f1",
        "Other":         "#6b7280",
    }
    transcode_reasons = [
        {"label": k, "count": v, "color": reason_colors[k]}
        for k, v in reason_counts.items() if v > 0
    ]

    # Top transcoders (top 8)
    top_transcoders = sorted(
        [{"user": u, "transcode_count": c} for u, c in transcoders.items()],
        key=lambda x: -x["transcode_count"]
    )[:8]

    # Worst platform (most transcodes, min 3 plays)
    worst_platform = None
    if platform_rates:
        worst = max(platform_rates, key=lambda x: x["transcode_pct"])
        if worst["transcode_pct"] > 0:
            worst_platform = worst["platform"]

    return {
        "total_plays":       total,
        "direct_pct":        round(direct    / total * 100) if total else 0,
        "transcode_pct":     round(transcode / total * 100) if total else 0,
        "copy_pct":          round(copy_     / total * 100) if total else 0,
        "worst_platform":    worst_platform,
        "transcode_reasons": transcode_reasons,
        "platform_rates":    platform_rates,
        "audio_causes":      audio_causes,
        "hdr_outcomes":      hdr_list,
        "top_transcoders":   top_transcoders,
    }


def _build_dashboard_context(shows, movies, users, forecast, watchlist_ids=None, db_path: Path | None = None):
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

    db_path = public_dir.parent / "data" / "webhook_plays.db"

    # Dashboard
    ctx = _build_dashboard_context(shows, movies, users, forecast, watchlist_ids, db_path=db_path)
    _render(env, "dashboard.html", public_dir / "index.html", ctx)

    # Playback Analytics
    svc_list = (services or {}).get("services", []) if isinstance(services, dict) else []
    tautulli_svc = next((s for s in svc_list if s.get("name") == "Tautulli"), None)
    tautulli_configured = tautulli_svc is not None and not tautulli_svc.get("not_configured")
    (public_dir / "playback").mkdir(exist_ok=True)
    _render(env, "playback.html", public_dir / "playback" / "index.html", {
        "analytics":            _compute_playback_analytics(db_path),
        "format_metrics":       _compute_format_metrics(db_path),
        "user_bandwidth":       _compute_user_bandwidth(db_path),
        "tautulli_configured":  tautulli_configured,
    })

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
