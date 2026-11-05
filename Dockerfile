FROM node:24-bookworm-slim AS web
WORKDIR /build
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends bash curl jq bc ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY pyproject.toml requirements.lock ./
COPY src/ ./src/
RUN pip install --no-cache-dir -r requirements.lock && pip install --no-cache-dir --no-deps .
COPY --from=web /build/dist ./web/dist
COPY THIRD_PARTY_NOTICES.md ./licenses/NOTICE.md
COPY web/vendor/ ./licenses/
COPY config/accounts.example.json ./config/accounts.example.json
COPY scripts/deployment_info.py ./scripts/deployment_info.py
EXPOSE 8080 1080 8888
CMD ["rapip"]
