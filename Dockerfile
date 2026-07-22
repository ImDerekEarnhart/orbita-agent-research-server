FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /build
COPY . .
RUN python -m pip wheel --wheel-dir /wheels .

FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="Orbita Agent Research Server" \
      org.opencontainers.image.version="0.4.0" \
      org.opencontainers.image.description="Authenticated MCP research with governed policy improvement"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ORBITA_AGENT_HOME=/data \
    ORBITA_AGENT_REQUIRE_AUTH=1 \
    ORBITA_AGENT_AUTH_MODE=oauth-github

RUN apt-get update \
    && apt-get install --yes --no-install-recommends gosu ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin orbita

COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/* \
    && rm -rf /wheels \
    && mkdir -p /data \
    && chown orbita:orbita /data

COPY deploy/docker-entrypoint.sh /usr/local/bin/orbita-entrypoint
RUN chmod 0755 /usr/local/bin/orbita-entrypoint

EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/orbita-entrypoint"]
