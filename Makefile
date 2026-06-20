PYTHON ?= python3
PIP ?= $(PYTHON) -m pip

export PYTHONPATH := scripts:$(PYTHONPATH)
export MANDEM_DATA_DIR ?= $(CURDIR)/.mandem-data

.PHONY: install test smoke db-init mcp clean

install:
	$(PIP) install -r requirements.txt

test:
	$(PYTHON) -m pytest -q scripts/tests

smoke:
	$(PYTHON) scripts/smoke.py

db-init:
	$(PYTHON) scripts/mandem_db.py footy init

mcp:
	$(PYTHON) scripts/mandem_mcp.py

clean:
	rm -rf .pytest_cache .mandem-data
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
