"""The run-2 script updates, each tested as the CLASS of failure it is a rule for (not the app it came from).

  billionmail, tidb   a port answers but isn't a website       -> no web answer here, never a runner crash
  krayin-crm, flarum  "No space left on device" (any case)     -> disk.full: clear space, retry the SAME part
  graphite            a download broke off mid-build           -> net.transient: pause, retry the SAME part once
  colanode            page drawn by its own JS, blank at load  -> browser waits APP_BUILDER_CONTENT_WAIT for it
  plane               skin link missing in the browser only    -> the failure records where the browser ended up,
                                                                  what page was served, and what <head> holds
Plus the feedback loop: every failure is in evidence.jsonl and `replay` re-diagnoses it under today's rules.
"""
import json
import pytest

import app_runner as ar
import fakes


# ------------------------------------------------------------------ 1. a port that isn't a website
@pytest.mark.parametrize("banner", [b"220 mail.example ESMTP Postfix\r\n",                 # SMTP (billionmail)
                                    b"J\x00\x00\x00\n8.0.11-TiDB-v7.5.0\x00\x01\x02\x03",   # MySQL handshake (tidb)
                                    b"* OK [CAPABILITY IMAP4rev1] Dovecot ready.\r\n"],       # IMAP
                         ids=["smtp", "mysql", "imap"])
def test_non_http_port_is_no_web_answer_not_a_crash(banner):
    assert ar.http_probe(fakes.banner_server(banner)) == (None, ar.NOT_HTTP)


def test_binary_junk_is_no_web_answer_not_a_crash():
    # Depending on timing this is a protocol error or a reset connection: either way, no answer, no raise.
    for _ in range(5):
        code, _ctype = ar.http_probe(fakes.banner_server(b"\x00\x01\x02\xff" * 300))
        assert code is None


def test_closed_and_real_http_ports_unchanged():
    import socket
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    assert ar.http_probe(port) == (None, "")
    code, ctype = ar.http_probe(int(fakes.skin_proxy().rsplit(":", 1)[1]))
    assert code == 200 and "html" in ctype


def _fake_app(monkeypatch, ports_by_name):
    rows = [{"ID": n, "Names": n, "Image": n, "State": "running", "Status": "Up 5 seconds",
             "Ports": f"127.0.0.1:{p}->{p}/tcp"} for n, p in ports_by_name.items()]
    monkeypatch.setattr(ar, "_containers", lambda app: rows)
    monkeypatch.setattr(ar, "_logs", lambda app, part, tail=200: "")
    monkeypatch.setattr(ar, "host_memory_low", lambda: False)
    monkeypatch.setattr(ar, "SETTLE_SECONDS", 1)


def test_wait_http_skips_mail_ports_and_finds_the_web_port(monkeypatch):
    smtp = fakes.banner_server(b"220 mail ESMTP\r\n")
    web = int(fakes.skin_proxy().rsplit(":", 1)[1])
    # The mail container carries the app's name, the web one doesn't: the runner probes the mail port
    # FIRST, every time. v109 crashed right there (BadStatusLine); it must be passed over.
    _fake_app(monkeypatch, {"billionmail-core-postfix": smtp, "portal": web})
    port, reason = ar._wait_http("billionmail", {"kind": "compose", "boot_timeout": 30})
    assert port == web and reason == ""


def test_wait_http_names_non_http_ports_when_nothing_else_answers(monkeypatch):
    smtp = fakes.banner_server(b"220 mail ESMTP\r\n")
    _fake_app(monkeypatch, {"mailonly": smtp})
    monkeypatch.setattr(ar, "BOOT_TIMEOUT_MAX", 2)
    port, reason = ar._wait_http("mailonly", {"kind": "compose", "boot_timeout": 1})
    assert port is None and reason.startswith("__STILL_STARTING__")
    assert f"ports [{smtp}] answer, but not in HTTP" in reason


# ------------------------------------------------------------------ 2. disk full, any wording
@pytest.mark.parametrize("text", [
    'ERROR: failed to solve: process "/bin/sh -c composer install" did not complete: write /app/vendor/x: No space left on device',
    "tar: ./node_modules/x: Cannot write: No space left on device\nERROR [build 4/7] RUN npm ci",
    "failed to register layer: write /usr/lib/x: no space left on device",
    "npm ERR! code ENOSPC\nnpm ERR! nospc ENOSPC: no space left on device, write",
    "E: You don't have enough free space in /var/cache/apt/archives/.",
    "fatal: write error: Disk quota exceeded"])
def test_disk_full_matches_any_case_before_build_failed(text):
    assert ar.diagnose(text, "start")["rule"] == "disk.full"
    assert ar.diagnose(text, "boot")["rule"] == "disk.full"


# ------------------------------------------------------------------ 3. a download broke off
NET = [
    "error: failed to download `wasm-bindgen v0.2.92`\nCaused by: [56] Failure when receiving data from the peer",
    'curl: (56) Recv failure: Connection reset by peer\nERROR: failed to solve: process "/bin/sh -c curl" did not complete successfully: exit code: 56',
    "failed to copy: httpReadSeeker: failed open: unexpected EOF",
    "Error response from daemon: Get \"https://registry-1.docker.io/v2/\": net/http: TLS handshake timeout",
    "failed to resolve reference \"docker.io/library/node:22\": dial tcp: lookup registry-1.docker.io: i/o timeout",
    "npm ERR! code ECONNRESET\nnpm ERR! network aborted",
    "error: RPC failed; curl 92 HTTP/2 stream 5 was not closed cleanly\nfatal: early EOF",
    "Could not resolve host: github.com",
]


@pytest.mark.parametrize("text", NET)
def test_broken_download_is_retried_during_start(text):
    assert ar.diagnose(text, "start") == {"rule": "net.transient", "fix": "retry_net"}


@pytest.mark.parametrize("text", NET[:4])
def test_network_words_in_the_running_apps_logs_are_not_a_download_retry(text):
    assert (ar.diagnose(text, "boot") or {}).get("rule") != "net.transient"


@pytest.mark.parametrize("text,rule", [
    ("pull access denied for foo/bar, repository does not exist", "image.missing"),
    ("docker.io/foo/bar:latest: not found: manifest unknown", "image.missing"),
    ('ERROR [build 3/5] RUN make\nmake: *** [all] Error 2\nfailed to solve: exit code: 2', "build.failed"),
    ("toomanyrequests: You have reached your pull rate limit", "registry.rate_limit"),
    ("yaml: line 3: unexpected EOF", "compose.invalid"),
])
def test_real_failures_keep_their_rule(text, rule):
    assert ar.diagnose(text, "start")["rule"] == rule


# ------------------------------------------------------------------ the retry loop itself (Docker faked)
PART_A = {"kind": "image", "image": "a/a:1", "env": {}, "why": "part A"}
PART_B = {"kind": "image", "image": "b/b:1", "env": {}, "why": "part B"}


def _drive(monkeypatch, script):
    """Run run_app with Docker replaced by `script`: a list of (part why, started?, text) per attempt."""
    calls, shell = [], []
    steps = iter(script)
    monkeypatch.setattr(ar, "docker_ok", lambda: (True, ""))
    monkeypatch.setattr(ar, "app_dir", lambda app: ar.LIB)
    monkeypatch.setattr(ar, "parts", lambda app, d: [dict(PART_A), dict(PART_B)])
    monkeypatch.setattr(ar, "down", lambda app, prune=None: "stopped")
    monkeypatch.setattr(ar, "sh", lambda cmd, **k: (shell.append(cmd) or (0, "")))
    monkeypatch.setattr(ar, "NET_RETRY_WAIT", 0)
    state = {}
    def start(app, d, part, notes):
        why, started, text = next(steps)
        calls.append((part["why"], dict(part.get("env") or {})))
        assert part["why"] == why, f"expected {why}, runner tried {part['why']}"
        state["started"], state["text"] = started, text
        return started, ("" if started else text)
    monkeypatch.setattr(ar, "_start", start)
    monkeypatch.setattr(ar, "_wait_http", lambda app, part: (20001, "") if state["text"] == "UP" else (None, state["text"]))
    return calls, shell


def test_disk_full_clears_space_and_retries_the_same_part(monkeypatch):
    calls, shell = _drive(monkeypatch, [("part A", False, "write /x: No space left on device"), ("part A", True, "UP")])
    r = ar.run_app("krayin")
    assert r["ok"] and r["part"]["why"] == "part A"
    assert ["docker", "builder", "prune", "-af"] in shell
    assert r["attempts"][0]["rule"] == "disk.full" and "retrying the same way" in " ".join(r["attempts"][0]["notes"])


def test_disk_full_after_another_fix_still_gets_its_retry(monkeypatch):
    # v109 shared one counter between fixes: a variable filled in first used up the disk-full retry.
    calls, shell = _drive(monkeypatch, [("part A", True, "__EXITED__\nAPP_KEY must be set"),
                                        ("part A", False, "No space left on device"),
                                        ("part A", True, "UP")])
    r = ar.run_app("flarum")
    assert r["ok"] and [c[0] for c in calls] == ["part A"] * 3
    assert "APP_KEY" in calls[1][1]


def test_broken_download_pauses_and_retries_the_same_part_once(monkeypatch):
    slept = []
    monkeypatch.setattr(ar.time, "sleep", lambda s: slept.append(s))
    calls, _ = _drive(monkeypatch, [("part A", False, "curl: (56) Recv failure: Connection reset by peer"),
                                    ("part A", False, "curl: (56) Recv failure: Connection reset by peer"),
                                    ("part B", True, "UP")])
    r = ar.run_app("graphite")
    assert r["ok"] and [c[0] for c in calls] == ["part A", "part A", "part B"]
    assert 0 in slept and r["attempts"][0]["rule"] == "net.transient" and r["attempts"][0]["phase"] == "start"


def test_app_logging_a_reset_is_not_retried_as_a_download(monkeypatch):
    calls, _ = _drive(monkeypatch, [("part A", True, "__EXITED__ 1 container(s) stopped\nread: connection reset by peer"),
                                    ("part B", True, "UP")])
    r = ar.run_app("someapp")
    assert r["ok"] and [c[0] for c in calls] == ["part A", "part B"]
    assert r["attempts"][0]["rule"] == "container.exited"


# ------------------------------------------------------------------ the feedback loop
def test_every_failure_is_recorded_and_replay_rediagnoses_it(monkeypatch, tmp_path):
    ev = tmp_path / "evidence.jsonl"
    rows = [  # as v109 wrote them: no phase, and the capital-N disk failure filed under build.failed
        {"app": "krayin-crm", "part": "p", "rule": "build.failed", "fix": "next_part",
         "raw_tail": "failed to solve: write /app/vendor: No space left on device"},
        {"app": "graphite", "part": "p", "rule": "unrecognised", "fix": "next_part",
         "raw_tail": "Caused by: [56] Failure when receiving data from the peer"},
        {"app": "x", "part": "p", "rule": "unrecognised", "fix": "next_part", "raw_tail": "something nobody has seen yet"},
        {"kind": "check", "app": "plane", "stage": "6 CLEAN", "code": "BROWSER_SKIN_LINK_COUNT", "detail": "{}"},
    ]
    ev.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    out = ar.replay(ev)
    assert out["rows"] == 3
    assert {(c["app"], c["now"]) for c in out["changed"]} == {("krayin-crm", "disk.full"), ("graphite", "net.transient")}
    assert out["unrecognised"] == [{"line": "something nobody has seen yet", "apps": ["x"]}]


def test_browser_failures_go_into_evidence_too():
    before = (ar.RUNNER / "evidence.jsonl").read_text() if (ar.RUNNER / "evidence.jsonl").exists() else ""
    ar._record_check_evidence("plane", {"broken_at": "6 CLEAN", "runner": {"part": "p"},
                                        "stages": {"6 CLEAN": {"status": "FAIL", "code": "BROWSER_SKIN_LINK_COUNT", "detail": '{"count": 0}'}}})
    new = (ar.RUNNER / "evidence.jsonl").read_text()[len(before):]
    row = json.loads(new)
    assert row["kind"] == "check" and row["code"] == "BROWSER_SKIN_LINK_COUNT" and row["detail"] == '{"count": 0}'


def test_learned_network_rules_only_match_download_output(monkeypatch):
    import repair_actions as ra
    monkeypatch.setattr(ar, "LEARNED_RULES", ar.RUNNER / "learned_rules_test.json")
    ra.add_runner_rule("net-proxy-reset", r"proxyconnect tcp: EOF", "retry_net")
    rule = [r for r in ar.learned_rules() if r["id"] == "net-proxy-reset"][0]
    assert rule["phase"] == "start"
    assert ar.diagnose("proxyconnect tcp: EOF", "start")["rule"] == "net-proxy-reset"
    assert (ar.diagnose("proxyconnect tcp: EOF", "boot") or {}).get("rule") != "net-proxy-reset"


# ------------------------------------------------------------------ the reusable runtimes retry their own downloads
def test_generated_runtime_builds_retry_downloads():
    import runtimes
    rust = runtimes._rust_dockerfile({"frontend": "frontend", "wasm_bindgen": "0.2.92", "cargo_about": None,
                                      "binaryen": "130", "build": "cargo run build"})
    assert "CARGO_NET_RETRY" in rust and "npm_config_fetch_retries" in rust and "--retry-all-errors" in rust
    assert "| tar" not in rust   # a retried download is never piped straight into tar
    php = runtimes._php_dockerfile({"php": "8.3", "docroot": "public", "laravel": False, "database": False, "env_example": None})
    assert 'Acquire::Retries' in php
