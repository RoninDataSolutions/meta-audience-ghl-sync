.PHONY: dev backend frontend test

dev: ## Run backend + frontend in parallel
	@trap 'kill 0' SIGINT; \
	$(MAKE) backend & \
	$(MAKE) frontend & \
	wait

backend: ## Run FastAPI dev server
	cd backend && venv/bin/uvicorn app:app --reload --port 9876

frontend: ## Run Vite dev server
	cd frontend && npm run dev

test: ## Run backend tests
	cd backend && venv/bin/pytest tests/ -v
