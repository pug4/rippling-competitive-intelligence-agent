# Hosted public-demo image for the FastAPI backend (competitive_agent.api:app).
#
# A Vercel-hosted UI chats/browses against this container. DEMO_PUBLIC=1 (set as
# a runtime env var, never baked in) keeps every expensive live-analysis POST
# behind a friendly 403 so the public cannot spend the owner's provider keys —
# only the three bundled demo runs are served, read-only, plus live chat/ask-AI.
#
# Build:  docker build -t competitive-agent-api .
# Run:    docker run -p 8000:8000 -e DEMO_PUBLIC=1 \
#             -e ANTHROPIC_API_KEY=... -e EXA_API_KEY=... -e SERPAPI_API_KEY=... \
#             competitive-agent-api
FROM python:3.12-slim

# uv: fast, reproducible installs straight from uv.lock.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PORT=8000

WORKDIR /app

# 1) Dependency layer — cached until the lockfile or manifest changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# 2) App source + behavioral config + the bundled demo packages + the seed
#    script. NOT the .env (see .dockerignore) — keys arrive as runtime env vars.
COPY src ./src
COPY config ./config
COPY ui/public/demo ./ui/public/demo
COPY scripts ./scripts

# 3) Install the project itself into the venv.
RUN uv sync --frozen --no-dev

# Run everything from the project venv (uvicorn, python, the installed package).
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# Entrypoint: seed the demo runs into outputs/runs/, then launch uvicorn on
# $PORT (Render/Railway inject it; default 8000) bound to 0.0.0.0. `exec` makes
# uvicorn PID 1 so it receives SIGTERM for graceful shutdown.
CMD ["sh", "-c", "python scripts/seed_demo_runs.py && exec uvicorn competitive_agent.api:app --host 0.0.0.0 --port ${PORT:-8000}"]
