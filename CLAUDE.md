# Working on ATTa

## Done means verified

No change is complete until both of these pass on the changed checkout:

    python3 -m unittest discover -s tests -v        # includes tests/test_login_selftest.py
    python3 04-deployment/login_selftest.py         # must end: LOGIN SELF-TEST PASSED: 11/11 steps

The Login self-test starts ATTa with the real launcher (`bash run`) in a throwaway folder, creates a test
account with `accounts.py add`, then signs in, loads a protected page, signs out and signs in again in a real
Chromium (Playwright). If it prints `LOGIN SELF-TEST FAILED at step N (...)`, the work is not done. Fix it, or
report that step and its output as the outcome. Never skip it or edit it to pass.

Known before the self-test was added: 8 errors in `test_v119_login_proof` and `test_v121_journey_author`
(Playwright sync API inside an asyncio loop). Report them; don't count them as a pass.

## Never overwrite

`$APP_BUILDER_ROOT/state/users.json` (all accounts), `$APP_BUILDER_ROOT/.env` (its
`APP_BUILDER_SESSION_SECRET`: changing it signs everyone out) and `$APP_BUILDER_ROOT/TEST_ACCOUNTS.txt`
(the only copy of the generated passwords). Details: docs/SETUP-AND-LOGIN.md.

## The live system

On a systemd Linux server, `bash run` is the full server deploy (`bootstrap.sh`). Don't run it, and don't
touch `/srv/app-builder` or `/opt/app-builder`, without the owner's approval. The self-test refuses to run there.
