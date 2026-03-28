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

echo "==> Creating database if it does not exist..."
python -c "
import pymysql
from urllib.parse import urlparse
import os
dsn = os.environ.get('TIDB_DSN', '')
if dsn:
    u = urlparse(dsn.replace('mysql+pymysql://', 'mysql://'))
    db_name = (u.path or '/algosphere').lstrip('/')
    conn = pymysql.connect(
        host=u.hostname or 'localhost',
        port=u.port or 4000,
        user=u.username or 'root',
        password=u.password or '',
    )
    with conn.cursor() as cur:
        cur.execute(f'CREATE DATABASE IF NOT EXISTS \`{db_name}\`')
    conn.close()
    print(f'    Database \"{db_name}\" ready')
"

echo "==> Running Alembic migrations..."
alembic upgrade head

echo "==> Starting API server..."
exec "$@"
