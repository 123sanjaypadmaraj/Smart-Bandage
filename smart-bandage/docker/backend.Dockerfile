# Phase 4 backend image -- FastAPI + Uvicorn.
# Build from the repo root so the image can `COPY` common/ and processing/,
# which backend.app imports:
#
#   docker build -f docker/backend.Dockerfile -t smart-bandage-backend .
#
# Or just `docker compose up` from the repo root (see docker-compose.yml).
FROM python:3.12-slim AS base

WORKDIR /app

# Install deps first so this layer is cached across code-only changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Everything backend.app imports across module boundaries.
COPY backend/ backend/
COPY common/ common/
COPY processing/ processing/
COPY simulator/ simulator/

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
