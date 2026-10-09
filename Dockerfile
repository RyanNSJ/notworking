# ---- build: resolve the locked dependencies into a venv ----
FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.10.4 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

# ---- runtime ----
FROM python:3.13-slim
LABEL org.opencontainers.image.source=https://github.com/RyanNSJ/notworking
RUN useradd --create-home --uid 10001 app && mkdir /data && chown app /data
COPY --from=build /app/.venv /app/.venv
COPY catalog /app/catalog
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    AGENTDOWN_DATABASE_URL=sqlite:////data/agentdown.db \
    AGENTDOWN_HOST=0.0.0.0 \
    AGENTDOWN_PORT=8000
USER app
WORKDIR /app
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"]
CMD ["agentdown", "serve"]
