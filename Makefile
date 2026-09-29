# Makefile — start the BOM Review Assistant (backend + frontend).
#
# Works on Windows (GNU make + cmd.exe, e.g. ezwinports make) and on Unix.
#
#   make dev        # start BOTH (backend :8012 + frontend :3011) in parallel
#   make backend    # FastAPI on http://127.0.0.1:8012  (Supabase from backend/.env)
#   make frontend   # Next.js console on http://localhost:3011 (webpack)
#   make install    # install backend (venv) + frontend (npm) dependencies
#   make test       # run the backend test suite

# Development ports are separate from Docker's frontend :3010 and backend :8011.
BACKEND_PORT ?= 8012
FRONTEND_PORT ?= 3011

# Prefer the working local-model environment when present. Other checkouts
# keep using .venv; either choice can be overridden with VENV_DIR=... .
VENV_DIR ?= $(if $(wildcard .venv-ollama/pyvenv.cfg),.venv-ollama,.venv)

ifeq ($(OS),Windows_NT)
    # ezwinports make runs recipes through cmd.exe; force it so set/&& work
    # even when a stray sh.exe is on PATH.
    SHELL := cmd.exe
    .SHELLFLAGS := /c
    VENV_PY ?= $(VENV_DIR)\Scripts\python.exe
    BACKEND_ENV := set "PYTHONIOENCODING=utf-8" &&
    FRONTEND_ENV := set "BACKEND_URL=http://127.0.0.1:$(BACKEND_PORT)" &&
    TEST_ENV := set "BOM_ALLOW_SQLITE=1" && set "BOM_DB_PATH=backend/data/bom_review.db" && set "PYTHONIOENCODING=utf-8" &&
else
    VENV_PY ?= $(VENV_DIR)/bin/python
    BACKEND_ENV := PYTHONIOENCODING=utf-8
    FRONTEND_ENV := BACKEND_URL=http://127.0.0.1:$(BACKEND_PORT)
    TEST_ENV := BOM_ALLOW_SQLITE=1 BOM_DB_PATH=backend/data/bom_review.db PYTHONIOENCODING=utf-8
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
	$(BACKEND_ENV) "$(VENV_PY)" -m uvicorn app.main:app --app-dir backend --port $(BACKEND_PORT)

frontend:
	cd frontend && $(FRONTEND_ENV) npm run dev -- --webpack --port $(FRONTEND_PORT)

install:
	"$(VENV_PY)" -m pip install -r backend/requirements.txt
	cd frontend && npm install

test:
	$(TEST_ENV) "$(VENV_PY)" -m pytest backend/tests -q
