# Sentinel Demo Makefile
# ─────────────────────────────────────────────────────────────────────────────
# Prerequisites: Python 3.12+, Docker, docker-compose
# ─────────────────────────────────────────────────────────────────────────────

.PHONY: help setup db order-api sentinel dashboard demo-healthy demo-failure \
        demo-recover load-gen test test-unit test-integration test-leak \
        stop clean

PYTHON     := python3
PIP        := pip3
ORDER_DIR  := demo/order-service
SENTINEL_DIR := sentinel

help:
	@echo ""
	@echo "  Sentinel Demo Commands"
	@echo "  ──────────────────────────────────────────────────"
	@echo "  make setup            Install all dependencies"
	@echo "  make db               Start PostgreSQL via Docker"
	@echo "  make order-api        Start the order API (port 9000)"
	@echo "  make sentinel         Start Sentinel (port 8001)"
	@echo "  make dashboard        Open dashboard in browser"
	@echo ""
	@echo "  make demo-healthy     Switch API to healthy mode (v1.0.0)"
	@echo "  make demo-failure     Switch API to connection-leak mode (v1.1.0)"
	@echo "  make demo-recover     Switch API back to healthy mode"
	@echo "  make load-gen         Run 100 requests × 20 concurrent"
	@echo "  make demo-run         Run the interactive demo script"
	@echo ""
	@echo "  make test             Run all tests"
	@echo "  make test-unit        Run unit tests only"
	@echo "  make test-integration Run integration tests (needs DB)"
	@echo "  make test-leak        Run connection-leak regression test"
	@echo ""
	@echo "  make stop             Stop all services"
	@echo "  make clean            Remove containers and volumes"
	@echo ""

# ─────────────────────────────────────────────────────────────────────────────
# Setup
# ─────────────────────────────────────────────────────────────────────────────

setup:
	@echo "→ Installing order-service dependencies..."
	cd $(ORDER_DIR) && $(PIP) install -r requirements.txt
	@echo "→ Installing sentinel dependencies..."
	cd $(SENTINEL_DIR) && $(PIP) install -r requirements.txt
	@echo "✓ Setup complete"

# ─────────────────────────────────────────────────────────────────────────────
# Infrastructure
# ─────────────────────────────────────────────────────────────────────────────

db:
	@echo "→ Starting PostgreSQL..."
	docker run --rm -d \
		--name sentinel-demo-pg \
		-e POSTGRES_USER=orders \
		-e POSTGRES_PASSWORD=orders \
		-e POSTGRES_DB=orders \
		-p 5432:5432 \
		postgres:16-alpine
	@echo "→ Waiting for Postgres to be ready..."
	@sleep 3
	@echo "→ Seeding database..."
	PGPASSWORD=orders psql -h localhost -U orders -d orders -f $(ORDER_DIR)/seed.sql
	@echo "✓ Database ready"

db-test:
	@echo "→ Creating test database..."
	PGPASSWORD=orders psql -h localhost -U orders -c "CREATE DATABASE orders_test;" orders || true
	PGPASSWORD=orders psql -h localhost -U orders -d orders_test -f $(ORDER_DIR)/seed.sql
	@echo "✓ Test database ready"

# ─────────────────────────────────────────────────────────────────────────────
# Services  (run in separate terminals)
# ─────────────────────────────────────────────────────────────────────────────

order-api:
	@echo "→ Starting order-api on :9000 (mode: $$DEMO_FAILURE_MODE)"
	cd $(ORDER_DIR) && uvicorn main:app --host 0.0.0.0 --port 9000 --reload

sentinel:
	@echo "→ Starting Sentinel on :8001"
	cd $(SENTINEL_DIR) && uvicorn main:app --host 0.0.0.0 --port 8001 --reload

dashboard:
	@echo "→ Opening dashboard..."
	open dashboard/index.html || xdg-open dashboard/index.html || echo "Open dashboard/index.html in your browser"

# ─────────────────────────────────────────────────────────────────────────────
# Demo controls
# ─────────────────────────────────────────────────────────────────────────────

demo-healthy:
	@echo "→ Switching to healthy mode..."
	curl -s -X POST "http://localhost:9000/admin/failure?mode=none" | python3 -m json.tool
	@echo "✓ API is now healthy"

demo-failure:
	@echo "→ Activating connection leak (v1.1.0)..."
	curl -s -X POST "http://localhost:9000/admin/failure?mode=connection_leak" | python3 -m json.tool
	@echo "✓ Connection leak active — run 'make load-gen' to trigger it"

demo-recover:
	@echo "→ Recovering — disabling failure mode..."
	curl -s -X POST "http://localhost:9000/admin/failure?mode=none" | python3 -m json.tool
	@echo "✓ API recovered"

load-gen:
	@echo "→ Running load generator (100 req × 20 concurrent)..."
	$(PYTHON) scripts/load_gen.py

demo-run:
	@echo "→ Starting interactive demo runner..."
	$(PYTHON) scripts/demo_runner.py

health-check:
	@echo "→ Order API health:"
	@curl -s http://localhost:9000/health | python3 -m json.tool
	@echo ""
	@echo "→ Sentinel status:"
	@curl -s http://localhost:8001/api/v1/status | python3 -m json.tool

# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

test:
	cd $(ORDER_DIR) && $(PYTHON) -m pytest -v

test-unit:
	cd $(ORDER_DIR) && $(PYTHON) -m pytest tests/test_order_service.py::TestHealthyImplementation tests/test_api.py -v

test-integration:
	cd $(ORDER_DIR) && $(PYTHON) -m pytest tests/test_order_service.py::TestOrderServiceIntegration -v

test-leak:
	cd $(ORDER_DIR) && $(PYTHON) -m pytest tests/test_order_service.py::TestConnectionRelease -v

# ─────────────────────────────────────────────────────────────────────────────
# Cleanup
# ─────────────────────────────────────────────────────────────────────────────

stop:
	@docker stop sentinel-demo-pg 2>/dev/null || true
	@echo "✓ Services stopped"

clean: stop
	@docker rm -f sentinel-demo-pg 2>/dev/null || true
	@echo "✓ Containers removed"
