# Docker Setup Guide

This project includes Docker configurations for containerizing the Sentinel SRE demo.

## Files

- **`docker-compose.yml`** — Recommended: runs all services (PostgreSQL, order-api, sentinel) with proper networking
- **`demo/order-service/Dockerfile`** — Order API service
- **`sentinel/Dockerfile`** — Sentinel service
- **`Dockerfile.multi`** — Alternative: single image with both services + supervisor (not recommended for development)

## Quick Start with Docker Compose

### 1. Build all images

```bash
docker-compose build
```

### 2. Start all services

```bash
docker-compose up -d
```

This starts:
- PostgreSQL on `localhost:5432`
- Order API on `localhost:9000`
- Sentinel on `localhost:8001`

### 3. Check services are running

```bash
# Order API health
curl http://localhost:9000/health

# Sentinel status
curl http://localhost:8001/api/v1/status
```

### 4. Run the demo

```bash
# In a new terminal
docker-compose exec order-api python3 scripts/load_gen.py

# Or run interactive demo
docker-compose exec sentinel python3 scripts/demo_runner.py
```

### 5. View logs

```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f order-api
docker-compose logs -f sentinel
```

### 6. Stop services

```bash
docker-compose down

# Also remove volumes
docker-compose down -v
```

## Development Mode

The docker-compose setup mounts volumes for live reloading:

```yaml
volumes:
  - ./demo/order-service:/app
  - ./sentinel:/app
```

Changes to `.py` files will automatically reload uvicorn (with `--reload` flag).

## Environment Variables

Create a `.env` file in the project root:

```bash
# Optional: GitHub integration
GITHUB_TOKEN=your_github_token_here

# Optional: OpenAI API for LLM-based RCA
OPENAI_API_KEY=sk-...
```

The `docker-compose.yml` will automatically load these.

## Building Individual Images

### Order API only

```bash
cd demo/order-service
docker build -t sentinel-demo-order-api:latest .
docker run -p 9000:9000 \
  -e DATABASE_URL=postgresql://orders:orders@host.docker.internal:5432/orders \
  sentinel-demo-order-api:latest
```

### Sentinel only

```bash
cd sentinel
docker build -t sentinel-demo-sentinel:latest .
docker run -p 8001:8001 \
  -e ORDER_API_URL=http://host.docker.internal:9000 \
  sentinel-demo-sentinel:latest
```

## Troubleshooting

### Database connection refused

Ensure PostgreSQL is healthy before order-api starts:

```bash
docker-compose logs postgres
```

The `healthcheck` in docker-compose should prevent order-api from starting before postgres is ready.

### Port already in use

Change the port mapping in `docker-compose.yml`:

```yaml
order-api:
  ports:
    - "9001:9000"  # Map to 9001 instead
```

### Volumes not updating in container

Ensure you're using the correct path format for your OS:
- **Linux/WSL**: `/path/to/Demo_1`
- **macOS**: `/path/to/Demo_1`
- **Windows**: `C:\path\to\Demo_1` (Docker Desktop will convert)

### Permission denied errors

Add your user to the docker group:

```bash
sudo usermod -aG docker $USER
newgrp docker
```

## Production-Ready Considerations

For production deployment:

1. **Remove `--reload` flags** — use stable image versions instead
2. **Use secrets management** — don't pass tokens in `docker-compose.yml`
3. **Add resource limits** — CPU, memory constraints
4. **Use health checks** — already included for PostgreSQL
5. **Add logging drivers** — centralize logs (ELK, Datadog, etc.)
6. **Enable restart policies** — `restart: unless-stopped`

Example production override:

```yaml
order-api:
  restart: unless-stopped
  command: gunicorn main:app --workers 4 --worker-class uvicorn.workers.UvicornWorker
  deploy:
    resources:
      limits:
        cpus: '1'
        memory: 512M
```

## Testing

Run tests in container:

```bash
# Unit tests
docker-compose exec order-api pytest tests/test_api.py -v

# Integration tests (needs DB)
docker-compose exec order-api pytest tests/test_order_service.py -v

# All tests
docker-compose exec order-api pytest -v
```

## References

- Docker Compose docs: https://docs.docker.com/compose/
- Multi-stage builds: https://docs.docker.com/build/building/multi-stage/
- Uvicorn with Docker: https://www.uvicorn.org/deployment/
