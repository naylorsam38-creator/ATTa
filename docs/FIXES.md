# Everything fixed

Every problem found and fixed while building and testing this kit, in the order it's grouped below.
Each one was found by actually running Coolify, not by guessing.

**Scope:** these fixes are in the Coolify deploy kit (this branch) and in how Coolify connects to ATTa.
ATTa's own application code (`claude/atta-hardening-v116`) was **not changed**; it was only tested against Coolify.

## A. Problems in Coolify itself that the kit now handles

| # | Problem | What would have happened | Fix |
|---|---|---|---|
| 1 | If Coolify rejects the admin details at first boot, it logs an error and carries on with public sign-up **open** | The first stranger to open port 8000 becomes the admin of your server | The kit checks the admin details with Coolify's exact rules before installing, re-runs Coolify's own admin setup if first boot failed, and `verify.sh` fails loudly if sign-up is open |
| 2 | Coolify rejects emails on domains like `example.com` (they publish "accepts no mail") and reserved names (`.test`, `.local`, `.internal`, `.lan`…) | Same as #1: silent failure, open sign-up. It happened during testing | The kit's email check mirrors Coolify's validator exactly |
| 3 | Passwords containing `$ # & \| \ " '` or spaces are mangled by Coolify's installer and settings file | Admin can't log in, or the password is silently changed | The kit only allows safe symbols, generates a strong password by default, and checks it against known breach lists |
| 4 | Usernames with spaces break Coolify's settings file | Coolify crashes on start | The kit only allows letters, digits, `_` and `-` |
| 5 | If `$USER` is empty (cloud-init, cron, some sudo setups), Coolify's own server gets no SSH login name | Everything looks healthy, but **no app can ever deploy**. It happened during testing | The kit always runs the installer as `USER=root`; `verify.sh` detects and explains it |
| 6 | Coolify can start its web proxy twice at once, and the two starts delete each other | The proxy disappears, so no app is reachable. It happened during testing | The kit waits until the proxy has stayed up for 75 seconds and restarts it if it drops |
| 7 | If the server boots with IPv6 disabled in the kernel, Coolify's dashboard crashes on a loop | The dashboard never starts. It happened during testing | Preflight blocks the install and gives the fix (re-enable it in GRUB) |
| 8 | Coolify reports "healthy" before it has finished creating its own server record | On fast servers the install crashed at random. **Found by the GitHub test** | The kit waits for Coolify's setup to finish before continuing |
| 9 | Coolify's installer exits "successfully" on some fatal errors (wrong OS, not root) | An install that did nothing looks like it worked | The kit checks the right Coolify version is actually running afterwards |
| 10 | The proxy isn't started until someone clicks through the onboarding screens | Apps can't be reached after install | The kit runs Coolify's own server validation during install, so the proxy is up straight away |
| 11 | Coolify stores its server key under a different filename than the installer created | Restoring onto a new server would have broken Coolify's access to it | Restore and verify look the key up in Coolify's database |
| 12 | Coolify's upgrade makes its secrets file look world-readable (mode 644) | A naive "fix" (`chmod 600`) would lock Coolify out of its own settings | `verify.sh` tests whether another user can *actually* read it (they can't, because the folder is locked) |
| 12b | The kit's admin repair could run while Coolify was still building its database on first boot: the admin got created, but the "sign-up off" setting was lost when Coolify created its settings afterwards | Sign-up left **open** on a fresh install. **Found by the GitHub test**; `verify.sh` caught it and stopped the install | The kit waits for Coolify's first boot to finish, lets Coolify's own admin step run first, and treats "admin exists but sign-up on" as something to fix (it switches sign-up off exactly as Coolify's seeder does), with five regression tests |

## B. Problems connecting Coolify to ATTa

| # | Problem | What would have happened | Fix |
|---|---|---|---|
| 13 | Coolify API tokens contain a `\|` character, and ATTa's `run` script reads `.env` as shell | The token gets cut off at the `\|`, so every hand-off fails | `connect-atta.sh` writes the value in quotes. Tested against ATTa's own `.env` reader |
| 14 | ATTa refuses to send its token over plain `http://` to a public IP (a good security rule) | On hosts with only a public IP (e.g. Hetzner), every hand-off would fail forever | `connect-atta.sh` refuses to write such a URL and tells you to use the https domain or a private network |
| 15 | ATTa's Coolify setup was 4 manual steps (turn on the API, create a token, fill in `.env`, map app names) | Easy to get wrong, and easy to give the token too much power | `connect-atta.sh` does all four, creates a deploy+read-only token and proves it can't change anything |
| 16 | ATTa's hand-off had never been run against a real Coolify | Unknown whether it worked at all | Run for real: sent → `DISPATCHED`, the deployment finished, no double deploys, and missing and wrong app IDs are handled, plus ATTa's self-repair lookup |
| 17 | The runbook told you to close port 8000 after setup | That would have cut ATTa off from Coolify | Port 8000 is now restricted to the ATTa server instead |
| 18 | Coolify updating itself could silently change the API ATTa relies on | ATTa's hand-off could break overnight | Auto-update is off by default; upgrades are deliberate (`upgrade.sh`, with a backup first) |
| 19 | ATTa pins a checksum for your `coolify-main.zip`, but the kit downloaded the installer instead | The zip's safety check was bypassed | New option: install straight from your zip after checking its checksum (it matches ATTa's exactly) |

## C. Bugs in the kit itself, caught by testing before they reached you

| # | Bug | Fix |
|---|---|---|
| 20 | Backup died silently because Coolify's settings file lacks one expected line | Settings lookups can no longer fail; there's a test for it |
| 21 | Several scripts would die silently if the `coolify` container was missing | One safe helper used everywhere |
| 22 | Any unexpected error could exit a script with no message | Every script now prints the exact file, line and command that failed |
| 23 | A failed database restore left Coolify stopped | Coolify is restarted, and the message gives the exact rollback command |
| 24 | Restore read the backup's details without checking them | A backup with bad details is refused with a clear message |
| 25 | The breach-list check's "couldn't check" result was treated as an error | Handled properly |
| 26 | `verify.sh` looked for the server key under the wrong name | Now looks it up in Coolify's database |
| 27 | The first GitHub test setup would never have run on the first push | Moved into its own workflow that runs whenever the scripts change |
| 28 | Two unit tests had bugs that made them test the wrong thing | Fixed |
| 29 | The runbook didn't say to point your domain at the new server after a restore | Added |
| 30 | Out-of-date test counts in the README | Corrected |

## D. Found but NOT fixed (needs your decision or can't be fixed from here)

| Item | Why it isn't fixed |
|---|---|
| ATTa's own `05-coolify/install-coolify.sh` still has problems #1, #5 and #6 | It lives on ATTa's branch; waiting on your OK to change it |
| Coolify's installer overwrites `/etc/docker/daemon.json` if you had custom Docker settings | That's Coolify's behaviour; documented in TROUBLESHOOTING.md with how to merge your settings back |
| Docker Hub limits free image downloads per IP | Outside our control; TROUBLESHOOTING.md explains `docker login` or using GitHub's registry |

## How it was proven

- 72 automated unit tests.
- A 53-step end-to-end test on real Coolify. It installs, logs in through the real login page, blocks an attacker's sign-up, deploys an app through the proxy, runs ATTa's exact deploy call, upgrades, wipes the whole server, reinstalls, restores, and checks everything came back.
- The same test passes on a clean GitHub machine against Coolify's live download servers.
