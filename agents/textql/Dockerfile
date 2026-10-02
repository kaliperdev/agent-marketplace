# The TextQL agent service. Started by a client's settings service, on that
# client's private network; it publishes no port.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.21 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY textql_agent ./textql_agent
RUN uv sync --frozen --no-dev
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["uv", "run", "--frozen", "--no-dev", "python", "-m", "textql_agent.server"]
