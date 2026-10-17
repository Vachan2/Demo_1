# Docker Setup Guide

This repository packages the Demo 1 Order API. The Sentinel platform is a
separate deployment; configure its URL and dedicated incident API key before
starting the Order API.

## Docker Compose

Set the integration variables in the shell or a root `.env` file:

```bash
export SENTINEL_URL='http://localhost:8000'
export SENTINEL_INCIDENT_API_KEY='set-a-local-incident-secret'
```

Then start the database and Order API:

```bash
docker compose up --build -d
```

This starts PostgreSQL on `localhost:5432` and the Order API on
`localhost:9000`. The Order API reports incidents to the configured modern
Sentinel endpoint using `X-Sentinel-Incident-Key`. It does not run a local
remediation service.

## Checks and demo

```bash
curl http://localhost:9000/health
make demo-failure
make load-gen
make dashboard
```

The modern Sentinel deployment should show the incident as an observe-only
Demo 1 investigation. It must not create GitHub changes, restart Azure
resources, deploy, or mark the incident resolved.

## Order API image

```bash
cd demo/order-service
docker build -t sentinel-demo-order-api:latest .
docker run -p 9000:9000 \
  -e DATABASE_URL=postgresql://orders:orders@host.docker.internal:5432/orders \
  -e SENTINEL_URL=http://host.docker.internal:8000 \
  -e SENTINEL_INCIDENT_API_KEY="$SENTINEL_INCIDENT_API_KEY" \
  sentinel-demo-order-api:latest
```
