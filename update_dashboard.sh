#!/bin/sh
. "$HOME/mediaenv/bin/activate"
export $(grep -v '^#' "$HOME/homepage/config/homepage.env" | xargs)

# New platform
python "$HOME/media-manager/scripts/run.py"