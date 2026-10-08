# The Slack agent service. Built from agents/ so the shared kit is included:
#   docker build -f agents/slack/Dockerfile -t kaliper/agent-slack:2.0.0 agents
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.21 /uv /usr/local/bin/uv
WORKDIR /app
COPY kit /kit
COPY slack/pyproject.toml slack/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY slack/slack_agent ./slack_agent
RUN uv sync --frozen --no-dev
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
# The environment was built above; running it directly skips a re-sync on every start.
CMD ["/app/.venv/bin/python", "-m", "slack_agent.server"]
