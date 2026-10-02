# The read-only catalog service. Reaches the catalog database over the compose
# network; publishes only the web service, on 127.0.0.1 (docker-compose.yml).
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.21 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY marketplace ./marketplace
RUN uv sync --frozen --no-dev
EXPOSE 8095
CMD ["uv", "run", "--frozen", "--no-dev", "python", "-m", "marketplace.service", "--host", "0.0.0.0", "--port", "8095"]
