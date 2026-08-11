#!/bin/sh
set -e

# Idempotent: creates missing tables/columns without touching existing data
# (README §9 "Note on init_db").
python -m app.db.init_db

# sync_sql is idempotent (upsert by slug) and needs no API key, so it's safe
# to run on every start — keeps `products` current if data/data_1 changed.
python -m app.catalog.sync_sql data/data_1

exec "$@"
