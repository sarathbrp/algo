# Shortcuts from repo root: `make loop-paper`, `make help`
PYTHON ?= python3

.PHONY: help loop loop-paper loop-live loop-v api seed-users test test-cov

help:
	@echo "AlgoSphere — quick commands (use PYTHON=python3.12 to pick interpreter):"
	@echo ""
	@echo "  make loop-paper   Alpaca loop, paper (default)"
	@echo "  make loop-live    Alpaca loop, LIVE — real money"
	@echo "  make loop-v       loop + --verbose"
	@echo "  make api          run API locally"
	@echo "  make seed-users   seed runtime users into the DB"
	@echo "  make test         run test suite"
	@echo "  make test-cov     run tests with coverage gate"
	@echo ""
	@echo "Same via:  ./bin/algo <command>   (see ./bin/algo help)"

loop loop-paper:
	$(PYTHON) scripts/run_alpaca_loop.py --paper

loop-live:
	$(PYTHON) scripts/run_alpaca_loop.py --live

loop-v:
	$(PYTHON) scripts/run_alpaca_loop.py --paper --verbose

api:
	$(PYTHON) scripts/run_api.py

seed-users:
	$(PYTHON) scripts/seed_users.py

test:
	PYTHONPATH=. $(PYTHON) -m pytest tests -v

test-cov:
	PYTHONPATH=. $(PYTHON) -m pytest tests -v --cov=src --cov-report=term-missing --cov-fail-under=80
