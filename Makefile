.PHONY: lint test e2e check

SCRIPTS := $(wildcard scripts/*.sh scripts/lib/*.sh tests/e2e/*.sh)

lint:
	shellcheck -x $(SCRIPTS)

test:
	bats tests/unit

check: lint test

# DESTRUCTIVE: wipes any Coolify install on this machine. Throwaway VM / CI only.
e2e:
	sudo E2E_CONFIRM_WIPE=yes tests/e2e/e2e.sh
