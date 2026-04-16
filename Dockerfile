# ── Stage 1: dependency installer ─────────────────────────────────────────────
FROM python:3.12-slim AS builder

# Install uv from official image (fast, reproducible installs)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Copy lockfile and project metadata only — maximises layer cache reuse.
# The venv is rebuilt only when pyproject.toml or uv.lock changes.
COPY pyproject.toml uv.lock ./

# Install all dependencies into /app/.venv (project source not yet needed)
RUN uv sync --frozen --no-install-project

# ── Stage 2: runtime image ─────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# uv is still needed at runtime to run commands inside the venv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Carry over the fully-built venv from the builder stage
COPY --from=builder /app/.venv /app/.venv

# Copy application source
COPY . .

# Pre-create persistent directories; the docker-compose volumes will bind-mount
# over these at runtime, preserving data across container restarts.
RUN mkdir -p sessions output

EXPOSE 8501

# uv run resolves the existing .venv automatically — no activation needed.
# --server.headless=true suppresses the "open browser" prompt in logs.
CMD ["uv", "run", "streamlit", "run", "streamlit_app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true"]
