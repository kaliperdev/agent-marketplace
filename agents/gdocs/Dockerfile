# The Google Docs agent service. Built from agents/ so the shared kit is included:
#   docker build -f agents/gdocs/Dockerfile -t kaliper/agent-gdocs:2.0.0 agents
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.21 /uv /usr/local/bin/uv
WORKDIR /app
COPY kit /kit
COPY gdocs/pyproject.toml gdocs/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY gdocs/gdocs_agent ./gdocs_agent
RUN uv sync --frozen --no-dev
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
# The environment was built above; running it directly skips a re-sync on every start.
CMD ["/app/.venv/bin/python", "-m", "gdocs_agent.server"]
