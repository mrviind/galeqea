# GaleQEA: development and deployment tasks.
.DEFAULT_GOAL := help
SHELL := /bin/bash
PY := .venv/bin/python
PIP := .venv/bin/pip

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

.PHONY: setup
setup: ## One-time setup: Python deps, Node deps, browsers, UI build
	python3 -m venv .venv
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -e "apps/api[dev,postgres]"
	cd apps/runner && npm install && npx playwright install chromium
	cd apps/web && npm install && npm run build
	@echo ""
	@echo "Setup complete. Start GaleQEA with:  make up"

.PHONY: start
start: setup up ## First run, one command: install everything, then launch on :8080

.PHONY: up
up: ## Start GaleQEA (API + UI + scheduler) on http://localhost:8080 (after `make start` once)
	cd apps/api && PYTHONPATH=. ../../$(PY) -m galeqea.cli up

.PHONY: dev
dev: ## Development: API with reload on :8080, Vite on :5173
	@trap 'kill 0' EXIT; \
	(cd apps/api && PYTHONPATH=. ../../$(PY) -m uvicorn galeqea.main:app --reload --port 8080) & \
	(cd apps/web && npm run dev) & \
	wait

.PHONY: test
test: ## Run the Python test suite
	cd apps/api && PYTHONPATH=. ../../$(PY) -m pytest -q

.PHONY: lint
lint: ## Lint and typecheck everything
	.venv/bin/ruff check apps/api/galeqea
	cd apps/web && npx tsc --noEmit
	cd apps/runner && node --check src/cli.mjs && node --check src/executor.mjs && node --check src/locator.mjs

.PHONY: build
build: ## Build the web UI
	cd apps/web && npm run build

.PHONY: doctor
doctor: ## Check that everything GaleQEA needs is installed
	cd apps/api && PYTHONPATH=. ../../$(PY) -m galeqea.cli doctor

.PHONY: mcp
mcp: ## Run the MCP server over stdio
	cd apps/api && PYTHONPATH=. ../../$(PY) -m galeqea.cli mcp

.PHONY: demo
demo: ## Serve the bundled demo application under test on :8765
	cd examples/demo-app && python3 -m http.server 8765

.PHONY: a11y
a11y: ## Run axe over GaleQEA's own pages (needs the app running on :8080)
	cd apps/runner && node a11y.mjs

.PHONY: bundle
bundle: ## Check the web UI's initial JS stays under the gzip budget (after `make build`)
	cd apps/web && node scripts/check-bundle.mjs

.PHONY: hero
hero: ## Re-record the landing-site hero from a running GaleQEA (needs the app + demo running)
	npm --prefix apps/runner exec playwright install chromium >/dev/null 2>&1 || true
	cd apps/runner && node ../../docs/site/scripts/record-hero.mjs

.PHONY: site
site: ## Preview the static landing site locally on :4321
	cd docs/site && python3 -m http.server 4321

.PHONY: site-audit
site-audit: ## Lighthouse the landing site (desktop + mobile), needs `make site` running on :4321
	cd apps/runner && CHROME_PATH="$$(node -e "console.log(require('playwright').chromium.executablePath())")" \
	  npx --yes lighthouse@12 http://localhost:4321/ --preset=desktop \
	  --chrome-flags="--headless=new" --output=json --output-path=/tmp/lh-desktop.json --quiet && \
	  node -e "const r=require('/tmp/lh-desktop.json').categories;console.log('desktop',Object.fromEntries(Object.entries(r).map(([k,v])=>[k,Math.round(v.score*100)])))"
	cd apps/runner && CHROME_PATH="$$(node -e "console.log(require('playwright').chromium.executablePath())")" \
	  npx --yes lighthouse@12 http://localhost:4321/ \
	  --chrome-flags="--headless=new" --output=json --output-path=/tmp/lh-mobile.json --quiet && \
	  node -e "const r=require('/tmp/lh-mobile.json').categories;console.log('mobile ',Object.fromEntries(Object.entries(r).map(([k,v])=>[k,Math.round(v.score*100)])))"

.PHONY: docker
docker: ## Build and start the Docker stack
	docker compose up --build

.PHONY: clean
clean: ## Remove build artefacts (leaves your GaleQEA data alone)
	rm -rf apps/web/dist apps/api/.pytest_cache .ruff_cache
	find . -name __pycache__ -prune -exec rm -rf {} +

.PHONY: bench
bench: ## Token-efficiency benchmark (WO#8), fails if under target/over budget
	cd apps/runner && node bench-tokens.mjs
