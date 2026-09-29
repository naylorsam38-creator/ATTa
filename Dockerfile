# ATTa v121 — one image, run by Coolify (05-coolify/kit/scripts/deploy-atta.sh builds and deploys it).
# Contains: ATTa's code, Python, git, Node, the Docker CLI + pinned Compose, and Playwright's Chromium
# for the real-browser stage. It uses the HOST's Docker through the mounted socket; it runs no Docker of its own.
FROM python:3.12-slim-bookworm

ARG ATTA_COMPOSE_VERSION=v5.5.1
ARG ATTA_COMPOSE_SHA256_X86_64=db1889184726840f75c4f9c001048430d4f25b3be3cb084d3ddd762bc0aed576
ARG ATTA_COMPOSE_SHA256_AARCH64=732e3a84c1a0f67256ce80bc2598a24546b10ca05f9faa97efceb1171ece2ef7
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    APP_BUILDER_ROOT=/srv/app-builder APP_BUILDER_HOST=0.0.0.0 APP_BUILDER_PORT=8787 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gnupg git unzip nodejs npm procps \
 && install -m 0755 -d /etc/apt/keyrings \
 && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian bookworm stable" \
      > /etc/apt/sources.list.d/docker.list \
 && apt-get update && apt-get install -y --no-install-recommends docker-ce-cli docker-buildx-plugin \
 && rm -rf /var/lib/apt/lists/*

# Docker Compose: the same pinned version and checksums as bootstrap-lib.sh. A mismatch fails the build.
RUN set -eu; arch="$(uname -m)"; \
    case "$arch" in x86_64) sum="$ATTA_COMPOSE_SHA256_X86_64" ;; aarch64) sum="$ATTA_COMPOSE_SHA256_AARCH64" ;; \
      *) echo "unsupported architecture $arch" >&2; exit 1 ;; esac; \
    mkdir -p /usr/local/lib/docker/cli-plugins; \
    curl -fsSL -o /usr/local/lib/docker/cli-plugins/docker-compose \
      "https://github.com/docker/compose/releases/download/${ATTA_COMPOSE_VERSION}/docker-compose-linux-${arch}"; \
    echo "$sum  /usr/local/lib/docker/cli-plugins/docker-compose" | sha256sum -c -; \
    chmod 755 /usr/local/lib/docker/cli-plugins/docker-compose; docker compose version

# Real-browser stage (watcher stage 6). Fails the build if Chromium can't open a page.
RUN pip install playwright \
 && python3 -m playwright install --with-deps chromium \
 && python3 -c "from playwright.sync_api import sync_playwright as s; p=s().start(); b=p.chromium.launch(headless=True); pg=b.new_page(); pg.goto('data:text/html,<title>ok</title>'); assert pg.title()=='ok'; b.close(); p.stop(); print('PLAYWRIGHT_BROWSER_PASS')"

COPY . /opt/atta
RUN rm -rf /opt/atta/05-coolify /opt/atta/tests /opt/atta/Dockerfile /opt/atta/docker-compose.coolify.yml \
 && find /opt/atta -name __pycache__ -prune -exec rm -rf {} + \
 && python3 -m py_compile /opt/atta/04-deployment/*.py

WORKDIR /srv/app-builder
EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=5 \
  CMD curl -fsS -o /dev/null "http://127.0.0.1:${APP_BUILDER_PORT}/" || exit 1
CMD ["python3", "/opt/atta/04-deployment/container_main.py"]
