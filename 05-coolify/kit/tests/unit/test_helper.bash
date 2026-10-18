# Shared setup for bats unit tests.
KIT_DIR="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../.." && pwd)"
export KIT_DIR
export NO_COLOR=1

# Put stub commands first on PATH. stub NAME 'shell body'
stub() {
    local name="$1" body="$2"
    mkdir -p "$BATS_TEST_TMPDIR/bin"
    printf '#!/usr/bin/env bash\n%s\n' "$body" >"$BATS_TEST_TMPDIR/bin/$name"
    chmod +x "$BATS_TEST_TMPDIR/bin/$name"
    export PATH="$BATS_TEST_TMPDIR/bin:$PATH"
}

load_common() {
    unset ATTA_COMMON_LOADED
    # shellcheck source=scripts/lib/common.sh
    . "$KIT_DIR/scripts/lib/common.sh"
}
