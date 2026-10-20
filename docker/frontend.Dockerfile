# Phase 5 dashboard image -- static build served by nginx.
# Vite bakes VITE_API_BASE_URL / VITE_WS_BASE_URL into the JS bundle at
# build time, so they're build args here, not runtime env vars:
#
#   docker build -f docker/frontend.Dockerfile \
#     --build-arg VITE_API_BASE_URL=http://localhost:8000 \
#     --build-arg VITE_WS_BASE_URL=ws://localhost:8000 \
#     -t smart-bandage-frontend .
#
# Or just `docker compose up` from the repo root (see docker-compose.yml).
FROM node:20-slim AS build

WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
ARG VITE_API_BASE_URL=http://localhost:8000
ARG VITE_WS_BASE_URL=ws://localhost:8000
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
ENV VITE_WS_BASE_URL=$VITE_WS_BASE_URL
RUN npm run build

FROM nginx:1.27-alpine
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/frontend/dist /usr/share/nginx/html
EXPOSE 80
