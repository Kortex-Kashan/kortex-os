# KORTEX Docker

Container configurations for development and production deployment.

## Files

| File | Description |
|------|-------------|
| `docker-compose.yml` | Local development stack (PostgreSQL, backend) |
| `docker-compose.prod.yml` | Production deployment configuration |
| `Dockerfile.backend` | Python backend container image |

## Quick Start

```bash
# Start local development stack
docker compose up -d

# View logs
docker compose logs -f

# Stop
docker compose down
```
