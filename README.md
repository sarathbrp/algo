# AlgoSphere

AlgoSphere is a Dockerized trading application built around:

- a FastAPI backend
- a dedicated Alpaca worker loop
- DB-backed user, account, position, and snapshot state
- shared market-data infrastructure
- a React frontend

The current architecture is documented in [ascii-workflow-mvp.md](/Users/spadakan/Documents/personal/Alpaca-algorithm/algo/ascii-workflow-mvp.md).

## Runtime

Core services:

- `api`: [src/api/main.py](/Users/spadakan/Documents/personal/Alpaca-algorithm/algo/src/api/main.py)
- `worker`: [scripts/run_alpaca_loop.py](/Users/spadakan/Documents/personal/Alpaca-algorithm/algo/scripts/run_alpaca_loop.py)
- `db models/repos`: [src/db](/Users/spadakan/Documents/personal/Alpaca-algorithm/algo/src/db)
- `market data`: [src/market_data](/Users/spadakan/Documents/personal/Alpaca-algorithm/algo/src/market_data)
- `frontend`: [frontend/src](/Users/spadakan/Documents/personal/Alpaca-algorithm/algo/frontend/src)

Production runtime is DB-first:

- active users/accounts load from the database
- tracked positions persist in the database
- worker health and reconciliation persist in the database
- YAML/file-based paths remain only as compatibility fallbacks

## Local Commands

```bash
make loop-paper
make loop-live
make loop-v
make api
make seed-users
make test
make test-cov
```

Equivalent helper commands are also available in [bin/algo](/Users/spadakan/Documents/personal/Alpaca-algorithm/algo/bin/algo).

## Docker

Main files:

- [Dockerfile](/Users/spadakan/Documents/personal/Alpaca-algorithm/algo/Dockerfile)
- [docker-compose.yml](/Users/spadakan/Documents/personal/Alpaca-algorithm/algo/docker-compose.yml)
- [docker/entrypoint-api.sh](/Users/spadakan/Documents/personal/Alpaca-algorithm/algo/docker/entrypoint-api.sh)

The current compose setup starts:

- `tidb`
- `api`
- `frontend`

## Database

Schema and migrations live in:

- [src/db/models.py](/Users/spadakan/Documents/personal/Alpaca-algorithm/algo/src/db/models.py)
- [alembic](/Users/spadakan/Documents/personal/Alpaca-algorithm/algo/alembic)

Recent runtime additions include:

- broker accounts
- user/account settings
- DB-backed tracked positions
- worker status and reconciliation support

## Frontend

The frontend is a Vite/React app in [frontend](/Users/spadakan/Documents/personal/Alpaca-algorithm/algo/frontend).

Primary user flows:

- login/signup
- onboarding
- dashboard
- trades
- settings

## Tests

Run the full suite:

```bash
PYTHONPATH=. python -m pytest tests -v
PYTHONPATH=. python -m pytest tests --cov=src --cov-report=term-missing
```
