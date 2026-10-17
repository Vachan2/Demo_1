# Sentinel Demo 1 — Observe-only incident integration

This repository contains the Demo 1 Order API. It generates a realistic
database connection-pool incident and reports bounded evidence to the modern
`sentinel-ai-platform` deployment. Demo 1 remains observe-only: it does not
create GitHub changes, restart Azure resources, deploy, or resolve incidents.

## Quick start

Start PostgreSQL and the Order API:

```bash
make db
make order-api
```

Configure the modern Sentinel endpoint before starting the Order API:

```bash
export SENTINEL_URL='http://localhost:8000'
export SENTINEL_INCIDENT_API_KEY='set-a-local-incident-secret'
```

The Order API sends incidents to `POST /api/v1/incidents` using the
`X-Sentinel-Incident-Key` header. The key must match the modern Sentinel
`SENTINEL_INCIDENT_API_KEY` setting.

## Demo walkthrough

```bash
export DEMO_FAILURE_ADMIN_TOKEN='set-a-local-demo-secret'
make demo-failure
make load-gen
make dashboard
```

The failure mode exhausts the database connection pool. Modern Sentinel
investigates the resulting incident and records an observe-only escalation.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `DEMO_FAILURE_MODE` | `none` | Initial failure mode; keep `none` outside a controlled demo. |
| `DEMO_FAILURE_ADMIN_TOKEN` | _(empty)_ | Secret required by `/admin/failure`. |
| `IMAGE_VERSION` | _(empty)_ | Deployed image/commit identifier sent to Sentinel. |
| `SENTINEL_URL` | `http://localhost:8000` | Modern Sentinel incident API base URL. |
| `SENTINEL_INCIDENT_API_KEY` | _(empty)_ | Dedicated key sent in `X-Sentinel-Incident-Key`; must match Sentinel. |

## Project structure

```
Demo 1/
├── demo/order-service/       # FastAPI + asyncpg Order API
├── dashboard/                # Static metrics dashboard
├── scripts/load_gen.py       # Standalone load generator
├── docker-compose.yml        # PostgreSQL + Order API
└── Makefile
```

## Tests

```bash
make test             # all Order API tests
make test-unit        # unit tests without a database
make test-integration # integration tests requiring PostgreSQL
make test-leak        # connection-leak regression test
```

## Sentinel integration

| Endpoint | Description |
|---|---|
| `POST /api/v1/incidents` | Authenticated incident submission to modern Sentinel |
| `GET /api/v1/incidents` | Incident listing provided by modern Sentinel |
| `GET /api/v1/incidents/{id}` | Incident detail provided by modern Sentinel |

The modern Sentinel platform owns investigation, policy, remediation, and
deployment behavior. This repository only reports Demo 1 evidence.
