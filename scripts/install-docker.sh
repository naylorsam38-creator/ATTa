#!/usr/bin/env bash
set -e
if command -v docker >/dev/null; then exit 0; fi
. /etc/os-release
case "$ID" in ubuntu|debian) apt-get update; apt-get install -y docker.io docker-compose-plugin;; amzn) dnf install -y docker;; *) exit 1;; esac
systemctl enable docker; systemctl start docker
