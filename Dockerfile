FROM node:24-bookworm-slim AS web
WORKDIR /build
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.13-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY pyproject.toml requirements.lock ./
COPY src/ ./src/
RUN pip install --no-cache-dir -r requirements.lock && pip install --no-cache-dir --no-deps .
COPY --from=web /build/dist ./web/dist
COPY THIRD_PARTY_NOTICES.md ./licenses/NOTICE.md
COPY web/vendor/react-bits.LICENSE ./licenses/react-bits.LICENSE
COPY web/vendor/flag-icons.LICENSE ./licenses/flag-icons.LICENSE
COPY config/accounts.example.json ./config/accounts.example.json
COPY scripts/deployment_info.py ./scripts/deployment_info.py
EXPOSE 8080 1080 8888
CMD ["rapip"]
