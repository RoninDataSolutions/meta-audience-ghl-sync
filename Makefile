.PHONY: dev backend frontend test up down logs restart db

# ── Local Docker deployment (permanent) ──────────────────────────────────────

up: ## Start postgres + app in Docker (detached)
	docker compose -f docker-compose.local.yml up -d --build

down: ## Stop and remove containers
	docker compose -f docker-compose.local.yml down

logs: ## Tail app logs
	docker compose -f docker-compose.local.yml logs -f app

restart: ## Rebuild and restart app container only (after code change)
	docker compose -f docker-compose.local.yml up -d --build app

# ── Local dev (hot-reload) ────────────────────────────────────────────────────

dev: db ## Run backend + frontend with hot-reload
	@trap 'kill 0' SIGINT; \
	$(MAKE) backend & \
	$(MAKE) frontend & \
	wait

db: ## Ensure postgres Docker container is running
	@docker start ghl-meta-postgres 2>/dev/null || true

backend: ## Run FastAPI dev server
	cd backend && venv/bin/uvicorn app:app --reload --port 9876

frontend: ## Run Vite dev server
	cd frontend && npm run dev

test: ## Run backend tests
	cd backend && venv/bin/pytest tests/ -v
