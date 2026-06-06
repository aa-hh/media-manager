# Media Manager

A self-hosted media intelligence dashboard for seedboxes and home servers. Connects to your existing Sonarr, Radarr, Plex, Tautulli, and Overseerr stack and turns them into a single visual interface for understanding your library, your storage, and how your users are actually watching.

---

## What it does

### Library browser
Poster-driven browsing of your full TV and movie library. Each title shows storage size, TMDB rating, genres, watch status per user, and deletion score. Backed by Sonarr and Radarr, enriched with TMDB metadata.

### Per-user watch history
See exactly who has watched what, when they last watched it, how far through a series they are, and how many episodes or plays they have. Sourced from Plex and/or Tautulli — if both are connected, the richer data source wins per user per title.

### User profiles
Each user gets a profile page showing their total watch count, storage they've requested, storage they've actually watched, and their full request and watch history.

### Request tracking
Pulls all requests and watchlists from Overseerr/Seerr. Shows who requested each title, whether they've watched it, and flags content that was requested but never touched.

### Deletion scoring
Every title gets a 0–100 deletion score based on: storage size, last watch date, play count, number of watchers, TMDB rating, and whether it was ever watched at all. Surfaces the clearest candidates for removal on the **Free Space** page.

### Scheduled deletion + Plex labels
From the Free Space page you can schedule items for deletion in 14 days or delete them immediately. Scheduling adds a `Leaving Soon` label to the item in Plex, which Kometa can read to display a banner overlay on the poster so users know content is going away. The label is automatically removed when the item is deleted or the schedule is cancelled.

→ [Kometa overlay setup guide](docs/kometa-overlays.md)

### Storage forecasting
Tracks library size over time and projects how long until you run out of space based on recent growth rate.

### Service status
Health dashboard for all connected services — current version, latest available version, and whether updates are available.

---

## Services

| Service | What it enables | Required? |
|---------|----------------|-----------|
| **Sonarr** | TV library, episode details, TV storage stats | One of Sonarr/Radarr |
| **Radarr** | Movie library, movie storage stats | One of Sonarr/Radarr |
| **TMDB** | Posters, ratings, overviews for all media | Strongly recommended |
| **Plex** | Watch history, per-user data, user profiles | Optional |
| **Tautulli** | Enhanced watch stats (more accurate than Plex alone) | Optional |
| **Overseerr / Seerr** | Request tracking, watchlists | Optional |

Minimum viable setup: **Sonarr or Radarr + TMDB**.

Multiple Sonarr and Radarr instances are supported — useful if you split TV and anime, or movies across multiple root folders with separate instances.

---

## Installation

### 1. Clone the repo

```bash
git clone https://github.com/aa-hh/media-manager
cd media-manager
```

### 2. Run the install script

```bash
chmod +x install.sh
./install.sh
```

This will:
- Check Python 3.9+ is available
- Create a `.venv` virtual environment and install dependencies
- Ask for a port number (default: 10400)
- Generate a secret key and Plex client ID
- Write an initial `config/.env`

### 3. Start the container

```bash
./bin/deploy.sh
```

Requires Docker or Podman with Compose.

### 4. Complete setup in your browser

Visit `http://your-server:10400` — you'll be redirected to the setup wizard automatically.

The wizard walks you through:
1. **Sign in with Plex** — authenticates you and lists your Plex servers to pick from
2. **Service matrix** — toggle which services you want to connect and see what each one enables
3. **Credentials** — enter URLs and API keys with live connection tests for each
4. **Plex library assignment** — pick which Plex library sections are TV and which are Movies
5. **Storage capacity** — optional, used for forecasting charts
6. **Save** — writes your config and redirects to the dashboard

---

## Keeping data fresh

The collection pipeline is a Python script that pulls data from all connected services and generates the dashboard HTML. Run it manually or schedule it:

```bash
# Full run (collect + generate)
./update_dashboard.sh

# Or separately
.venv/bin/python scripts/run.py collect
.venv/bin/python scripts/run.py generate
```

**Recommended cron schedule** — add to `crontab -e`:

```cron
# Refresh dashboard every day at 4am
0 4 * * * /path/to/media-manager/update_dashboard.sh >> /path/to/media-manager/logs/cron.log 2>&1

# Keep container running (restart if it dies)
@reboot /path/to/media-manager/bin/media-manager-ensure-running.sh >> /path/to/media-manager/logs/ensure.log 2>&1
*/5 * * * * /path/to/media-manager/bin/media-manager-ensure-running.sh >> /path/to/media-manager/logs/ensure.log 2>&1
```

---

## Configuration

All configuration lives in `config/.env`. The setup wizard writes this file — you can also edit it directly.

See [`config/.env.example`](config/.env.example) for all supported variables with documentation.

Key variables:

```env
PORT=10400                    # host port the app listens on
AUTH_SECRET_KEY=...           # generated by install.sh
PLEX_CLIENT_ID=...            # generated by install.sh

SONARR_URL=https://sonarr.example.com
SONARR_API_KEY=...
RADARR_URL=https://radarr.example.com
RADARR_API_KEY=...
TMDB_API_KEY=...

PLEX_URL=https://plex.example.com:32400
PLEX_TOKEN=...
PLEX_TV_SECTIONS=1            # comma-separated if multiple
PLEX_MOVIE_SECTIONS=2

TAUTULLI_URL=http://localhost:8181
TAUTULLI_API_KEY=...

SEERR_URL=https://overseerr.example.com
SEERR_API_KEY=...

STORAGE_CAPACITY_GB=4096      # for forecasting charts
SETUP_COMPLETE=true
```

Multiple Sonarr/Radarr instances use comma-separated values:
```env
SONARR_URL=https://sonarr.example.com,https://anime.example.com
SONARR_API_KEY=key1,key2
```

---

## Architecture

```
Sonarr ─┐
Radarr ─┤
TMDB   ─┤──► scripts/run.py ──► data/*.json ──► scripts/generate.py ──► public/
Plex   ─┤                                                                    │
Tautulli┤                                                              auth_proxy
Seerr  ─┘                                                             (FastAPI)
                                                                            │
                                                                         Browser
```

- **Collection** (`scripts/run.py collect`) — fetches from all services, enriches data, scores for deletion, writes JSON to `data/`
- **Generation** (`scripts/run.py generate`) — renders Jinja2 templates into static HTML in `public/`
- **Auth proxy** (`auth_proxy/`) — FastAPI app that handles Plex OAuth login, serves the static files, and exposes settings/setup APIs
- **Container** — the auth proxy runs in Docker/Podman via `compose.yaml`; the collection script runs on the host on a cron

---

## User mapping

If your Plex display names don't match your Overseerr usernames, edit `config/users.json` to link them:

```json
[
  {
    "name": "alice",
    "seerr_id": 1,
    "plex_names": ["Alice", "alice@plex"]
  }
]
```

This can also be managed through the Settings page in the dashboard.

---

## Logs

```bash
# Collection logs
tail -f logs/media_manager.log

# Container logs
podman logs -f media-manager
```
