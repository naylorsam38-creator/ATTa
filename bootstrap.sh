#!/usr/bin/env bash
set -e
mkdir -p /opt/atta
[ -f /opt/atta/.initialized ] || touch /opt/atta/.initialized
bash scripts/install-docker.sh || true
