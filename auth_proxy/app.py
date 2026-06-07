import asyncio
import os
import subprocess
import json
from pathlib import Path
from secrets import compare_digest
from datetime import datetime, timezone, timedelta
import httpx
import aiosqlite
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse, FileResponse
from fastapi import HTTPException
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

app = FastAPI()

SECRET_KEY = os.environ["AUTH_SECRET_KEY"]
PLEX_TOKEN = os.environ.get("PLEX_TOKEN") or os.environ.get("HOMEPAGE_VAR_PLEX_TOKEN", "")
PLEX_SERVER_URL = os.environ.get("PLEX_URL", "").rstrip("/")
CLIENT_ID = os.environ.get("PLEX_CLIENT_ID", "2a1c3b4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d")
API_KEY = os.environ.get("AUTH_API_KEY", "")
SESSION_MAX_AGE = 86400 * 7
COOKIE_SESSION = "mm_session"

PUBLIC_DIR = Path(os.environ.get("PUBLIC_DIR", "/app/public"))
CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/app/config"))
DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
PROJECT_DIR = Path(os.environ.get("PROJECT_DIR", "/app/project"))
CONFIG_ENV_PATH = CONFIG_DIR / ".env"
PLAYS_DB = DATA_DIR / "webhook_plays.db"

PIPELINE_JOBS = [
    {"id": "sonarr",    "name": "Sonarr",             "description": "Fetch TV library from Sonarr"},
    {"id": "radarr",    "name": "Radarr",              "description": "Fetch movie library from Radarr"},
    {"id": "overseerr", "name": "Overseerr",           "description": "Fetch requests & watchlists from Overseerr"},
    {"id": "watch",     "name": "Watch History",       "description": "Fetch watch history from Plex and/or Tautulli"},
    {"id": "tmdb",      "name": "TMDB",                "description": "Fetch metadata & artwork from TMDB"},
    {"id": "services",  "name": "Services",            "description": "Check health & version of all services"},
    {"id": "build",     "name": "Build",               "description": "Rebuild data files from cached raw data"},
    {"id": "generate",  "name": "Generate",            "description": "Regenerate dashboard HTML"},
    {"id": "collect",   "name": "Collect All",         "description": "Run all collectors then build"},
    {"id": "all",       "name": "Full Pipeline",       "description": "Collect all + build + generate"},
]
_running_jobs: dict[str, bool] = {}
_jobs_lock = asyncio.Lock()
_scheduler = AsyncIOScheduler(timezone=timezone.utc)
SEERR_URL = os.environ.get("SEERR_URL", "").rstrip("/")
SEERR_KEY = os.environ.get("SEERR_API_KEY") or os.environ.get("HOMEPAGE_VAR_SEERR_API_KEY", "")
def _is_setup_complete() -> bool:
    if os.environ.get("SETUP_COMPLETE", "").lower() in ("true", "1", "yes"):
        return True
    if CONFIG_ENV_PATH.exists():
        for line in CONFIG_ENV_PATH.read_text().splitlines():
            if line.strip().upper() == "SETUP_COMPLETE=TRUE" or line.strip() == "SETUP_COMPLETE=1":
                return True
    return False

signer = URLSafeTimedSerializer(SECRET_KEY)
server_machine_id: str | None = None


def _schedule_pipeline(cron_expr: str) -> None:
    """Replace the scheduled pipeline job with a new cron expression."""
    _scheduler.remove_all_jobs()
    try:
        trigger = CronTrigger.from_crontab(cron_expr, timezone=timezone.utc)
        _scheduler.add_job(_run_pipeline_scheduled, trigger, id="pipeline", replace_existing=True)
        print(f"Scheduled pipeline: {cron_expr}")
    except Exception as e:
        print(f"Warning: invalid cron expression {cron_expr!r}: {e}")


async def _run_pipeline_scheduled() -> None:
    """Run the full pipeline (all) as the scheduled job."""
    async with _jobs_lock:
        if _running_jobs.get("all"):
            return
        _running_jobs["all"] = True
    try:
        script = PROJECT_DIR / "scripts" / "run.py"
        proc = await asyncio.create_subprocess_exec(
            "python", str(script), "all",
            cwd=str(PROJECT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        await proc.communicate()
    finally:
        async with _jobs_lock:
            _running_jobs["all"] = False


async def _init_plays_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(PLAYS_DB) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS plays (
                id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                event                    TEXT,
                event_at                 INTEGER,
                session_key              TEXT,
                rating_key               TEXT,
                tmdb_id                  INTEGER,
                media_type               TEXT,
                transcode_decision       TEXT,
                video_decision           TEXT,
                audio_decision           TEXT,
                subtitle_decision        TEXT,
                quality_profile          TEXT,
                progress_percent         REAL,
                view_offset              INTEGER,
                src_container            TEXT,
                src_video_codec          TEXT,
                src_video_bitrate        TEXT,
                src_video_resolution     TEXT,
                src_video_bit_depth      TEXT,
                src_hdr_type             TEXT,
                src_audio_codec          TEXT,
                src_audio_channels       TEXT,
                stream_container         TEXT,
                stream_video_codec       TEXT,
                stream_video_bitrate     TEXT,
                stream_video_resolution  TEXT,
                stream_audio_codec       TEXT,
                stream_audio_channels    TEXT,
                client_user              TEXT,
                client_friendly_name     TEXT,
                client_platform          TEXT,
                client_platform_version  TEXT,
                client_product           TEXT,
                client_player            TEXT,
                client_device            TEXT
            )
        """)
        for ddl in (
            "ALTER TABLE plays ADD COLUMN session_key TEXT",
            "ALTER TABLE plays ADD COLUMN progress_percent REAL",
            "ALTER TABLE plays ADD COLUMN view_offset INTEGER",
        ):
            try:
                await db.execute(ddl)
            except Exception:
                pass  # column already exists
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_plays_session_event_at ON plays(session_key, event_at, id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_plays_event_session ON plays(event, session_key)"
        )
        await db.commit()


async def _init_deletions_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(PLAYS_DB) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS pending_deletions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id       TEXT NOT NULL,
                item_type     TEXT NOT NULL,
                title         TEXT,
                season_number INTEGER,
                plex_key      TEXT,
                scheduled_for TEXT NOT NULL,
                created_at    TEXT NOT NULL,
                status        TEXT NOT NULL DEFAULT 'pending'
            )
        """)
        await db.commit()


LEAVING_SOON_LABEL = "Leaving Soon"
SEASON_LEAVING_LABEL = "Season Leaving"
LEAVING_SOON_DAYS = 14


def _instances(url_var: str, key_var: str) -> list[tuple[str, str]]:
    """Returns [(url, api_key), ...] for a comma-separated *_URL/*_API_KEY pair."""
    cfg = _read_config_env()
    urls = [u.strip() for u in (os.environ.get(url_var) or cfg.get(url_var, "")).split(",") if u.strip()]
    keys = [k.strip() for k in (os.environ.get(key_var) or cfg.get(key_var, "")).split(",") if k.strip()]
    return [(u, keys[i] if i < len(keys) else (keys[0] if keys else "")) for i, u in enumerate(urls)]


def _find_item(item_id: str) -> dict | None:
    try:
        tv = json.loads((DATA_DIR / "tv.json").read_text())
        movies = json.loads((DATA_DIR / "movies.json").read_text())
        for item in tv + movies:
            if item.get("id") == item_id:
                return item
    except Exception:
        pass
    return None


async def _radarr_delete_movie(radarr_id: int) -> bool:
    for url, key in _instances("RADARR_URL", "RADARR_API_KEY"):
        try:
            async with httpx.AsyncClient(verify=False, timeout=30) as client:
                resp = await client.delete(
                    f"{url.rstrip('/')}/api/v3/movie/{radarr_id}",
                    params={"deleteFiles": "true", "addImportExclusion": "false"},
                    headers={"X-Api-Key": key},
                )
                if resp.status_code in (200, 202, 204):
                    return True
        except Exception as e:
            print(f"Radarr delete failed against {url}: {e}")
    return False


async def _sonarr_delete_series(sonarr_id: int) -> bool:
    for url, key in _instances("SONARR_URL", "SONARR_API_KEY"):
        try:
            async with httpx.AsyncClient(verify=False, timeout=30) as client:
                resp = await client.delete(
                    f"{url.rstrip('/')}/api/v3/series/{sonarr_id}",
                    params={"deleteFiles": "true"},
                    headers={"X-Api-Key": key},
                )
                if resp.status_code in (200, 202, 204):
                    return True
        except Exception as e:
            print(f"Sonarr delete failed against {url}: {e}")
    return False


async def _sonarr_delete_season(sonarr_id: int, season_number: int) -> bool:
    for url, key in _instances("SONARR_URL", "SONARR_API_KEY"):
        try:
            async with httpx.AsyncClient(verify=False, timeout=30) as client:
                resp = await client.get(
                    f"{url.rstrip('/')}/api/v3/episodefile",
                    params={"seriesId": sonarr_id},
                    headers={"X-Api-Key": key},
                )
                if resp.status_code != 200:
                    continue
                file_ids = [f["id"] for f in resp.json() if f.get("seasonNumber") == season_number]
                if not file_ids:
                    continue
                del_resp = await client.delete(
                    f"{url.rstrip('/')}/api/v3/episodefile/bulk",
                    json={"episodeFileIds": file_ids},
                    headers={"X-Api-Key": key},
                )
                if del_resp.status_code in (200, 202, 204):
                    return True
        except Exception as e:
            print(f"Sonarr season delete failed against {url}: {e}")
    return False


async def _delete_via_arr(item: dict, season_number: int | None) -> bool:
    if item["type"] == "movie":
        return await _radarr_delete_movie(item["radarr_id"])
    if season_number is not None:
        return await _sonarr_delete_season(item["sonarr_id"], season_number)
    return await _sonarr_delete_series(item["sonarr_id"])


async def _plex_set_label(rating_key: str | None, label: str, add: bool) -> None:
    """Add or remove a Plex label on an item via the metadata edit-fields API.

    NOTE: untested against a live server — Plex's batch tag-edit query parameter
    format (label[].tag.tag / label[].tag.tag.tag-) has shifted across versions.
    Verify this against your server and adjust if the label doesn't apply/clear.
    """
    if not (PLEX_SERVER_URL and PLEX_TOKEN and rating_key):
        return
    try:
        params = {"X-Plex-Token": PLEX_TOKEN}
        if add:
            params["label[0].tag.tag"] = label
            params["label.locked"] = "1"
        else:
            params["label[0].tag.tag-"] = label
        async with httpx.AsyncClient(verify=False, timeout=15) as client:
            await client.put(f"{PLEX_SERVER_URL}/library/metadata/{rating_key}", params=params)
    except Exception as e:
        print(f"Warning: could not {'add' if add else 'remove'} Plex label on {rating_key}: {e}")


async def _get_plex_season_key(show_plex_key: str, season_number: int) -> str | None:
    """Return the Plex ratingKey for a specific season, or None if not found."""
    if not (PLEX_SERVER_URL and PLEX_TOKEN and show_plex_key):
        return None
    try:
        async with httpx.AsyncClient(verify=False, timeout=15) as client:
            resp = await client.get(
                f"{PLEX_SERVER_URL}/library/metadata/{show_plex_key}/children",
                params={"X-Plex-Token": PLEX_TOKEN},
                headers={"Accept": "application/json"},
            )
            if resp.status_code != 200:
                return None
            children = resp.json().get("MediaContainer", {}).get("Metadata", [])
            for child in children:
                if child.get("index") == season_number:
                    return child.get("ratingKey")
    except Exception as e:
        print(f"Warning: could not fetch Plex season key for show {show_plex_key} S{season_number}: {e}")
    return None


async def _maybe_remove_label(plex_key: str | None) -> None:
    """Remove the Leaving Soon label only if no other pending row still references this Plex item."""
    if not plex_key:
        return
    async with aiosqlite.connect(PLAYS_DB) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM pending_deletions WHERE plex_key = ? AND status = 'pending'", (plex_key,)
        )
        row = await cur.fetchone()
    if row and row[0] == 0:
        await _plex_set_label(plex_key, LEAVING_SOON_LABEL, add=False)


async def _maybe_remove_season_leaving_label(item_id: str) -> None:
    """Remove 'Season Leaving' from the show if no season deletions remain pending for it."""
    async with aiosqlite.connect(PLAYS_DB) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM pending_deletions WHERE item_id = ? AND season_number IS NOT NULL AND status = 'pending'",
            (item_id,),
        )
        row = await cur.fetchone()
    if row and row[0] == 0:
        item = _find_item(item_id)
        if item:
            await _plex_set_label(item.get("plex_key"), SEASON_LEAVING_LABEL, add=False)


async def _cancel_pending_for(item_id: str, season_number: int | None) -> None:
    async with aiosqlite.connect(PLAYS_DB) as db:
        if season_number is None:
            cur = await db.execute(
                "SELECT id, plex_key FROM pending_deletions WHERE item_id = ? AND season_number IS NULL AND status = 'pending'",
                (item_id,),
            )
        else:
            cur = await db.execute(
                "SELECT id, plex_key FROM pending_deletions WHERE item_id = ? AND season_number = ? AND status = 'pending'",
                (item_id, season_number),
            )
        rows = await cur.fetchall()
        for row_id, _ in rows:
            await db.execute("UPDATE pending_deletions SET status = 'cancelled' WHERE id = ?", (row_id,))
        await db.commit()
    for _, plex_key in rows:
        await _maybe_remove_label(plex_key)
    if season_number is not None:
        await _maybe_remove_season_leaving_label(item_id)


async def _process_pending_deletions() -> None:
    """Daily job: perform the actual delete for any pending row whose date has arrived."""
    today = datetime.now(timezone.utc).date().isoformat()
    async with aiosqlite.connect(PLAYS_DB) as db:
        cur = await db.execute(
            "SELECT id, item_id, item_type, season_number, plex_key FROM pending_deletions "
            "WHERE status = 'pending' AND scheduled_for <= ?",
            (today,),
        )
        rows = await cur.fetchall()

    any_done = False
    for row_id, item_id, item_type, season_number, plex_key in rows:
        item = _find_item(item_id)
        if not item:
            continue
        ok = await _delete_via_arr(item, season_number)
        if not ok:
            continue
        async with aiosqlite.connect(PLAYS_DB) as db:
            await db.execute("UPDATE pending_deletions SET status = 'done' WHERE id = ?", (row_id,))
            await db.commit()
        await _maybe_remove_label(plex_key)
        if season_number is not None:
            await _maybe_remove_season_leaving_label(item_id)
        any_done = True

    if any_done:
        asyncio.create_task(_run_pipeline_scheduled())


@app.post("/api/library/delete-now")
async def library_delete_now(request: Request):
    body = await request.json()
    item_id = str(body.get("item_id", ""))
    season_number = body.get("season_number")
    item = _find_item(item_id)
    if not item:
        return JSONResponse({"error": "Item not found."}, status_code=404)

    ok = await _delete_via_arr(item, season_number)
    if not ok:
        return JSONResponse({"error": "Delete failed — check that Sonarr/Radarr are reachable."}, status_code=502)

    await _cancel_pending_for(item_id, season_number)
    asyncio.create_task(_run_pipeline_scheduled())
    return JSONResponse({"ok": True})


@app.post("/api/library/schedule-delete")
async def library_schedule_delete(request: Request):
    body = await request.json()
    item_id = str(body.get("item_id", ""))
    season_number = body.get("season_number")
    item = _find_item(item_id)
    if not item:
        return JSONResponse({"error": "Item not found."}, status_code=404)

    scheduled_for = (datetime.now(timezone.utc) + timedelta(days=LEAVING_SOON_DAYS)).date().isoformat()
    item_type = "season" if season_number is not None else item["type"]
    title = item["title"] if season_number is None else f"{item['title']} — Season {season_number}"

    if season_number is not None:
        plex_key = await _get_plex_season_key(item.get("plex_key"), season_number)
    else:
        plex_key = item.get("plex_key")

    async with aiosqlite.connect(PLAYS_DB) as db:
        await db.execute(
            "INSERT INTO pending_deletions "
            "(item_id, item_type, title, season_number, plex_key, scheduled_for, created_at, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')",
            (item_id, item_type, title, season_number, plex_key,
             scheduled_for, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()

    await _plex_set_label(plex_key, LEAVING_SOON_LABEL, add=True)
    if season_number is not None:
        await _plex_set_label(item.get("plex_key"), SEASON_LEAVING_LABEL, add=True)
    return JSONResponse({"ok": True, "scheduled_for": scheduled_for})


@app.get("/api/library/pending")
async def library_pending():
    """Items currently scheduled for deletion — used to restore UI state on page load."""
    async with aiosqlite.connect(PLAYS_DB) as db:
        cur = await db.execute(
            "SELECT item_id, season_number, title, scheduled_for FROM pending_deletions WHERE status = 'pending'"
        )
        rows = await cur.fetchall()
    return JSONResponse({
        "pending": [
            {"item_id": item_id, "season_number": season_number, "title": title, "scheduled_for": scheduled_for}
            for item_id, season_number, title, scheduled_for in rows
        ]
    })


@app.post("/api/library/cancel-delete")
async def library_cancel_delete(request: Request):
    body = await request.json()
    item_id = str(body.get("item_id", ""))
    season_number = body.get("season_number")
    await _cancel_pending_for(item_id, season_number)
    return JSONResponse({"ok": True})


@app.on_event("startup")
async def startup():
    global server_machine_id

    await _init_plays_db()
    await _init_deletions_db()

    # Start the APScheduler; fall back to default schedule if not explicitly configured
    _scheduler.start()
    DEFAULT_CRON = "0 */6 * * *"
    cron_expr = _read_config_env().get("CRON_SCHEDULE", DEFAULT_CRON).strip() or DEFAULT_CRON
    _schedule_pipeline(cron_expr)
    _scheduler.add_job(_process_pending_deletions, CronTrigger(hour=3, minute=15, timezone=timezone.utc),
                       id="pending_deletions", replace_existing=True)

    if not PLEX_SERVER_URL or not PLEX_TOKEN:
        print("Info: PLEX_URL/PLEX_TOKEN not configured — Plex server identity check skipped")
        return
    try:
        async with httpx.AsyncClient(verify=False, timeout=10) as client:
            resp = await client.get(
                f"{PLEX_SERVER_URL}/identity",
                headers={"X-Plex-Token": PLEX_TOKEN, "Accept": "application/json"},
            )
            server_machine_id = resp.json()["MediaContainer"]["machineIdentifier"]
    except Exception as exc:
        print(f"Warning: could not fetch Plex server identity: {exc}")


# ── Auth middleware ───────────────────────────────────────────────────────────

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path

    # Tautulli webhook — no auth required (internal service)
    if path == "/api/tautulli/webhook":
        return await call_next(request)

    # Setup wizard redirect — must come before auth check
    if not _is_setup_complete():
        if path not in ("/setup",) and not path.startswith("/api/setup") and not path.startswith("/auth/"):
            return RedirectResponse("/setup", status_code=302)
        return await call_next(request)

    if path.startswith("/auth/"):
        return await call_next(request)

    authed = False
    session_cookie = request.cookies.get(COOKIE_SESSION)
    if session_cookie:
        try:
            signer.loads(session_cookie, max_age=SESSION_MAX_AGE)
            authed = True
        except (BadSignature, SignatureExpired):
            pass

    if not authed and API_KEY:
        provided = request.query_params.get("api_key", "")
        if not provided:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                provided = auth_header[7:]
        if provided and compare_digest(provided, API_KEY):
            authed = True

    if not authed:
        return RedirectResponse("/auth/login", status_code=302)

    return await call_next(request)


# ── Auth routes ───────────────────────────────────────────────────────────────

_LOGIN_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Media Manager — Sign in</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      min-height: 100vh; display: flex; align-items: center; justify-content: center;
      background: #0f0f13; font-family: system-ui, sans-serif; color: #e5e7eb;
    }
    .card {
      background: #18181f; border: 1px solid #2a2a35; border-radius: 12px;
      padding: 2.5rem 2rem; width: 100%; max-width: 360px; text-align: center;
    }
    .logo { font-size: 1.5rem; font-weight: 700; letter-spacing: -.5px; margin-bottom: .25rem; }
    .sub { color: #6b7280; font-size: .875rem; margin-bottom: 2rem; }
    button {
      display: inline-flex; align-items: center; gap: .5rem; cursor: pointer;
      background: #e5a00d; color: #000; font-weight: 600; font-size: .95rem;
      padding: .7rem 1.5rem; border-radius: 8px; border: none;
      transition: opacity .15s;
    }
    button:hover { opacity: .85; }
    button:disabled { opacity: .5; cursor: default; }
    .msg { margin-top: 1.25rem; font-size: .8rem; color: #ef4444; }
    .info { margin-top: 1.25rem; font-size: .8rem; color: #6b7280; }
  </style>
</head>
<body>
  <div class="card">
    <div class="logo">Media Manager</div>
    <div class="sub">Sign in to continue</div>
    <button id="btn" onclick="startLogin()">
      <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor">
        <path d="M10 2C5.58 2 2 5.58 2 10s3.58 8 8 8 8-3.58 8-8-3.58-8-8-8zm0 3c1.66 0 3 1.34 3 3s-1.34 3-3 3-3-1.34-3-3 1.34-3 3-3zm0 9.2c-2.5 0-4.71-1.28-6-3.22.03-1.99 4-3.08 6-3.08 1.99 0 5.97 1.09 6 3.08-1.29 1.94-3.5 3.22-6 3.22z"/>
      </svg>
      Sign in with Plex
    </button>
    <div id="status"></div>
  </div>
  <script>
    const CLIENT_ID = "CLIENT_ID_PLACEHOLDER";
    let pollTimer = null;

    async function startLogin() {
      const btn = document.getElementById("btn");
      const status = document.getElementById("status");
      btn.disabled = true;
      status.className = "info";
      status.textContent = "Opening Plex…";

      try {
        const pinResp = await fetch("https://plex.tv/api/v2/pins", {
          method: "POST",
          headers: {
            "X-Plex-Client-Identifier": CLIENT_ID,
            "X-Plex-Product": "Media Manager",
            "X-Plex-Version": "1.0",
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
          },
          body: "strong=true",
        });
        const pin = await pinResp.json();

        const callbackUrl = window.location.origin + "/auth/waiting";
        const authUrl = "https://app.plex.tv/auth#?" +
          "clientID=" + encodeURIComponent(CLIENT_ID) +
          "&code=" + encodeURIComponent(pin.code) +
          "&forwardUrl=" + encodeURIComponent(callbackUrl) +
          "&context[device][product]=Media+Manager";

        const w = 800, h = 700;
        const left = Math.round(window.screenX + (window.outerWidth - w) / 2);
        const top = Math.round(window.screenY + (window.outerHeight - h) / 2);
        window.open(authUrl, "plex-auth", `width=${w},height=${h},left=${left},top=${top},popup=yes`);
        status.textContent = "Waiting for Plex sign-in…";
        pollForToken(pin.id);
      } catch (e) {
        status.className = "msg";
        status.textContent = "Could not reach Plex. Try again.";
        btn.disabled = false;
      }
    }

    function pollForToken(pinId) {
      let attempts = 0;
      pollTimer = setInterval(async () => {
        attempts++;
        if (attempts > 60) {
          clearInterval(pollTimer);
          document.getElementById("status").className = "msg";
          document.getElementById("status").textContent = "Timed out. Please try again.";
          document.getElementById("btn").disabled = false;
          return;
        }
        try {
          const r = await fetch("https://plex.tv/api/v2/pins/" + pinId, {
            headers: {
              "X-Plex-Client-Identifier": CLIENT_ID,
              "Accept": "application/json",
            },
          });
          const data = await r.json();
          if (data.authToken) {
            clearInterval(pollTimer);
            validateToken(data.authToken);
          }
        } catch (_) {}
      }, 3000);
    }

    async function validateToken(token) {
      const status = document.getElementById("status");
      status.textContent = "Verifying…";
      try {
        const r = await fetch("/auth/validate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ token }),
        });
        if (r.ok) {
          window.location.href = "/";
        } else {
          const data = await r.json().catch(() => ({}));
          status.className = "msg";
          status.textContent = data.detail || "Access denied.";
          document.getElementById("btn").disabled = false;
        }
      } catch (e) {
        status.className = "msg";
        status.textContent = "Verification failed. Try again.";
        document.getElementById("btn").disabled = false;
      }
    }

    if (window.opener && window.location.pathname === "/auth/waiting") {
      window.close();
    }
  </script>
</body>
</html>"""


@app.get("/auth/login")
async def login():
    return HTMLResponse(_LOGIN_PAGE.replace("CLIENT_ID_PLACEHOLDER", CLIENT_ID))


@app.get("/auth/waiting")
async def waiting():
    return HTMLResponse("<html><body><script>window.close();</script><p>Done, closing…</p></body></html>")


@app.post("/auth/validate")
async def validate(request: Request):
    body = await request.json()
    auth_token = body.get("token", "").strip()
    if not auth_token:
        return JSONResponse({"detail": "Missing token."}, status_code=400)

    if server_machine_id:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    "https://plex.tv/api/v2/resources",
                    params={"includeHttps": 1, "includeRelay": 1},
                    headers={
                        "X-Plex-Client-Identifier": CLIENT_ID,
                        "X-Plex-Product": "Media Manager",
                        "X-Plex-Version": "1.0",
                        "Accept": "application/json",
                        "X-Plex-Token": auth_token,
                    },
                )
                resources = resp.json()
        except Exception:
            return JSONResponse({"detail": "Could not verify with Plex."}, status_code=502)

        accessible = {
            r["clientIdentifier"]
            for r in resources
            if r.get("provides", "").startswith("server")
        }
        if server_machine_id not in accessible:
            return JSONResponse({"detail": "Your Plex account doesn't have access to this server."}, status_code=403)

    response = JSONResponse({"ok": True})
    response.set_cookie(
        COOKIE_SESSION,
        signer.dumps(auth_token),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=True,
    )
    return response


@app.get("/auth/logout")
async def logout():
    response = RedirectResponse("/", status_code=302)
    response.delete_cookie(COOKIE_SESSION)
    return response


# ── Settings API ──────────────────────────────────────────────────────────────

def _read_user_mappings() -> list:
    p = CONFIG_DIR / "users.json"
    if not p.exists():
        return []
    return json.loads(p.read_text())


def _write_user_mappings(mappings: list) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    (CONFIG_DIR / "users.json").write_text(json.dumps(mappings, indent=2))


async def _fetch_seerr_users() -> list[dict]:
    if not SEERR_URL or not SEERR_KEY:
        return []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{SEERR_URL}/api/v1/user",
                params={"take": 100, "skip": 0},
                headers={"X-Api-Key": SEERR_KEY},
            )
            data = resp.json()
            return [
                {"id": u["id"], "displayName": u.get("displayName") or u.get("username") or str(u["id"])}
                for u in data.get("results", [])
            ]
    except Exception:
        return []


async def _fetch_plex_users() -> list[str]:
    """Fetch all managed/home users from plex.tv (includes users who haven't connected yet)."""
    if not PLEX_TOKEN:
        return []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://plex.tv/api/v2/home/users",
                headers={
                    "X-Plex-Token": PLEX_TOKEN,
                    "X-Plex-Client-Identifier": CLIENT_ID,
                    "Accept": "application/json",
                },
            )
            data = resp.json()
            return [u["title"] for u in data.get("users", []) if u.get("title")]
    except Exception:
        return []


async def _get_known_plex_names() -> list[str]:
    names: set[str] = set()

    # Live fetch from Plex — includes users who haven't watched anything yet
    for n in await _fetch_plex_users():
        names.add(n)

    # Also include names seen in watch history (covers cases where Plex API misses someone)
    for u in _read_user_mappings():
        for n in u.get("plex_names", []):
            names.add(n)
    try:
        tv = json.loads((DATA_DIR / "tv.json").read_text())
        movies = json.loads((DATA_DIR / "movies.json").read_text())
        for item in tv + movies:
            for user in item.get("watch_data", {}).keys():
                names.add(user)
    except Exception:
        pass
    return sorted(names)


@app.get("/api/settings/data")
async def settings_data():
    mappings = _read_user_mappings()
    seerr_users = await _fetch_seerr_users()
    all_plex_names = await _get_known_plex_names()
    mapped_plex = {n for u in mappings for n in u.get("plex_names", [])}
    unmapped_plex = [n for n in all_plex_names if n not in mapped_plex]
    return JSONResponse({
        "user_mappings": mappings,
        "seerr_users": seerr_users,
        "unmapped_plex_names": unmapped_plex,
        "all_plex_names": all_plex_names,
    })


@app.post("/api/settings/users")
async def settings_save_users(request: Request):
    body = await request.json()
    if not isinstance(body, list):
        return JSONResponse({"detail": "Expected a JSON array."}, status_code=400)
    if len(body) > 500:
        return JSONResponse({"detail": "Too many users."}, status_code=400)
    for item in body:
        if not isinstance(item, dict):
            return JSONResponse({"detail": "Each entry must be an object."}, status_code=400)
    _write_user_mappings(body)
    return JSONResponse({"ok": True})


# ── Settings + Setup pages ────────────────────────────────────────────────────

@app.get("/settings")
async def settings_page_route():
    html_path = Path(__file__).parent / "settings.html"
    if not html_path.exists():
        raise HTTPException(status_code=404)
    return HTMLResponse(html_path.read_text())


@app.get("/setup")
async def setup_page():
    if _is_setup_complete():
        return RedirectResponse("/", status_code=302)
    html_path = Path(__file__).parent / "setup.html"
    if not html_path.exists():
        return HTMLResponse("<h1>Setup page not found</h1>", status_code=500)
    return HTMLResponse(html_path.read_text())


def _read_config_env() -> dict:
    result = {}
    if CONFIG_ENV_PATH.exists():
        for line in CONFIG_ENV_PATH.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            result[k.strip()] = v.strip().strip('"').strip("'")
    return result


def _write_config_env(values: dict) -> None:
    url_keys = {
        "SONARR_URL", "RADARR_URL", "SEERR_URL", "PLEX_URL", "TAUTULLI_URL",
        "RUTORRENT_URL", "TRACKER_BLUTOPIA_URL", "TRACKER_BEYONDHD_URL", "TRACKER_PRIVATEHD_URL",
    }
    for k in url_keys:
        if k in values and values[k]:
            if not values[k].startswith(("http://", "https://")):
                raise ValueError(f"{k} must start with http:// or https://")
    CONFIG_ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_config_env()
    existing.update(values)
    lines = [f"{k}={v}" for k, v in existing.items()]
    CONFIG_ENV_PATH.write_text("\n".join(lines) + "\n")


@app.post("/api/setup/test-service")
async def setup_test_service(request: Request):
    body = await request.json()
    service = body.get("service", "").lower()
    url = (body.get("url") or "").rstrip("/")
    key = body.get("key", "")

    try:
        async with httpx.AsyncClient(verify=False, timeout=15, follow_redirects=True) as client:
            if service in ("sonarr", "radarr"):
                r = await client.get(f"{url}/api/v3/system/status", params={"apikey": key})
                r.raise_for_status()
                version = r.json().get("version", "unknown")
                return JSONResponse({"ok": True, "version": version})
            elif service in ("overseerr", "seerr"):
                r = await client.get(f"{url}/api/v1/status", headers={"X-Api-Key": key})
                r.raise_for_status()
                version = r.json().get("version", "unknown")
                return JSONResponse({"ok": True, "version": version})
            elif service == "tautulli":
                r = await client.get(f"{url}/api/v2", params={"apikey": key, "cmd": "get_tautulli_info"})
                r.raise_for_status()
                data = r.json()
                version = data.get("response", {}).get("data", {}).get("tautulli_version", "unknown")
                return JSONResponse({"ok": True, "version": version})
            elif service == "plex":
                r = await client.get(f"{url}/identity",
                                     headers={"X-Plex-Token": key, "Accept": "application/json"})
                r.raise_for_status()
                version = r.json().get("MediaContainer", {}).get("version", "unknown")
                return JSONResponse({"ok": True, "version": version})
            elif service == "tmdb":
                r = await client.get("https://api.themoviedb.org/3/configuration",
                                     params={"api_key": key})
                r.raise_for_status()
                return JSONResponse({"ok": True, "version": "API v3"})
            elif service == "rutorrent":
                import xmlrpc.client
                rpc_url = url.rstrip("/") + "/RPC2"
                auth_url = rpc_url
                if key:  # key = "username:password" format sent from UI
                    parts = key.split(":", 1)
                    if len(parts) == 2:
                        from urllib.parse import quote
                        proto, rest = rpc_url.split("://", 1)
                        auth_url = f"{proto}://{quote(parts[0])}:{quote(parts[1])}@{rest}"
                body = xmlrpc.client.dumps((), methodname="system.listMethods")
                r = await client.post(auth_url, content=body.encode(), headers={"Content-Type": "text/xml"})
                r.raise_for_status()
                return JSONResponse({"ok": True, "version": "rTorrent"})
            elif service in ("tracker-blutopia", "tracker-beyondhd"):
                username = body.get("username", "").strip()
                if username:
                    r = await client.get(
                        f"{url.rstrip('/')}/api/user/{username}",
                        params={"api_token": key},
                    )
                else:
                    r = await client.get(
                        f"{url.rstrip('/')}/api/torrents",
                        params={"api_token": key, "perPage": 1},
                    )
                if r.status_code == 401:
                    return JSONResponse({"ok": False, "error": "Invalid API key (401)"}, status_code=200)
                if r.status_code == 403:
                    return JSONResponse({"ok": False, "error": "Forbidden (403) — check API key permissions"}, status_code=200)
                if r.status_code == 404:
                    return JSONResponse({"ok": False, "error": "User not found (404) — check username"}, status_code=200)
                if not r.is_success:
                    return JSONResponse({"ok": False, "error": f"HTTP {r.status_code} from tracker"}, status_code=200)
                if username:
                    data = r.json().get("data", {})
                    up = data.get("uploaded") or 0
                    dn = data.get("downloaded") or 1
                    ratio = round(up / dn, 3) if dn else None
                    return JSONResponse({"ok": True, "version": f"ratio {ratio}" if ratio is not None else "Connected"})
                else:
                    return JSONResponse({"ok": True, "version": "API key valid (add username for ratio stats)"})
            elif service == "tracker-privatehd":
                # AvistaZ auth: POST credentials → bearer token → GET /api/v1/users/me
                username = body.get("username", "").strip()
                password = body.get("password", "").strip()
                pid = body.get("pid", "").strip()
                if not username or not password or not pid:
                    return JSONResponse({"ok": False, "error": "Username, password, and PID are all required"}, status_code=200)
                auth_r = await client.post(
                    f"{url.rstrip('/')}/api/v1/jackett/auth",
                    data={"username": username, "password": password, "pid": pid},
                )
                if auth_r.status_code in (401, 403):
                    return JSONResponse({"ok": False, "error": "Authentication failed — check username, password, and PID"}, status_code=200)
                auth_r.raise_for_status()
                token = auth_r.json().get("token")
                if not token:
                    return JSONResponse({"ok": False, "error": "No token returned from auth endpoint"}, status_code=200)
                me_r = await client.get(
                    f"{url.rstrip('/')}/api/v1/users/me",
                    params={"api_token": token},
                )
                me_r.raise_for_status()
                data = me_r.json().get("data", {})
                up = data.get("uploaded") or 0
                dn = data.get("downloaded") or 1
                ratio = round(up / dn, 3) if dn else None
                return JSONResponse({"ok": True, "version": f"ratio {ratio}" if ratio is not None else "Connected"})
            else:
                return JSONResponse({"ok": False, "error": "Unknown service"}, status_code=400)
    except httpx.ConnectError as e:
        return JSONResponse({"ok": False, "error": f"Could not connect: {e}"}, status_code=200)
    except httpx.TimeoutException:
        return JSONResponse({"ok": False, "error": "Request timed out"}, status_code=200)
    except Exception as e:
        print(f"Service test error ({type(e).__name__}): {e}")
        return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"}, status_code=200)


@app.post("/api/setup/plex-servers")
async def setup_plex_servers(request: Request):
    body = await request.json()
    token = body.get("token", "")
    if not token:
        return JSONResponse({"error": "token required"}, status_code=400)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://plex.tv/api/v2/resources",
                params={"includeHttps": 1, "includeRelay": 1, "includeIPv6": 0},
                headers={
                    "X-Plex-Client-Identifier": CLIENT_ID,
                    "X-Plex-Product": "Media Manager",
                    "X-Plex-Token": token,
                    "Accept": "application/json",
                },
            )
            resources = r.json()
        servers = []
        for res in resources:
            if not res.get("provides", "").startswith("server"):
                continue
            connections = [
                {"uri": c["uri"], "local": c.get("local", False), "relay": c.get("relay", False)}
                for c in res.get("connections", [])
            ]
            servers.append({
                "name": res.get("name", ""),
                "clientIdentifier": res.get("clientIdentifier", ""),
                "connections": connections,
            })
        return JSONResponse({"servers": servers})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)


@app.post("/api/setup/plex-libraries")
async def setup_plex_libraries(request: Request):
    body = await request.json()
    url = body.get("url", "").rstrip("/")
    token = body.get("token", "")
    if not url or not token:
        return JSONResponse({"error": "url and token required"}, status_code=400)
    try:
        async with httpx.AsyncClient(verify=False, timeout=10) as client:
            r = await client.get(
                f"{url}/library/sections",
                headers={"X-Plex-Token": token, "Accept": "application/json"},
            )
            r.raise_for_status()
            sections = r.json().get("MediaContainer", {}).get("Directory", [])
        libraries = [{"id": s["key"], "title": s["title"], "type": s["type"]} for s in sections]
        return JSONResponse({"libraries": libraries})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)


@app.post("/api/setup/save")
async def setup_save(request: Request):
    if _is_setup_complete():
        return JSONResponse({"detail": "Setup already complete."}, status_code=403)
    body = await request.json()
    sonarr_url = body.get("SONARR_URL", "").strip()
    radarr_url = body.get("RADARR_URL", "").strip()
    if not sonarr_url and not radarr_url:
        return JSONResponse({"detail": "At least one of Sonarr or Radarr is required."}, status_code=400)

    allowed_keys = {
        "SONARR_URL", "SONARR_API_KEY", "RADARR_URL", "RADARR_API_KEY",
        "SEERR_URL", "SEERR_API_KEY", "PLEX_URL", "PLEX_TOKEN",
        "TAUTULLI_URL", "TAUTULLI_API_KEY", "TMDB_API_KEY",
        "PLEX_TV_SECTIONS", "PLEX_MOVIE_SECTIONS", "STORAGE_CAPACITY_GB",
    }
    config_values = {k: v for k, v in body.items() if k in allowed_keys and v}
    config_values["SETUP_COMPLETE"] = "true"
    try:
        _write_config_env(config_values)
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=400)
    return JSONResponse({"ok": True})


@app.get("/api/settings/services")
async def settings_get_services():
    """Return current service configuration from config/.env."""
    cfg = _read_config_env()
    return JSONResponse({
        "SONARR_URL":                cfg.get("SONARR_URL", ""),
        "SONARR_API_KEY":            cfg.get("SONARR_API_KEY", ""),
        "RADARR_URL":                cfg.get("RADARR_URL", ""),
        "RADARR_API_KEY":            cfg.get("RADARR_API_KEY", ""),
        "SEERR_URL":                 cfg.get("SEERR_URL", ""),
        "SEERR_API_KEY":             cfg.get("SEERR_API_KEY", ""),
        "PLEX_URL":                  cfg.get("PLEX_URL", ""),
        "PLEX_TOKEN":                cfg.get("PLEX_TOKEN", ""),
        "PLEX_TV_SECTIONS":          cfg.get("PLEX_TV_SECTIONS", ""),
        "PLEX_MOVIE_SECTIONS":       cfg.get("PLEX_MOVIE_SECTIONS", ""),
        "TAUTULLI_URL":              cfg.get("TAUTULLI_URL", ""),
        "TAUTULLI_API_KEY":          cfg.get("TAUTULLI_API_KEY", ""),
        "TMDB_API_KEY":              cfg.get("TMDB_API_KEY", ""),
        "STORAGE_CAPACITY_GB":       cfg.get("STORAGE_CAPACITY_GB", ""),
        "VERIFY_SSL":                cfg.get("VERIFY_SSL", "true"),
        "RUTORRENT_URL":             cfg.get("RUTORRENT_URL", ""),
        "RUTORRENT_USERNAME":        cfg.get("RUTORRENT_USERNAME", ""),
        "RUTORRENT_PASSWORD":        cfg.get("RUTORRENT_PASSWORD", ""),
        "TRACKER_BLUTOPIA_URL":      cfg.get("TRACKER_BLUTOPIA_URL", ""),
        "TRACKER_BLUTOPIA_USERNAME": cfg.get("TRACKER_BLUTOPIA_USERNAME", ""),
        "TRACKER_BLUTOPIA_API_KEY":  cfg.get("TRACKER_BLUTOPIA_API_KEY", ""),
        "TRACKER_BEYONDHD_URL":      cfg.get("TRACKER_BEYONDHD_URL", ""),
        "TRACKER_BEYONDHD_USERNAME": cfg.get("TRACKER_BEYONDHD_USERNAME", ""),
        "TRACKER_BEYONDHD_API_KEY":  cfg.get("TRACKER_BEYONDHD_API_KEY", ""),
        "TRACKER_PRIVATEHD_URL":      cfg.get("TRACKER_PRIVATEHD_URL", ""),
        "TRACKER_PRIVATEHD_USERNAME": cfg.get("TRACKER_PRIVATEHD_USERNAME", ""),
        "TRACKER_PRIVATEHD_PASSWORD": cfg.get("TRACKER_PRIVATEHD_PASSWORD", ""),
        "TRACKER_PRIVATEHD_PID":      cfg.get("TRACKER_PRIVATEHD_PID", ""),
    })


def _load_job_runs() -> dict:
    """Read data/job_runs.json written by run.py after each job."""
    path = DATA_DIR / "job_runs.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


@app.get("/api/settings/jobs")
async def settings_get_jobs():
    """List pipeline jobs with running state and last-run time."""
    async with _jobs_lock:
        running_snapshot = dict(_running_jobs)
    job_runs = _load_job_runs()
    jobs = []
    for j in PIPELINE_JOBS:
        jid = j["id"]
        jobs.append({
            **j,
            "running": running_snapshot.get(jid, False),
            "lastRun": job_runs.get(jid),
        })
    return JSONResponse(jobs)


@app.post("/api/settings/jobs/{job_id}/run")
async def settings_run_job(job_id: str):
    """Trigger a pipeline job in the background."""
    if job_id not in {j["id"] for j in PIPELINE_JOBS}:
        return JSONResponse({"error": "Unknown job."}, status_code=404)
    async with _jobs_lock:
        if _running_jobs.get(job_id):
            return JSONResponse({"error": "Job already running."}, status_code=409)
        _running_jobs[job_id] = True

    async def _run():
        try:
            script = PROJECT_DIR / "scripts" / "run.py"
            proc = await asyncio.create_subprocess_exec(
                "python", str(script), job_id,
                cwd=str(PROJECT_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            await proc.communicate()
        finally:
            async with _jobs_lock:
                _running_jobs[job_id] = False

    asyncio.create_task(_run())
    return JSONResponse({"ok": True})


DEFAULT_CRON = "0 */6 * * *"


@app.get("/api/settings/schedule")
async def settings_get_schedule():
    """Return the current pipeline cron schedule."""
    cron_expr = _read_config_env().get("CRON_SCHEDULE", DEFAULT_CRON).strip() or DEFAULT_CRON
    next_run: str | None = None
    job = _scheduler.get_job("pipeline")
    if job and job.next_run_time:
        next_run = job.next_run_time.isoformat()
    return JSONResponse({
        "cron": cron_expr,
        "nextRun": next_run,
    })


@app.post("/api/settings/schedule")
async def settings_save_schedule(request: Request):
    """Update the pipeline cron schedule. Empty string resets to default."""
    body = await request.json()
    cron_expr = body.get("cron", "").strip() or DEFAULT_CRON
    try:
        CronTrigger.from_crontab(cron_expr, timezone=timezone.utc)
    except Exception as e:
        return JSONResponse({"error": f"Invalid cron expression: {e}"}, status_code=400)
    cfg = _read_config_env()
    cfg["CRON_SCHEDULE"] = cron_expr
    _write_config_env(cfg)
    _schedule_pipeline(cron_expr)
    return JSONResponse({"ok": True})


@app.post("/api/settings/config")
async def settings_save_config(request: Request):
    body = await request.json()
    allowed_keys = {
        "SONARR_URL", "SONARR_API_KEY", "RADARR_URL", "RADARR_API_KEY",
        "SEERR_URL", "SEERR_API_KEY", "PLEX_URL", "PLEX_TOKEN",
        "TAUTULLI_URL", "TAUTULLI_API_KEY", "TMDB_API_KEY",
        "PLEX_TV_SECTIONS", "PLEX_MOVIE_SECTIONS", "STORAGE_CAPACITY_GB", "VERIFY_SSL",
        "RUTORRENT_URL", "RUTORRENT_USERNAME", "RUTORRENT_PASSWORD",
        "TRACKER_BLUTOPIA_URL", "TRACKER_BLUTOPIA_USERNAME", "TRACKER_BLUTOPIA_API_KEY",
        "TRACKER_BEYONDHD_URL", "TRACKER_BEYONDHD_USERNAME", "TRACKER_BEYONDHD_API_KEY",
        "TRACKER_PRIVATEHD_URL", "TRACKER_PRIVATEHD_USERNAME",
        "TRACKER_PRIVATEHD_PASSWORD", "TRACKER_PRIVATEHD_PID",
    }
    config_values = {k: v for k, v in body.items() if k in allowed_keys}
    try:
        _write_config_env(config_values)
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=400)
    return JSONResponse({"ok": True})


# ── Tautulli webhook ──────────────────────────────────────────────────────────

async def _plex_hdr_type(rating_key: str) -> str | None:
    """Query Plex for HDR type: 'DV', 'HDR10', 'HLG', or None for SDR."""
    if not PLEX_SERVER_URL or not PLEX_TOKEN or not rating_key:
        return None
    try:
        async with httpx.AsyncClient(verify=False, timeout=5) as client:
            resp = await client.get(
                f"{PLEX_SERVER_URL}/library/metadata/{rating_key}",
                headers={"X-Plex-Token": PLEX_TOKEN, "Accept": "application/json"},
            )
            streams = (
                resp.json()
                .get("MediaContainer", {})
                .get("Metadata", [{}])[0]
                .get("Media", [{}])[0]
                .get("Part", [{}])[0]
                .get("Stream", [])
            )
        for s in streams:
            if s.get("streamType") != 1:
                continue
            if s.get("DOVIProfile"):
                return "DV"
            trc = s.get("colorTrc", "")
            if trc == "smpte2084":
                return "HDR10"
            if trc == "arib-std-b67":
                return "HLG"
        return None
    except Exception:
        return None


@app.post("/api/tautulli/webhook")
async def tautulli_webhook(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    media = body.get("media", {})
    playback = body.get("playback", {})
    src = body.get("source_quality", {})
    stream = body.get("stream_quality", {})
    client = body.get("client", {})

    rating_key = media.get("rating_key", "")
    tmdb_id_raw = media.get("tmdb_id")
    try:
        tmdb_id = int(tmdb_id_raw) if tmdb_id_raw else None
    except (ValueError, TypeError):
        tmdb_id = None

    event = body.get("event")
    # Only worth the extra Plex round-trip on the initial play — pause/resume/
    # stop/buffer events describe the same stream and don't change HDR type.
    hdr_type = await _plex_hdr_type(rating_key) if event == "play" else None

    progress_percent_raw = playback.get("progress_percent")
    try:
        progress_percent = float(progress_percent_raw) if progress_percent_raw not in (None, "") else None
    except (ValueError, TypeError):
        progress_percent = None

    async with aiosqlite.connect(PLAYS_DB) as db:
        await db.execute("""
            INSERT INTO plays (
                event, event_at, session_key, rating_key, tmdb_id, media_type,
                transcode_decision, video_decision, audio_decision, subtitle_decision,
                quality_profile, progress_percent, view_offset,
                src_container, src_video_codec, src_video_bitrate, src_video_resolution,
                src_video_bit_depth, src_hdr_type, src_audio_codec, src_audio_channels,
                stream_container, stream_video_codec, stream_video_bitrate,
                stream_video_resolution, stream_audio_codec, stream_audio_channels,
                client_user, client_friendly_name, client_platform, client_platform_version,
                client_product, client_player, client_device
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            event,
            body.get("event_at"),
            body.get("session_key"),
            rating_key,
            tmdb_id,
            media.get("media_type"),
            playback.get("transcode_decision"),
            playback.get("video_decision"),
            playback.get("audio_decision"),
            playback.get("subtitle_decision"),
            playback.get("quality_profile"),
            progress_percent,
            playback.get("view_offset"),
            src.get("container"),
            src.get("video_codec"),
            src.get("video_bitrate"),
            src.get("video_resolution"),
            src.get("video_bit_depth"),
            hdr_type,
            src.get("audio_codec"),
            src.get("audio_channels"),
            stream.get("stream_container"),
            stream.get("stream_video_codec"),
            stream.get("stream_video_bitrate"),
            stream.get("stream_video_resolution"),
            stream.get("stream_audio_codec"),
            stream.get("stream_audio_channels"),
            client.get("user"),
            client.get("friendly_name"),
            client.get("platform"),
            client.get("platform_version"),
            client.get("product"),
            client.get("player"),
            client.get("device"),
        ))
        await db.commit()

    return JSONResponse({"ok": True})


# ── Static file serving (catch-all — must be last) ───────────────────────────

@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


@app.get("/{path:path}")
async def serve_static(path: str):
    if not path:
        target = PUBLIC_DIR / "index.html"
    else:
        target = (PUBLIC_DIR / path).resolve()
        if not str(target).startswith(str(PUBLIC_DIR.resolve())):
            raise HTTPException(status_code=404)
    if target.is_dir():
        target = target / "index.html"
    if not target.exists():
        raise HTTPException(status_code=404)
    return FileResponse(target)
