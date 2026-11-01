# One container: FastAPI backend on 127.0.0.1:8000 (private) + Next.js on $PORT (public).
FROM node:20-bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv tzdata ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements-web.txt backend/requirements-web.txt
RUN python3 -m venv /venv && /venv/bin/pip install --no-cache-dir -r backend/requirements-web.txt

COPY frontend/package.json frontend/package.json
RUN cd frontend && npm install --no-audit --no-fund

COPY . .
RUN cd frontend && npm run build

ENV NODE_ENV=production \
    API_URL=http://127.0.0.1:8000 \
    APP_TIMEZONE=Africa/Lagos \
    API_CACHE_PATH=/tmp/edgeboard-api-cache.json

CMD ["sh", "/app/start.sh"]
