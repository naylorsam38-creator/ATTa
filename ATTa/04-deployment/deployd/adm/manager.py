"""The deploy itself (v117). Used by deployd (queue) and deployctl (direct). One deploy at a time, server-wide.

    created -> validating   authorised? bundle staged, checked, its own tests pass (as an unprivileged user)
            -> building     what is live now is recorded as the rollback target (verified releases only);
                            `bash run` from the bundle, as a stoppable tree holding the deploy lock:
                            bootstrap.sh prepares the new release OFF to the side (packages, Docker, browser,
                            a smoke test of the new gateway) and records `built`
            -> health_checking  bootstrap.sh snapshots units + nginx, switches, restarts, and checks ATTa through
                            nginx and a real browser; on any failure it restores the snapshot exactly
            -> verified     bootstrap.sh's checks passed
            -> live         ADM's OWN independent check passed too; only now does the release become known-good

Any failure: failed / timed_out / interrupted, and then a rollback to EXACTLY the release that was live before
(the one recorded at the start, which had passed its own verification), only after every process of the failed
deploy is confirmed stopped. Rollback, in order of preference: bootstrap already restored it (ADM re-verifies) ->
the snapshot's restore.sh (exact: units, nginx, symlink) -> re-running the known-good bundle -> the backup.

A deploy that was killed (deployd crashed, server rebooted, deployctl ^C'd) is never re-run: the next deployd or
deployctl finds it (RUNNING/, or a record that never finished), stops whatever survived it, marks it
`interrupted`, and rolls back if it had switched anything."""
from pathlib import Path
import hashlib, json, os, traceback
from . import (config, journal, queue, staging, backup, activation, health, authz, deployment, releases, proc)
from .lock import DeployLock, Busy  # noqa: F401  (Busy re-exported for deployd/deployctl)

def _identity():
    import sys
    p = str(Path(__file__).resolve().parents[2])
    if p not in sys.path:
        sys.path.insert(0, p)
    import atta_identity
    return atta_identity


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _version_of(release):
    try:
        return str(json.loads((Path(release) / "release.json").read_text()).get("version", "?"))
    except (OSError, ValueError):
        return "?"


def _same(a, b):
    try:
        return a is not None and b is not None and Path(a).resolve() == Path(b).resolve()
    except OSError:
        return False


def _live_code():
    return releases.live(config.APP)


def _live_summary():
    cur = _live_code()
    if cur is None:
        return None
    return {"release": str(cur), "version": releases.version_of(cur), "verified": releases.is_verified(cur)}


def _check_release(release, log):
    """Health of `release` as the live one, with the checks IT supports (identity proof, or legacy)."""
    legacy = not _identity().has_identity(Path(release))
    fails = health.check(expect_release=None if legacy else Path(release).name, legacy=legacy)
    for l in fails:
        log.write(l + "\n")
    log.flush()
    return fails


def previous_live(job, log):
    """What is live BEFORE this deploy, and whether it may be returned to. Called under the lock, before anything
    changes. The rollback target is this record, never "the folder before the newest"."""
    cur = _live_code()
    if cur is None:
        log.write("previous live: none (first installation on this server)\n")
        return None
    kg = releases.known_good(config.CODE_RELEASES)
    akg = activation.adm_known_good()
    bundle = akg["release"] if akg and _same(akg.get("code_release"), cur) else None
    if kg and _same(kg["release"], cur):
        log.write(f"previous live: {cur.name} (known-good, verified by {kg.get('deployment_id')})\n")
        return {"release": str(cur), "version": kg.get("version"), "verified": True, "adopted": kg.get("adopted", False),
                "code_sha256": kg.get("code_sha256"), "bundle": bundle}
    # Live code is not the recorded known-good: a server installed by an older ATTa, or changed by hand. It becomes
    # a rollback target only if it passes its health check right now.
    log.write(f"previous live: {cur.name} is not a recorded known-good release; checking it before trusting it\n")
    fails = _check_release(cur, log)
    if fails:
        log.write("previous live: NOT healthy, so there is no verified version to roll back to\n")
        return {"release": str(cur), "version": releases.version_of(cur), "verified": False, "reason": "; ".join(fails)}
    releases.mark_verified(config.APP, cur, job, adopted=True)
    rec = releases.promote(config.CODE_RELEASES, config.APP, cur, job)
    log.write(f"previous live: {cur.name} was healthy; recorded as known-good (adopted)\n")
    return {"release": str(cur), "version": rec["version"], "verified": True, "adopted": True,
            "code_sha256": rec["code_sha256"], "bundle": bundle}


def _fail(job, state, reason, log):
    doc = deployment.get(job)
    log.write(f"FAIL {reason}\n"); log.flush()
    if doc["state"] in deployment.FAILURES:
        if reason not in (doc.get("failure_reason") or ""):
            deployment.set_fields(job, failure_reason=f"{doc.get('failure_reason')}; then: {reason}"[:2000])
        return deployment.get(job)
    return deployment.transition(job, state, reason=reason[:2000])


def _finish_failed_candidate(job, log):
    doc = deployment.get(job)
    cand = (doc.get("attempted") or {}).get("release")
    if cand and not _same(cand, _live_code()) and Path(cand).is_dir():
        releases.mark_failed(cand, job, doc.get("failure_reason") or "deploy failed")
    kept = [r for r in ((doc.get("previous_live") or {}).get("release"),) if r]
    for r in releases.prune(config.CODE_RELEASES, config.APP, config.KEEP_RELEASES, protect=kept):
        log.write(f"pruned {r}\n")


def _rollback_succeeded(job, method, target, log):
    cur = _live_code()
    final = {"release": str(cur), "version": releases.version_of(cur), "code_sha256": releases.tree_sha256(cur)}
    deployment.set_rollback(job, deployment.SUCCEEDED, method=method, target=target["release"])
    deployment.transition(job, "rolled_back", final_live=final)
    log.write(f"ROLLED_BACK to {Path(target['release']).name} ({method}); live again: {final['version']}\n")
    _finish_failed_candidate(job, log)
    return "ROLLED_BACK"


def _rollback_failed(job, reason, log, result=deployment.ROLLBACK_FAILED):
    deployment.set_rollback(job, result, reason=reason[:2000])
    deployment.set_fields(job, final_live=_live_summary())
    log.write(f"FAIL rollback: {reason}\nROLLBACK_FAILED\n"); log.flush()
    return "ROLLBACK_FAILED"


def _settle(sub, reason):
    """A helper record (a rollback attempt) that did not reach `live` must still end settled, or recovery would
    keep finding it. Its outcome is on the parent deployment's record."""
    d = deployment.get(sub)
    if not d or deployment.is_final(d):
        return
    if d["state"] not in deployment.FAILURES:
        deployment.transition(sub, "failed", reason=reason)
    deployment.set_rollback(sub, deployment.NOT_NEEDED, reason="rollback attempt; see the parent deployment")


def rollback(job, log, lock_fd, cancel=None):
    """Put back EXACTLY the release recorded as live (and verified) before this deployment. The caller has confirmed
    that no process of the failed deploy is still running. Callers pass no `cancel`: a rollback, once started, runs to
    its end (or ROLLBACK_TIMEOUT); only deployd's shutdown defers it, before it starts, to the next recovery."""
    doc = deployment.get(job)
    target = doc.get("previous_live")
    cur = _live_code()
    reached = [h["state"] for h in doc.get("state_history") or []]
    switched = "health_checking" in reached or (target is not None and cur is not None
                                                and not _same(cur, target["release"]))
    if not switched and (target is None or _same(cur, target["release"])):
        deployment.set_rollback(job, deployment.NOT_NEEDED, reason="it failed before anything live was switched")
        deployment.set_fields(job, final_live=_live_summary())
        log.write("nothing live was switched: no rollback needed\n")
        _finish_failed_candidate(job, log)
        return deployment.verdict_of(deployment.get(job))
    if not target or not target.get("verified"):
        return _rollback_failed(job, "there is no verified release to return to "
                                     f"({(target or {}).get('reason') or 'nothing was live before'})", log)
    deployment.set_rollback(job, deployment.PENDING, target=target["release"])
    log.write(f"=== rollback to {Path(target['release']).name}\n"); log.flush()
    # 1. bootstrap.sh restored it itself (it does, on any failure after switching): verify that independently.
    if _same(_live_code(), target["release"]) and (doc.get("local_restore") or {}).get("result") == "verified":
        if not _check_release(target["release"], log):
            return _rollback_succeeded(job, "restored by bootstrap.sh, verified by ADM", target, log)
    # 2. the snapshot bootstrap.sh took before switching: restore.sh puts units, nginx and the symlink back exactly.
    txn = doc.get("transaction")
    if txn and (Path(txn) / "restore.sh").is_file():
        res = activation.run_restore(txn, log, job=job, lock_fd=lock_fd, cancel=cancel)
        if not res.stopped:
            return _rollback_failed(job, "restore.sh could not be stopped", log, deployment.BLOCKED)
        if res.returncode == 0 and _same(_live_code(), target["release"]) and not _check_release(target["release"], log):
            return _rollback_succeeded(job, "snapshot restore", target, log)
        log.write("snapshot restore did not bring the previous release back healthy; trying the known-good bundle\n")
    # 3. re-run the known-good bundle's own `bash run` (slow; reinstalls that exact code, then it is checked by hash).
    bundle = target.get("bundle")
    if bundle and (Path(bundle) / "run").is_file():
        sub = f"{job}-rollback"
        if not deployment.get(sub):
            deployment.create(sub, kind="rollback", rollback_of=job, previous_live=_live_summary(),
                              attempted={"version": target.get("version"), "bundle": bundle})
        res = activation.run_bootstrap(bundle, log, job=sub, lock_fd=lock_fd, cancel=cancel,
                                       timeout=config.ROLLBACK_TIMEOUT)
        if not res.stopped:
            return _rollback_failed(job, "the known-good bundle's installer could not be stopped", log, deployment.BLOCKED)
        cur = _live_code()
        same_code = cur is not None and target.get("code_sha256") and releases.tree_sha256(cur) == target["code_sha256"]
        if res.returncode == 0 and same_code and not _check_release(cur, log):
            sd = deployment.get(sub)
            if sd and sd.get("state") == "verified":
                releases.promote(config.CODE_RELEASES, config.APP, cur, sub)
                deployment.transition(sub, "live", final_live=_live_summary())
            return _rollback_succeeded(job, "re-ran the known-good bundle (same code, checked by hash)", target, log)
        if res.returncode == 0 and not same_code:
            log.write("FAIL the known-good bundle installed different code than was live before; not accepted\n")
        _settle(sub, "this rollback attempt did not bring the previous release back healthy")
    # 4. the backup of the live code folder taken before this deploy.
    b = doc.get("backup")
    if b and (Path(b) / "bootstrap.sh").is_file():
        res = activation.run_bootstrap_dir(b, log, job=f"{job}-backup", lock_fd=lock_fd, cancel=cancel)
        cur = _live_code()
        if (res.stopped and res.returncode == 0 and cur is not None and target.get("code_sha256")
                and releases.tree_sha256(cur) == target["code_sha256"] and not _check_release(cur, log)):
            return _rollback_succeeded(job, "backup", target, log)
    return _rollback_failed(job, "no rollback method brought the previous release back healthy (see the log)", log)


def _after_tree(job, res, log, lock_fd, cancel, shutting_down):
    """Decide what the installer's end means: timed out / interrupted / failed / verified."""
    doc = deployment.get(job)
    if res.timed_out or res.interrupted:
        kind = "timed_out" if res.timed_out else "interrupted"
        why = (f"bash run did not finish within {config.DEPLOY_TIMEOUT}s" if res.timed_out
               else "the deploy was stopped (deployd shutting down or cancelled)")
        _fail(job, kind, f"{why} (reached: {doc['state']})", log)
        if not res.stopped:
            return _rollback_failed(job, "processes of the deploy could not be stopped: "
                                    + "; ".join(f"{p} {c}" for p, c in res.survivors), log, deployment.BLOCKED)
        if shutting_down():
            deployment.set_rollback(job, deployment.PENDING, reason="deployd was stopped; the rollback runs when it "
                                                                    "starts again (nothing is left running)")
            log.write("INTERRUPTED: rollback deferred to the next start of deployd\n")
            return "INTERRUPTED"
        return rollback(job, log, lock_fd)
    if not res.ok or doc["state"] != "verified":
        if doc["state"] in deployment.FAILURES:
            reason = doc.get("failure_reason") or "bootstrap.sh reported a failure"
        elif res.returncode:
            reason = f"bash run exited {res.returncode} while '{doc['state']}' (see the log)"
        elif res.leftovers:
            reason = "bash run left processes running after it exited"
        else:
            reason = f"bash run exited 0 but the deployment is '{doc['state']}', not verified"
        _fail(job, "failed", reason, log)
        if not res.stopped:
            return _rollback_failed(job, "processes left by the deploy could not be stopped", log, deployment.BLOCKED)
        return rollback(job, log, lock_fd)
    return None


def process(meta, lk=None, cancel=None, shutting_down=lambda: False):
    """Run one claimed (or deployctl's own) job to a verdict. Returns the verdict string.
    lk: the DeployLock already held by the caller (deployd/deployctl); taken here if None (raises lock.Busy)."""
    job = meta["job_id"]
    config.ensure_dirs()
    own = lk is None
    if own:
        lk = DeployLock(config.LOCK, job).acquire()
    logp = config.LOGS / f"{job}.log"
    try:
        with open(logp, "a") as log:
            try:
                return _process(meta, lk, cancel, shutting_down, log, logp)
            except Exception as e:   # a bug in ADM itself: the record must still end up honest
                log.write(f"FAIL ADM error: {e}\n{traceback.format_exc()}"); log.flush()
                doc = deployment.get(job)
                if doc and not deployment.is_final(doc):
                    _fail(job, "failed", f"ADM error: {e}", log)
                    try:
                        return rollback(job, log, lk.fd)
                    except Exception as e2:
                        log.write(f"FAIL rollback error: {e2}\n{traceback.format_exc()}")
                        return _rollback_failed(job, f"ADM error during rollback: {e2}", log)
                return deployment.verdict_of(doc) if doc else "FAILED"
    finally:
        staging.discard(job)
        doc = deployment.get(job)
        if doc is None or deployment.is_final(doc):
            queue.finish(meta)
        if own:
            lk.release()


def _process(meta, lk, cancel, shutting_down, log, logp):
    job = meta["job_id"]
    doc = deployment.get(job)
    if doc is None or "state" not in doc:
        src = {"original_name": meta.get("original_name")}
        try:
            src["bundle_sha256"] = _sha256(meta["archive"])
        except (OSError, KeyError):
            pass
        deployment.create(job, kind="adm", origin=meta.get("origin"), requested_by=meta.get("requested_by"),
                          source=src, build_id=meta.get("build_id"), log=str(logp))
    if lk.stale_owner:
        journal.record(job, "STALE_LOCK_OWNER", owner=lk.stale_owner)
        log.write(f"note: an earlier deploy ({lk.stale_owner.get('deployment_id')}) ended without releasing its "
                  "owner record; it was not running (the lock was free)\n")
    deployment.transition(job, "validating")
    try:
        ok, why = authz.allowed(meta)
        if not ok:
            raise staging.BundleRejected(f"not authorised: {why}")
        staged = staging.extract(meta["archive"], job)
        root = staging.find_root(staged)
        version = staging.check(root)
        journal.record(job, "CHECKED", version=version, root=str(root))
        log.write(f"checked bundle version {version}\n")
        if config.RUN_TESTS:
            ran = staging.run_tests(root, log, cancel=cancel)
            journal.record(job, "TESTED", result=ran)
        else:
            journal.record(job, "TESTS_SKIPPED", reason="ATTA_ADM_RUN_TESTS=0")
    except staging.BundleRejected as e:
        _fail(job, "failed", f"bundle rejected: {e}", log)
        deployment.set_rollback(job, deployment.NOT_NEEDED, reason="nothing live was touched")
        deployment.set_fields(job, touched_live=False, final_live=_live_summary())
        log.write("FAILED (nothing live was touched)\n")
        return "FAILED"
    except proc.Cancelled as e:
        _fail(job, "interrupted", str(e), log)
        deployment.set_rollback(job, deployment.NOT_NEEDED, reason="nothing live was touched")
        return deployment.verdict_of(deployment.get(job))
    try:
        rj = json.loads((root / "release.json").read_text())
    except (OSError, ValueError):
        rj = {}
    prev = previous_live(job, log)
    deployment.set_fields(job, previous_live=prev, attempted={"version": version},
                          source={"source_commit": rj.get("source_commit"), "manifest_sha256": rj.get("manifest_sha256")})
    b = backup.snapshot(job)
    journal.record(job, "BACKED_UP", backup=str(b) if b else None)
    bundle_release = activation.promote(root, job, version)
    deployment.set_fields(job, attempted={"bundle": str(bundle_release)})
    deployment.transition(job, "building")
    try:
        activation.wait_for_pipeline(log, cancel)
    except proc.Cancelled as e:
        _fail(job, "interrupted", str(e), log)
        return rollback(job, log, lk.fd)
    res = activation.run_bootstrap(
        bundle_release, log, job=job, lock_fd=lk.fd, cancel=cancel,
        on_start=lambda pid, pgid: deployment.set_fields(job, **deployment.process_fields(pid, pgid)))
    deployment.set_fields(job, process={"exit": res.returncode, "seconds": res.seconds, "timed_out": res.timed_out,
                                        "interrupted": res.interrupted, "stopped": res.stopped,
                                        "signalled": len(res.signalled), "escalated": res.escalated,
                                        "leftovers": [c for _, c in res.leftovers][:20],
                                        "survivors": [c for _, c in res.survivors][:20]})
    verdict = _after_tree(job, res, log, lk.fd, cancel, shutting_down)
    if verdict is not None:
        return verdict
    # bootstrap.sh says verified. ADM's own, independent check decides.
    doc = deployment.get(job)
    cand = (doc.get("attempted") or {}).get("release")
    if not cand or not _same(cand, _live_code()):
        _fail(job, "failed", f"bootstrap.sh reported verified, but the live code is {_live_code()} not {cand}", log)
        return rollback(job, log, lk.fd)
    fails = _check_release(cand, log)
    if fails:
        _fail(job, "failed", "ADM's independent check failed: " + "; ".join(fails), log)
        return rollback(job, log, lk.fd)
    try:
        releases.promote(config.CODE_RELEASES, config.APP, cand, job)
    except releases.ReleaseError as e:
        _fail(job, "failed", f"could not record the release as known-good: {e}", log)
        return rollback(job, log, lk.fd)
    activation.record_live(bundle_release, version, job, cand)
    deployment.transition(job, "live", final_live={"release": str(cand), "version": version,
                                                    "code_sha256": releases.tree_sha256(cand)})
    removed = backup.prune() + releases.prune(config.CODE_RELEASES, config.APP, config.KEEP_RELEASES)
    journal.record(job, "PRUNED", pruned=removed)
    log.write(f"DEPLOYED {version}\n")
    return "DEPLOYED"


def recover(lk, log_to=print, cancel=None):
    """Under the deploy lock: deal with every deploy that ended without finishing (killed/crashed/rebooted).
    Each is marked `interrupted` (never re-run), whatever survived it is stopped, and the previous release is put
    back if it had switched anything. Returns [(job, verdict)]."""
    done = []
    ids = [m.get("job_id") for m in queue.running()] + [d["job_id"] for d in deployment.unfinished()]
    for job in dict.fromkeys(i for i in ids if i):
        logp = config.LOGS / f"{job}.log"
        with open(logp, "a") as log:
            log.write(f"=== recovery by pid {os.getpid()}: this deploy ended without finishing\n")
            strays = proc.find_by_env("ATTA_DEPLOYMENT_ID", job)
            if strays:
                r = proc.stop_pids(strays, grace=config.KILL_GRACE)
                log.write(f"stopped {len(r.signalled)} process(es) still running for it\n")
                if not r.stopped:
                    if deployment.get(job) and not deployment.is_final(deployment.get(job)):
                        if deployment.get(job)["state"] not in deployment.FAILURES:
                            deployment.transition(job, "interrupted", reason="the deploy process died")
                        _rollback_failed(job, "processes of the dead deploy could not be stopped", log, deployment.BLOCKED)
                    done.append((job, "ROLLBACK_FAILED"))
                    continue
            doc = deployment.get(job)
            verdict = None
            if doc is None or "state" not in doc:
                journal.record(job, "INTERRUPTED", reason="claimed but never started")
                verdict = "INTERRUPTED"
            else:
                if doc["state"] not in deployment.FAILURES and not deployment.is_final(doc):
                    deployment.transition(job, "interrupted", reason=f"the deploy process stopped while '{doc['state']}'"
                                                                     " (killed, crashed, or the server restarted)")
                doc = deployment.get(job)
                if not deployment.is_final(doc):
                    try:
                        verdict = rollback(job, log, lk.fd)
                    except Exception as e:
                        log.write(f"FAIL recovery rollback error: {e}\n{traceback.format_exc()}")
                        verdict = _rollback_failed(job, f"recovery error: {e}", log)
                else:
                    verdict = deployment.verdict_of(doc)
            meta = next((m for m in queue.running() if m.get("job_id") == job), {"job_id": job})
            queue.finish(meta)
            log.write(f"recovered: {verdict}\n")
        log_to(f"recovered job {job}: {verdict}")
        done.append((job, verdict))
    return done


def self_hash():
    h = hashlib.sha256()
    for p in sorted(Path(__file__).parent.glob("*.py")) + [Path(__file__).parent.parent / "deployd.py"]:
        try:
            h.update(p.read_bytes())
        except OSError:
            pass
    return h.hexdigest()


def manual_rollback(job, target, lk):
    """`deployctl rollback`: go back to the previous known-good release, as a deployment of its own. Its code is
    reinstalled from that release's own folder (bootstrap.sh inside it) and must hash-match the recorded code
    before it can go live; if anything fails, the normal rollback returns to what was live when this started."""
    config.ensure_dirs()
    logp = config.LOGS / f"{job}.log"
    with open(logp, "a") as log:
        cur = _live_code()
        kg = releases.known_good(config.CODE_RELEASES)
        prev = None
        if cur is not None:
            prev = {"release": str(cur), "version": releases.version_of(cur), "verified": releases.is_verified(cur),
                    "code_sha256": (kg or {}).get("code_sha256") if kg and _same(kg["release"], cur) else None}
        deployment.create(job, kind="rollback", origin="local", requested_by="deployctl", previous_live=prev,
                          attempted={"version": target.get("version"), "from_release": target["release"]}, log=str(logp))
        deployment.transition(job, "validating")
        src = Path(target["release"])
        if not (src / "bootstrap.sh").is_file() or releases.tree_sha256(src) != target.get("code_sha256"):
            _fail(job, "failed", f"{src.name} is missing or changed since it was verified; refusing to reinstall it", log)
            deployment.set_rollback(job, deployment.NOT_NEEDED, reason="nothing live was touched")
            return "FAILED"
        deployment.transition(job, "building")
        res = activation.run_bootstrap_dir(src, log, job=job, lock_fd=lk.fd, timeout=config.DEPLOY_TIMEOUT)
        verdict = _after_tree(job, res, log, lk.fd, None, lambda: False)
        if verdict is not None:
            return verdict
        doc = deployment.get(job)
        new = _live_code()
        if new is None or releases.tree_sha256(new) != target.get("code_sha256"):
            _fail(job, "failed", "the reinstalled code does not match the known-good release's recorded hash", log)
            return rollback(job, log, lk.fd)
        fails = _check_release(new, log)
        if fails:
            _fail(job, "failed", "ADM's independent check failed: " + "; ".join(fails), log)
            return rollback(job, log, lk.fd)
        releases.promote(config.CODE_RELEASES, config.APP, new, job)
        deployment.transition(job, "live", final_live={"release": str(new), "version": releases.version_of(new),
                                                        "code_sha256": target.get("code_sha256")})
        log.write(f"DEPLOYED {releases.version_of(new)} (rolled back by hand to {src.name})\n")
        return "DEPLOYED"


def reclaim_abandoned(log_to=print):
    """The deploy lock is held, but by whom? If its owner record names a process that is gone (pid + start time +
    host all checked), the holders are what is left of a KILLED deployment (the lock fd was passed down to its
    installer). They are found exactly — by ATTA_DEPLOYMENT_ID=<that deployment> in their environment — and stopped,
    so the lock frees and recovery can mark it interrupted and roll back. A lock held by a live owner, or by
    processes that can't be identified, is never touched. Returns the proc.TreeResult, or None if nothing was done."""
    from . import lock as _lock
    held, owner = _lock.holder(config.LOCK)
    if not held or not owner or _lock.owner_alive(owner) or not owner.get("deployment_id"):
        return None
    strays = proc.find_by_env("ATTA_DEPLOYMENT_ID", owner["deployment_id"])
    if not strays:
        return None
    log_to(f"deploy lock held by what is left of deployment {owner['deployment_id']} (its owner pid {owner.get('pid')} "
           f"is gone): stopping {len(strays)} process(es)")
    return proc.stop_pids(strays, grace=config.KILL_GRACE)
