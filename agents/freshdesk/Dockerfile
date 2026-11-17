# Staged image: do not publish until a live Freshdesk connection and guide pass.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.21 /uv /usr/local/bin/uv
WORKDIR /app
COPY kit /kit
COPY freshdesk/pyproject.toml freshdesk/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY freshdesk/freshdesk_agent ./freshdesk_agent
RUN uv sync --frozen --no-dev
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["/app/.venv/bin/python", "-m", "freshdesk_agent.server"]
