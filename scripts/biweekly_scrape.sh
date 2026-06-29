#!/usr/bin/env bash
# Bi-weekly Homelytics refresh: re-scrape the projects already in the DB and
# record an inventory snapshot for each (drives the "flats booked over time"
# trend). Invoked by the launchd agent on the 1st & 15th; safe to run by hand.
set -euo pipefail

REPO="/Users/tgidwani/homelytics"

# launchd runs with a minimal PATH — point at the Python that has the deps and
# at the Homebrew PostgreSQL client.
export PATH="/Library/Frameworks/Python.framework/Versions/3.10/bin:/opt/homebrew/opt/postgresql@15/bin:/usr/bin:/bin"

cd "$REPO"
echo "===== biweekly scrape started $(date '+%Y-%m-%d %H:%M:%S') ====="
python3 main.py --refresh-existing --no-complaints
echo "===== biweekly scrape finished $(date '+%Y-%m-%d %H:%M:%S') ====="
