FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /build
COPY . .
RUN python -m pip wheel --wheel-dir /wheels .

FROM debian:bookworm-slim AS lean-builder

ENV ELAN_HOME=/opt/elan \
    PATH=/opt/elan/bin:${PATH}
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates curl git zstd \
    && rm -rf /var/lib/apt/lists/* \
    && curl --proto '=https' --tlsv1.2 -sSf https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh \
       | sh -s -- -y --default-toolchain none
WORKDIR /opt/orbita-language-limit
COPY src/orbita_agent/resources/language_limit_kernel/ ./
# Repository transport preserves a final LF; the frozen rc1 sources intentionally
# did not contain one. Remove that byte, then verify the exact manifest identity.
RUN for file in MANIFEST.json lake-manifest.json lakefile.toml lean-toolchain OrbitaLanguageLimit.lean \
      OrbitaLanguageLimit/Basic.lean OrbitaLanguageLimit/Certificate.lean; do \
      if [ "$(tail -c 1 "$file" | od -An -t u1 | tr -d ' ')" = "10" ]; then truncate -s -1 "$file"; fi; \
    done \
    && echo "f25e21067c53116b8d70e80cc375d2205b459f0c01af3c095c758b288f54379c  MANIFEST.json" | sha256sum -c - \
    && lake exe cache get \
    && lake build

FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="Orbita Agent Research Server" \
      org.opencontainers.image.version="0.10.0" \
      org.opencontainers.image.description="Authenticated MCP research with governed policy improvement"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ORBITA_AGENT_HOME=/data \
    ORBITA_AGENT_REQUIRE_AUTH=1 \
    ORBITA_AGENT_AUTH_MODE=oauth-github \
    ORBITA_LANGUAGE_LIMIT_KERNEL_ROOT=/opt/orbita-language-limit \
    ORBITA_LEAN_EXECUTABLE=/opt/elan/bin/lake \
    ELAN_HOME=/opt/elan \
    PATH=/opt/elan/bin:${PATH}

RUN apt-get update \
    && apt-get install --yes --no-install-recommends gosu ca-certificates libgmp10 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin orbita

COPY --from=builder /wheels /wheels
COPY --from=lean-builder /opt/elan /opt/elan
COPY --from=lean-builder /opt/orbita-language-limit /opt/orbita-language-limit
RUN python -m pip install --no-cache-dir /wheels/* \
    && rm -rf /wheels \
    && mkdir -p /data \
    && chown orbita:orbita /data

COPY deploy/docker-entrypoint.sh /usr/local/bin/orbita-entrypoint
RUN chmod 0755 /usr/local/bin/orbita-entrypoint

EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/orbita-entrypoint"]
