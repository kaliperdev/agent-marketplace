# The GitHub agent service. Built from agents/ so the shared kit is included:
#   docker build -f agents/github/Dockerfile -t kaliper/agent-github:2.0.0 agents
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.21 /uv /usr/local/bin/uv
WORKDIR /app
COPY kit /kit
COPY github/pyproject.toml github/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY github/github_agent ./github_agent
RUN uv sync --frozen --no-dev
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
# The environment was built above; running it directly skips a re-sync on every start.
CMD ["/app/.venv/bin/python", "-m", "github_agent.server"]
