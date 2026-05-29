# GIK-IceChain v2.0 — Makefile

.DEFAULT_GOAL := help
PYTHON        ?= python3
PIP           ?= pip

.PHONY: install
install: ## Install package in editable mode with all dependencies
	$(PIP) install -e ".[full]"

.PHONY: install-dev
install-dev: ## Install with dev dependencies only
	$(PIP) install -e ".[dev]"

.PHONY: pre-commit-install
pre-commit-install: ## Install pre-commit hooks
	pre-commit install
	pre-commit install --hook-type commit-msg

.PHONY: lint
lint: ## Run ruff linter
	ruff check src/ tests/
	ruff format --check src/ tests/

.PHONY: format
format: ## Auto-format with ruff
	ruff format src/ tests/
	ruff check --fix src/ tests/

.PHONY: typecheck
typecheck: ## Run mypy type checker
	mypy src/gik_icechain/ --ignore-missing-imports

.PHONY: test
test: ## Run all unit tests
	pytest tests/unit/ -v --tb=short -m "unit" \
	  --cov=src/gik_icechain \
	  --cov-report=term-missing \
	  --cov-fail-under=80

.PHONY: test-fast
test-fast: ## Run unit tests without coverage (faster)
	pytest tests/unit/ -v --tb=short -m "unit" -x

.PHONY: test-integration
test-integration: ## Run integration tests (requires AWS credentials)
	pytest tests/integration/ -v --tb=short -m "integration" --timeout=300

.PHONY: test-notebooks
test-notebooks: ## Test all notebooks with nbmake
	pytest --nbmake notebooks/ --nbmake-timeout=300 -v \
	  --ignore=notebooks/05_aifs_vs_ifs_comparison.ipynb

.PHONY: test-all
test-all: test test-notebooks ## Run all tests

.PHONY: download-data
download-data: ## Download admin boundaries, CMORPH thresholds, EM-DAT
	$(PYTHON) scripts/download_data.py --component all

.PHONY: run-demo
run-demo: ## Run pipeline on 30-day demo window (Oct 2024)
	$(PYTHON) -m gik_icechain run-all \
	  --start 2024-10-01 \
	  --end   2024-10-31 \
	  --region east_africa \
	  --output results/demo/

.PHONY: benchmark
benchmark: ## Run full benchmark: GIK+IceChunk vs dynamical.org
	$(PYTHON) scripts/run_benchmark.py \
	  --output results/benchmarks/
	@echo "Results written to results/benchmarks/"

.PHONY: validate-store
validate-store: ## Validate IceChunk store integrity
	$(PYTHON) scripts/validate_store.py \
	  --store-uri $${GIK_ICECHUNK_STORE_URI:-s3://your-bucket/gik-icechain-store}

.PHONY: export-eahw
export-eahw: ## Export admin-1 risk to East Africa Hazard Watch Portal format
	$(PYTHON) scripts/export_eahw.py \
	  --risk-dir results/admin1_risk/ \
	  --output results/eahw_export/

.PHONY: dashboard
dashboard: ## Launch dashboard locally
	$(PYTHON) -m gik_icechain dashboard --port 8080

.PHONY: build-storymaps
build-storymaps: ## Regenerate all VEDA-UI storymap MDX files
	$(PYTHON) dashboard/storymaps/generate_storymaps.py \
	  --exceedance-store $${GIK_EXCEEDANCE_STORE_URI} \
	  --risk-dir results/admin1_risk/ \
	  --output dashboard/calendar_map/data/

.PHONY: docker-build
docker-build: ## Build Docker image
	docker build -t gik-icechain:latest .

.PHONY: docker-run
docker-run: ## Run pipeline in Docker (demo)
	docker run --rm \
	  -e AWS_PROFILE=ecmwf-open \
	  -v ~/.aws:/root/.aws:ro \
	  -v $(PWD)/results:/results \
	  gik-icechain:latest \
	  python -m gik_icechain run-all \
	    --start 2024-10-01 \
	    --end   2024-10-31

.PHONY: clean
clean: ## Remove build artifacts, caches, temp files
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache"  -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	find . -name "coverage.xml" -delete 2>/dev/null || true
	find . -name ".coverage"    -delete 2>/dev/null || true

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-25s\033[0m %s\n", $$1, $$2}'
