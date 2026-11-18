# Stage 1: install the app and its packages into /app/.venv with uv.
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Packages first, so this slow layer is reused when only the code changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY README.md ./
COPY src ./src
RUN uv sync --locked --no-dev

# Stage 2: only what's needed to run, without uv.
FROM python:3.12-slim
RUN useradd --create-home --uid 1000 app && mkdir /data && chown app /data
COPY --from=build --chown=app /app /app
USER app
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 DOCINTEL_DATA_DIR=/data
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"]
# One worker: search indexes and chats live in memory, so two processes would disagree.
# --proxy-headers: the app runs behind Caddy, which passes on the visitor's real address.
CMD ["uvicorn", "document_intelligence_rag.web:app", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
