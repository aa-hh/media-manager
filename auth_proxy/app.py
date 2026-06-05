import os
import json
from pathlib import Path
from secrets import compare_digest
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse, FileResponse
from fastapi import HTTPException
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

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
CONFIG_ENV_PATH = CONFIG_DIR / ".env"
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


@app.on_event("startup")
async def startup():
    global server_machine_id
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


# ── Setup wizard ─────────────────────────────────────────────────────────────

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
    url_keys = {"SONARR_URL", "RADARR_URL", "SEERR_URL", "PLEX_URL", "TAUTULLI_URL"}
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
        async with httpx.AsyncClient(verify=False, timeout=8) as client:
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
            else:
                return JSONResponse({"ok": False, "error": "Unknown service"}, status_code=400)
    except Exception as e:
        print(f"Service test error: {e}")
        return JSONResponse({"ok": False, "error": "Connection failed — check the URL and API key."}, status_code=200)


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
        "SONARR_URL":          cfg.get("SONARR_URL", ""),
        "SONARR_API_KEY":      cfg.get("SONARR_API_KEY", ""),
        "RADARR_URL":          cfg.get("RADARR_URL", ""),
        "RADARR_API_KEY":      cfg.get("RADARR_API_KEY", ""),
        "SEERR_URL":           cfg.get("SEERR_URL", ""),
        "SEERR_API_KEY":       cfg.get("SEERR_API_KEY", ""),
        "PLEX_URL":            cfg.get("PLEX_URL", ""),
        "PLEX_TOKEN":          cfg.get("PLEX_TOKEN", ""),
        "PLEX_TV_SECTIONS":    cfg.get("PLEX_TV_SECTIONS", ""),
        "PLEX_MOVIE_SECTIONS": cfg.get("PLEX_MOVIE_SECTIONS", ""),
        "TAUTULLI_URL":        cfg.get("TAUTULLI_URL", ""),
        "TAUTULLI_API_KEY":    cfg.get("TAUTULLI_API_KEY", ""),
        "TMDB_API_KEY":        cfg.get("TMDB_API_KEY", ""),
        "STORAGE_CAPACITY_GB": cfg.get("STORAGE_CAPACITY_GB", ""),
        "VERIFY_SSL":          cfg.get("VERIFY_SSL", "true"),
    })


@app.post("/api/settings/config")
async def settings_save_config(request: Request):
    body = await request.json()
    allowed_keys = {
        "SONARR_URL", "SONARR_API_KEY", "RADARR_URL", "RADARR_API_KEY",
        "SEERR_URL", "SEERR_API_KEY", "PLEX_URL", "PLEX_TOKEN",
        "TAUTULLI_URL", "TAUTULLI_API_KEY", "TMDB_API_KEY",
        "PLEX_TV_SECTIONS", "PLEX_MOVIE_SECTIONS", "STORAGE_CAPACITY_GB", "VERIFY_SSL",
    }
    config_values = {k: v for k, v in body.items() if k in allowed_keys}
    try:
        _write_config_env(config_values)
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=400)
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
