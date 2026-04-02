# ── EasyBets Makefile ─────────────────────────────────────────────────────────
# Usage: make <target>

.PHONY: dev install ingest train analyze lint clean

## Start the API in dev mode (hot reload)
dev:
	uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

## Install all Python dependencies
install:
	pip install -r requirements.txt

## Pull Polymarket training data into MongoDB (last 500 markets for a quick test)
ingest:
	python -m ingestion.build_mongo_matrix --limit 500

## Pull ALL Polymarket training data (slow — use for production)
ingest-full:
	python -m ingestion.build_mongo_matrix

## Rebuild MongoDB from scratch (drops existing data first)
ingest-fresh:
	python -m ingestion.build_mongo_matrix --fresh

## Train the ML model from the data in MongoDB
train:
	python -m ml.run_pipeline

## Analyse historical base rates by question category
analyze:
	python -m ml.base_rate_analysis

## Lint with ruff (install with: pip install ruff)
lint:
	ruff check .

## Remove Python cache files
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete
