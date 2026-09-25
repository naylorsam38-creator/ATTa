# ADM — ATTa Deploy Manager

**What it is.** The part that updates ATTa's own code on the server safely. Before ADM, uploading
the ATTa bundle on the web only refreshed the skins package and the Front Door page; the Python
code in `/opt/app-builder` only changed when someone SSH'd in and ran `bash run` again, with no
backup and no way back. ADM closes that gap.

**What it does, every time, in this order.**

```
1. STAGING     extract the zip into adm/staging/<job> — nothing live touched
2. CHECKED     bundle root found (run + release.json + 04-deployment/), required files present,
               every .py in 04-deployment compiles. Fails here = FAILED, nothing live touched
3. BACKED_UP   copy of the live code folder /opt/app-builder -> adm/backups/<time>-<job>
4. ACTIVATING  the bundle is moved whole to adm/releases/<job>-<version>, then ITS OWN `bash run`
               is executed (bootstrap.sh: copy code in, rewrite units, restart services, its own gate).
               Waits first until no build is running, so the restart never kills a build.
5. HEALTHY     independent check from outside: all services active, gateway /health = OK, nginx answers
6. DEPLOYED    adm/current -> this release, adm/previous -> the one before; old releases/backups pruned

Any failure after step 4:  ROLLING_BACK -> previous release's `bash run` (or the backup's
bootstrap.sh) -> health -> ROLLED_BACK.  If even that fails: ROLLBACK_FAILED (a human is needed).
```

Every job has a journal (`adm/state/journal/<job>.json`) and a full log (`adm/logs/<job>.log`).
Read the log the usual way: the **last line** is the outcome, any **FAIL** lines say why.

## Where things are

| Path | What |
|---|---|
| `/opt/app-builder` | v114.1: a **symlink** to the live code folder in `/opt/app-builder-releases/<version>-<time>/`. Every `bash run` installs into a fresh folder and switches the link in one step; the last 3 (plus the previous one) are kept |
| `/opt/app-builder/deployd/` | ADM code (`deployd.py` daemon, `deployctl` CLI, `adm/` modules) |
| `/srv/app-builder/adm/` | ADM's own data: `incoming/ staging/ releases/ backups/ logs/ state/queue state/journal current previous` |
| `/opt/app-builder/release.json` | written on every deploy: which version is live and which job put it there |
| `/etc/systemd/system/atta-deployd.service` | the daemon, installed and enabled by bootstrap.sh |
| `/usr/local/bin/deployctl` | symlink so `deployctl` works from any folder |

All settings (paths, timeouts, how many releases/backups to keep, poll interval) are in one
labelled block at the top of `04-deployment/deployd/adm/config.py`.

## Two ways a deploy starts

1. **Web upload.** A logged-in **admin** uploads the ATTa bundle (v114: from any other account the upload is
   refused before the skins package, Front Door page or code are touched, and ADM re-checks the account itself) on *Add an app*. The pipeline does what
   it always did (skins, Front Door page, catalogue, checks) and, on the server, queues the same zip
   with ADM. The build page gets a **System update** section showing the ADM job, its verdict and log.
   ADM waits for that build to finish before restarting anything.
2. **Terminal.** `sudo deployctl deploy /path/to/ATTa-deployNNN.zip` — same steps, runs in front of
   you, exits 0 on DEPLOYED and 1 otherwise. Or drop a zip into `/srv/app-builder/adm/incoming/`.

Only one deploy can run at a time (file lock). The daemon and the CLI never collide; the CLI just
reports the lock if the daemon is mid-deploy.

### Who may deploy (v115)

Authority comes from **where a job came from**, never from a name written in it:

- Every queued job records an `origin`: `web` (uploaded on the web) or `local` (queued on the server by
  root: `deployctl deploy`, a zip in `incoming/`, or a zip root placed in the pipeline inbox).
- A `web` job runs only if the account that uploaded it is an enabled **admin**.
- Every job, of either origin, must sit in `state/queue/` as a root-owned file with one link, and that
  folder must be `0700 root` (deployd sets this on start). Anyone able to write the queue could otherwise
  forge any job. `incoming/` is only read while nobody but root can write it.
- A job with no `origin` (queued by an older ATTa) or an unknown one is refused: queue it again.
- The names `system`, `incoming`, `deployctl`, `local` and `root` cannot be accounts. An existing account
  with one of them is disabled when the gateway starts, and an alert says so. New accounts also may not
  start with `admin-`.
- A zip placed straight into `/srv/app-builder/inbox/` with no build record counts as the server's own
  only if root put it there and the inbox is not writable by anyone else; otherwise it is refused. For
  system updates prefer `sudo deployctl deploy`.

## deployctl

```
sudo deployctl deploy <bundle.zip>   stage, check, back up, activate, health-check; rolls back on failure
sudo deployctl rollback              put the previous release back (or the latest backup)
deployctl status                     what is live, what was live before, last job + verdict
deployctl releases                   kept releases, newest first, LIVE marked
deployctl jobs                       every journaled job with its verdict
deployctl journal <job>              full event list for one job
deployctl log <job>                  FAIL lines + last line of that job's bash-run log
```
Every command ends with one `RESULT:` line. Read that first.

## First time on the existing v111a server

ADM is not on the box yet, so the first install is the one manual step:

```
unzip ATTa-deploy111a-ADM-integrated.zip && cd ATTa && sudo bash run
```
That runs the normal bootstrap, which now also installs and starts `atta-deployd`. From then on
every later bundle goes through ADM (web upload or `deployctl deploy`).

## Test checklist (run on the server, real output only)

Each step names what to look at. Tick it only when that exact line is seen.

```
 1. sudo bash run                              -> last line "Open http://<EC2-IP>/ ..." and "DEPLOYMENT VERIFIED" above it
 2. systemctl is-active atta-deployd           -> active
 3. deployctl status                           -> RESULT: NO_JOBS_YET  (live code folder shows "no release.json — deployed before ADM")
 4. sudo deployctl deploy <this same zip>      -> RESULT: DEPLOYED v111a+ADM
 5. deployctl status                           -> live: v111a+ADM ; previous: -  ; RESULT: DEPLOYED
 6. deployctl log <job from step 4>            -> no FAIL lines ; RESULT: DEPLOYED v111a+ADM
 7. sudo deployctl deploy <this same zip>      -> RESULT: DEPLOYED ; then deployctl status shows previous: v111a+ADM
 8. sudo deployctl rollback                    -> RESULT: ROLLED_BACK now live: v111a+ADM ; all four app-builder/nginx services active
 9. Bad bundle: copy the zip, edit any .py inside 04-deployment to contain a syntax error, re-zip,
    sudo deployctl deploy bad.zip              -> RESULT: FAILED ; log ends "FAILED (nothing live was touched)" ; site still up
10. Web path: log in, Add an app, upload the bundle zip -> build page shows "System update" with
    verdict DEPLOYED once the build finishes; journalctl -u atta-deployd shows "job ...: DEPLOYED"
11. Failing deploy with rollback: make a bundle whose 04-deployment/gateway.py compiles but exits
    at start (e.g. add `raise SystemExit(1)` as its first line). sudo deployctl deploy it
                                               -> RESULT: ROLLED_BACK ; deployctl status live = the version from before ; gateway /health = OK
12. deployctl releases                         -> at most KEEP_RELEASES (5) rows, LIVE marked on the right one
```

Step 11 is the one that proves ADM earns its place: the site is still up after a broken bundle.

## What ADM does not do

- It never edits app source, library apps, builds, or the pipeline's records. The pipeline is the
  only writer of build records; ADM writes its own journal and the build page reads it.
- It does not re-implement the deploy. Activation *is* the bundle's own `bash run`. If bootstrap.sh
  changes, ADM automatically runs the new one.
- It is not run by Claude. Delivered untested (rule of 2026-09-22 / 2026-09-24); the checklist
  above is the test, on the server, by Sam's system.
