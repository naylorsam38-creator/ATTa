"""The same run-2 classes through REAL Docker: a real compose app with a mail port, and real BuildKit
builds that stop the way krayin-crm (disk) and graphite (download) did. Skipped where Docker isn't running.
Images come from mirror.gcr.io (Docker Hub's anonymous limit is easily hit on shared machines)."""
import shutil, subprocess
import pytest

import app_runner as ar

BASE = "mirror.gcr.io/library/python:3.12-alpine"


def _docker_ready() -> bool:
    if not shutil.which("docker"):
        return False
    if subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode != 0:
        return False
    return subprocess.run(["docker", "pull", "-q", BASE], capture_output=True, timeout=300).returncode == 0


pytestmark = pytest.mark.skipif(not _docker_ready(), reason="Docker (and the base image) not available")


@pytest.fixture
def library_app(monkeypatch):
    monkeypatch.setattr(ar, "UPSTREAM_SEARCH", False)
    monkeypatch.setattr(ar, "SETTLE_SECONDS", 2)
    monkeypatch.setattr(ar, "NET_RETRY_WAIT", 1)
    made = []
    def make(name, files):
        d = ar.LIB / name
        shutil.rmtree(d, ignore_errors=True)
        for rel, text in files.items():
            (d / rel).parent.mkdir(parents=True, exist_ok=True); (d / rel).write_text(text)
        made.append(name)
        return name
    yield make
    for name in made:
        ar.down(name, prune=False)


MAIL = "import socket\ns=socket.socket();s.setsockopt(1,2,1);s.bind(('',25));s.listen(9)\nwhile 1:\n c,_=s.accept();c.sendall(b'220 mail ESMTP\\r\\n');c.close()\n"


def test_real_compose_app_with_a_mail_port_starts_on_its_web_port(library_app):
    # The mail service carries the app's name, so its port is probed first; its short-form `ports: ["25"]`
    # is published too (v109 dropped short-form ports as if they were internal).
    app = library_app("mailapp", {
        "docker-compose.yml": f"""services:
  mailapp-postfix:
    image: {BASE}
    command: ["python", "-c", {MAIL!r}]
    ports: ["25", "2525:25"]
  portal:
    image: {BASE}
    command: ["sh", "-c", "mkdir -p /w && echo '<html><body>hi</body></html>' > /w/index.html && cd /w && python -m http.server 80"]
    ports: ["8080:80"]
"""})
    r = ar.run_app(app, log=lambda *a: None)
    assert r["ok"], r
    assert ar.http_probe(int(r["url"].rsplit(":", 1)[1].strip("/")))[0] == 200


def _failing_build(message):
    return {"Dockerfile": f'FROM {BASE}\nRUN echo "{message}" >&2 && exit 1\n'}


def test_real_build_stopped_by_full_disk_is_pruned_and_retried(library_app):
    app = library_app("diskapp", _failing_build("write /app/vendor/x: No space left on device"))
    r = ar.run_app(app, log=lambda *a: None)
    rules = [(a["part"], a.get("rule")) for a in r["attempts"]]
    assert not r["ok"]
    assert rules[:2] == [(rules[0][0], "disk.full")] * 2, rules   # same way of starting it, twice
    assert "retrying the same way" in " ".join(r["attempts"][0]["notes"])


def test_real_build_whose_download_broke_off_is_retried_once(library_app):
    app = library_app("netapp", _failing_build("curl: (56) Recv failure: Connection reset by peer"))
    r = ar.run_app(app, log=lambda *a: None)
    rules = [(a["part"], a.get("rule"), a.get("phase")) for a in r["attempts"]]
    assert rules[:2] == [(rules[0][0], "net.transient", "start")] * 2, rules
    assert len(r["attempts"]) == 2   # once retried, then given up on (only one way to start it)
