# Makefile — start the BOM Review Assistant (backend + frontend).
#
# Works on Windows (GNU make + cmd.exe, e.g. ezwinports make) and on Unix.
#
#   make dev        # start BOTH (backend :8011 + frontend :3010) in parallel
#   make backend    # FastAPI on http://127.0.0.1:8011  (local SQLite dev mode)
#   make frontend   # Next.js console on http://localhost:3010 (webpack)
#   make install    # install backend (venv) + frontend (npm) dependencies
#   make test       # run the backend test suite

BACKEND_PORT := 8011
FRONTEND_PORT := 3010

ifeq ($(OS),Windows_NT)
    # ezwinports make runs recipes through cmd.exe; force it so set/&& work
    # even when a stray sh.exe is on PATH.
    SHELL := cmd.exe
    .SHELLFLAGS := /c
    VENV_PY := .venv\Scripts\python.exe
    BACKEND_ENV := set "BOM_ALLOW_SQLITE=1" && set "BOM_DB_PATH=backend/data/bom_review.db" && set "PYTHONIOENCODING=utf-8" &&
else
    VENV_PY := .venv/bin/python
    BACKEND_ENV := BOM_ALLOW_SQLITE=1 BOM_DB_PATH=backend/data/bom_review.db PYTHONIOENCODING=utf-8
endif

.DEFAULT_GOAL := help
.PHONY: help dev backend frontend install test

help:
	@echo make dev       - start backend :$(BACKEND_PORT) + frontend :$(FRONTEND_PORT) together
	@echo make backend   - start FastAPI backend only
	@echo make frontend  - start Next.js frontend only
	@echo make install   - install backend + frontend dependencies
	@echo make test      - run backend tests

dev:
	@echo Starting backend :$(BACKEND_PORT) and frontend :$(FRONTEND_PORT) - Ctrl+C stops both
	@$(MAKE) -j2 backend frontend

backend:
	$(BACKEND_ENV) $(VENV_PY) -m uvicorn app.main:app --app-dir backend --port $(BACKEND_PORT)

frontend:
	cd frontend && npm run dev -- --webpack --port $(FRONTEND_PORT)

install:
	$(VENV_PY) -m pip install -r backend/requirements.txt
	cd frontend && npm install

test:
	$(BACKEND_ENV) $(VENV_PY) -m pytest backend/tests -q
