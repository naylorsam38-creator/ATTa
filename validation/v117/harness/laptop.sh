#!/usr/bin/env bash
# laptop.sh CMD... — run a command as the laptop user sam inside atta-laptop, with the gateway base set.
exec docker exec -u sam -w /home/sam -e ATTA_BASE=http://127.0.0.1:8787 atta-laptop bash -lc "$*"
