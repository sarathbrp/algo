#!/bin/bash
set -e

echo "==> Waiting for TiDB to be ready..."
for i in $(seq 1 30); do
    if python -c "
import pymysql
from urllib.parse import urlparse
import os
dsn = os.environ.get('TIDB_DSN', '')
if not dsn:
    exit(0)
u = urlparse(dsn.replace('mysql+pymysql://', 'mysql://'))
pymysql.connect(
    host=u.hostname or 'localhost',
    port=u.port or 4000,
    user=u.username or 'root',
    password=u.password or '',
    connect_timeout=3,
)
" 2>/dev/null; then
        echo "==> TiDB is ready"
        break
    fi
    echo "    Waiting for TiDB... ($i/30)"
    sleep 2
done

echo "==> Running Alembic migrations..."
alembic upgrade head

echo "==> Starting trading worker loop..."
# The trading loop exits when market closes. This wrapper restarts it
# each cycle so the container stays alive and catches the next session.
while true; do
    echo "$(date '+%Y-%m-%d %H:%M:%S') — Starting trading loop..."
    python scripts/run_alpaca_loop.py "$@" || true
    echo "$(date '+%Y-%m-%d %H:%M:%S') — Trading loop exited. Sleeping 60s before restart..."
    sleep 60
done
