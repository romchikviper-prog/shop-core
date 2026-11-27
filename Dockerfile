# syntax=docker/dockerfile:1.7

# Етап 1: збирання. Тут є uv, компіляція байткоду й wheel.
FROM python:3.12-slim AS builder

ARG GIT_SHA=dev
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.13.10 /uv /uvx /bin/
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN printf 'GIT_SHA = "%s"\n' "$GIT_SHA" > src/shop/_build_info.py \
    && uv sync --frozen --no-dev \
    && uv build --wheel --out-dir /dist \
    && rm -rf build src/shop_core.egg-info

# Етап 2: мінімальний рунтайм. Лише інтерпретатор, venv, код і wheel.
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PATH=/app/.venv/bin:$PATH

WORKDIR /app

RUN groupadd --system --gid 10001 shop \
    && useradd --system --uid 10001 --gid shop --home-dir /app --shell /usr/sbin/nologin shop

COPY --from=builder --chown=10001:10001 /app/.venv ./.venv
COPY --from=builder --chown=10001:10001 /app/src ./src
COPY --from=builder --chown=10001:10001 /dist ./dist

USER 10001:10001

EXPOSE 8080
STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)"]

ENTRYPOINT ["python", "-m", "shop.api"]
