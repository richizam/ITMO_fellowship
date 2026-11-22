# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_CACHE=1
# antiword reads legacy .doc CVs (LibreOffice would add ~400 MB)
RUN apt-get update && apt-get install -y --no-install-recommends antiword \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./

# --- build the SJR quartile database once, at image build time ---------------------
FROM base AS sjr
RUN uv sync --frozen --no-install-project
COPY scripts/build_sjr.py scripts/
RUN .venv/bin/python scripts/build_sjr.py

# --- runtime --------------------------------------------------------------------------
FROM base AS runtime
RUN uv sync --frozen --no-dev --no-install-project
COPY app app
COPY scripts scripts
COPY --from=sjr /app/data/sjr.sqlite /opt/sjr/sjr.sqlite
ENV PATH="/app/.venv/bin:$PATH" SJR_DB=/opt/sjr/sjr.sqlite MODELS_DIR=/opt/models
RUN useradd --uid 1000 --create-home appuser && mkdir -p /app/data /opt/models && chown appuser /app/data /opt/models
USER appuser
# download the multilingual embedding model and pre-compute the ITMO catalog index (stage 2)
RUN python -m app.analysis.matching --warmup
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
# one worker: background jobs live in process memory
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
