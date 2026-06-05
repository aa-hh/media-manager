# Media Manager

A visual media intelligence dashboard for Whatbox that combines Sonarr, Radarr, TMDB metadata, and storage analytics into a poster-driven experience.

---

# Project Goals

Media Manager answers a simple question:

> Why is my storage full?

Traditional disk usage tools only show folders and files.

Media Manager presents storage usage through the lens of your media library:

- Movies
- TV Shows
- Collections
- Genres
- Ratings
- Quality Profiles
- Watch History (future)

The result is a visual dashboard that helps identify:

- Largest storage consumers
- Low-value content
- Duplicate quality upgrades
- Unwatched content
- Space recovery opportunities

---

# Architecture

text Sonarr    \     \      ---> media_check.py ---> index.html ---> Nginx ---> Browser     /    / Radarr  TMDB   |   +--> Poster Enrichment 

Important:

The Python script does all processing.

The web server only serves the generated HTML.

This keeps the application fast and lightweight.

---

# Directory Structure

text ~/media-manager/  ├── cache/ │   ├── ignore_list.json │   └── poster_cache.json │ ├── logs/ │   └── media_check.log │ ├── public/ │   └── index.html │ ├── scripts/ │   └── media_check.py │ ├── docker-compose.yml ├── update_dashboard.sh └── README.md 

---

# Requirements

## Applications

Required:

- Sonarr
- Radarr
- Podman
- Podman Compose
- Python 3

Optional:

- Plex
- Tautulli

---

# Environment Variables

Media Manager loads credentials from:

text ~/homepage/config/homepage.env 

Example:

env SONARR_URL=https://sonarr.box.example.com RADARR_URL=https://radarr.box.example.com  HOMEPAGE_VAR_SONARR_API_KEY=xxxxxxxx HOMEPAGE_VAR_RADARR_API_KEY=xxxxxxxx  TMDB_API_KEY=xxxxxxxx 

---

# Python Environment

Create a dedicated virtual environment.

bash /usr/bin/virtualenv -p python3 --system-site-packages ~/mediaenv 

Activate:

bash source ~/mediaenv/bin/activate 

Install dependencies:

bash pip install requests 

Verify:

bash python --version pip list 

Deactivate:

bash deactivate 

---

# Dashboard Generator

Main script:

text ~/media-manager/scripts/media_check.py 

Purpose:

- Pull Sonarr data
- Pull Radarr data
- Fetch TMDB posters
- Build storage statistics
- Generate dashboard HTML
- Update caches
- Write logs

---

# Wrapper Script

File:

text ~/media-manager/update_dashboard.sh 

Contents:

bash #!/bin/sh  . "$HOME/mediaenv/bin/activate"  export $(grep -v '^#' "$HOME/homepage/config/homepage.env" | xargs)  python "$HOME/media-manager/scripts/media_check.py" 

Make executable:

bash chmod +x ~/media-manager/update_dashboard.sh 

---

# Manual Dashboard Generation

Run:

bash ~/media-manager/update_dashboard.sh 

Expected output:

text [2026-06-05 04:22:01] === Media check started === [2026-06-05 04:22:03] Fetched 214 series from Sonarr [2026-06-05 04:22:06] Fetched 1387 movies from Radarr [2026-06-05 04:22:08] Dashboard generated [2026-06-05 04:22:08] Dashboard generation completed in 7.3 seconds 

---

# Logging

Logs are stored at:

text ~/media-manager/logs/media_check.log 

View:

bash cat ~/media-manager/logs/media_check.log 

Recent entries:

bash tail -50 ~/media-manager/logs/media_check.log 

Live monitoring:

bash tail -f ~/media-manager/logs/media_check.log 

---

# Poster Cache

File:

text cache/poster_cache.json 

Purpose:

- Reduce TMDB requests
- Faster generation
- Prevent API rate limits

---

# Ignore List

File:

text cache/ignore_list.json 

Example:

json [   "tv:123",   "movie:456" ] 

Ignored items do not appear in reports.

Media Manager automatically removes entries that no longer exist.

---

# Nginx Web Server

Media Manager is served using a lightweight Nginx container.

Generated dashboard:

text public/index.html 

is mounted into the container.

---

# docker-compose.yml

Example:

yaml services:   media-manager:     image: docker.io/library/nginx:alpine     container_name: media-manager      ports:       - "10450:80"      volumes:       - ./public:/usr/share/nginx/html:ro      restart: unless-stopped 

Change:

yaml 10450 

to any available Whatbox port.

---

# Starting The Container

Navigate to the project:

bash cd ~/media-manager 

Start:

bash podman-compose up -d 

Verify:

bash podman ps 

Expected:

text media-manager Up 0.0.0.0:10450->80/tcp 

---

# Restarting

After changing configuration:

bash cd ~/media-manager  podman-compose down  podman-compose up -d 

---

# Checking Logs

Container logs:

bash podman logs media-manager 

Live:

bash podman logs -f media-manager 

---

# Testing The Site

Generate dashboard:

bash ~/media-manager/update_dashboard.sh 

Open:

text https://shenzhou.whatbox.ca:10450 

or

text https://your-domain.example.com:10450 

You should see:

- Storage chart
- Movie posters
- TV posters
- Storage statistics

---

# Homepage Integration

Add a custom service:

yaml - Media:     - Media Manager:         icon: mdi-chart-box         href: https://your-domain.example.com:10450         description: Storage Analytics 

This creates a clickable tile inside Homepage.

---

# Automation

Edit cron:

bash crontab -e 

Example:

cron 0 4 * * * /home/USERNAME/media-manager/update_dashboard.sh >> /home/USERNAME/media-manager/logs/cron.log 2>&1 

Runs every day at:

text 04:00 

---

# Useful Commands

Generate dashboard:

bash ~/media-manager/update_dashboard.sh 

Check logs:

bash tail -f ~/media-manager/logs/media_check.log 

Check container:

bash podman ps 

Restart container:

bash podman restart media-manager 

Stop container:

bash podman stop media-manager 

Start container:

bash podman start media-manager 

---

# Future Roadmap

## Version 1.1

- Storage totals
- Largest shows
- Largest movies
- Recently added

## Version 1.2

- Genre analysis
- Collection analysis
- TMDB ratings

## Version 2.0

- Plex integration
- Tautulli integration
- Watch counts
- Last watched data

## Version 3.0

- Storage value score
- Space recovery recommendations
- Interactive drilldowns
- Collection management

---

# Design Philosophy

Media Manager is not a file browser.

It is a media intelligence platform.

The objective is to transform storage consumption into actionable information using metadata, ratings, collections, posters, and viewing behavior.