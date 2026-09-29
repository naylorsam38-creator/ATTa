"""v117 tests: every qualified app gets its own Coolify service, ATTa runs in a container under
Coolify and fetches the whole app list on first start. No Docker, no network, no root needed:
Coolify is a fake HTTP server on 127.0.0.1 that answers like the supplied 4.3.23 source.

    cd ATTa && python3 -m unittest discover -s tests -v
"""
import base64, importlib, json, os, sys, tempfile, threading, unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEP = HERE.parent / "04-deployment"
TMP = Path(tempfile.mkdtemp(prefix="atta-v117-"))
os.environ.setdefault("APP_BUILDER_ROOT", str(TMP / "root"))
os.environ.setdefault("ATTA_ADM_ROOT", str(TMP / "adm"))
sys.path[:0] = [str(DEP), str(DEP / "deployd")]

import yaml  # noqa: E402
import builds, app_runner, coolify_handoff as h, coolify_provision as prov, pipeline, container_main  # noqa: E402


class FakeCoolify:
    """Just enough of Coolify's API. Lists are plain JSON arrays, as the real one answers."""

    def __init__(self):
        self.projects, self.services, self.calls, self.fail_create = [], [], [], False
        me = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body):
                raw = json.dumps(body).encode()
                self.send_response(code); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n) or b"{}") if n else {}

            def do_GET(self):
                me.calls.append(("GET", self.path))
                if self.path == "/api/v1/servers":
                    return self._send(200, [{"uuid": "srv-other", "name": "edge"}, {"uuid": "srv-local", "name": "localhost"}])
                if self.path == "/api/v1/projects":
                    return self._send(200, me.projects)
                if self.path == "/api/v1/services":
                    return self._send(200, me.services)
                self._send(404, {"message": "not found"})

            def do_POST(self):
                b = self._body(); me.calls.append(("POST", self.path, b))
                if self.path == "/api/v1/projects":
                    p = {"uuid": f"prj-{len(me.projects) + 1}", "name": b["name"]}; me.projects.append(p)
                    return self._send(201, {"uuid": p["uuid"]})
                if self.path == "/api/v1/services":
                    if me.fail_create:
                        return self._send(500, {"message": "boom"})
                    base64.b64decode(b["docker_compose_raw"], validate=True)   # Coolify insists on base64
                    s = {"uuid": f"svc-{len(me.services) + 1}", "name": b["name"], "compose": b["docker_compose_raw"]}
                    me.services.append(s)
                    return self._send(201, {"uuid": s["uuid"], "domains": [f"http://{b['name']}.1.2.3.4.sslip.io"]})
                if self.path.startswith("/api/v1/deploy?"):
                    uuid = self.path.split("uuid=")[1].split("&")[0]
                    # DeployController for a Service: no deployment_uuid, just a "started" message.
                    return self._send(200, {"deployments": [{"message": f"Service {uuid} started. It could take a while, be patient.",
                                                             "resource_uuid": uuid}]})
                self._send(404, {"message": "not found"})

            def do_PATCH(self):
                b = self._body(); me.calls.append(("PATCH", self.path, b))
                uuid = self.path.rsplit("/", 1)[1]
                for s in me.services:
                    if s["uuid"] == uuid:
                        s["compose"] = b["docker_compose_raw"]
                        return self._send(200, {"uuid": uuid})
                self._send(404, {"message": "not found"})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}"

    def compose(self, i=-1):
        return yaml.safe_load(base64.b64decode(self.services[i]["compose"]))

    def close(self):
        self.srv.shutdown(); self.srv.server_close()


def overlay(app):
    b = app_runner.LIB / app / ".ui-capability" / "ui-bridge"
    b.mkdir(parents=True, exist_ok=True)
    (b / "proxy.js").write_text("// skin proxy")
    (b.parent / "run-ui.sh").write_text("#!/bin/sh")


def recipe(app, **r):
    overlay(app)
    app_runner.RECIPES.mkdir(parents=True, exist_ok=True)
    (app_runner.RECIPES / f"{app_runner.safe_id(app)}.json").write_text(json.dumps(r))


def qualified_build(apps):
    rec = builds.create("admin")
    builds.update(rec["id"], state=builds.QUALIFIED)
    return rec["id"], [{"app": a, "qualified": True, "verdict": "PASS"} for a in apps]


class Base(unittest.TestCase):
    def setUp(self):
        self.c = FakeCoolify()
        h.COOLIFY_URL, h.COOLIFY_TOKEN = self.c.url, "1|test"
        prov._TARGET.clear()
        prov.AUTOCREATE = True
        h.RESOURCES_FILE.parent.mkdir(parents=True, exist_ok=True)
        h.RESOURCES_FILE.write_text('{"apps": {}}')

    def tearDown(self):
        self.c.close()


class ComposeFromRecipe(unittest.TestCase):
    def test_image_app_keeps_the_test_runs_fence_and_gets_its_own_address(self):
        c, web = prov.compose_for("Grafana", {"kind": "image", "image": "grafana/grafana:11", "port": 3000,
                                              "env": {"A": "1"}, "volumes": ["/var/lib/grafana", "/etc:/x"]}, "b1", skin=None)
        s = c["services"]["app"]
        self.assertEqual(web, "app")
        self.assertEqual(s["image"], "grafana/grafana:11")
        self.assertIn("SERVICE_URL_APP_3000", s["environment"])
        self.assertEqual(s["environment"]["A"], "1")
        self.assertEqual(s["volumes"], ["/var/lib/grafana"])          # host binds never go through
        self.assertEqual(s["security_opt"], ["no-new-privileges:true"])
        self.assertEqual(s["cap_drop"], ["ALL"])
        self.assertEqual(s["pids_limit"], app_runner.PIDS_LIMIT)
        self.assertNotIn("pull_policy", s)

    def test_relaxed_caps_recipe_stays_relaxed(self):
        c, _ = prov.compose_for("x", {"kind": "image", "image": "i", "port": 80, "relaxed_caps": True}, "b1", skin=None)
        self.assertNotIn("cap_drop", c["services"]["app"])

    def test_dockerfile_app_runs_the_image_that_qualified_pinned_per_build(self):
        pinned = []
        c, _ = prov.compose_for("My App", {"kind": "dockerfile", "dockerfile": "Dockerfile", "port": 8080}, "b42",
                                pin=lambda img, app, b: pinned.append((img, b)) or f"atta-qualified/my-app:{b}", skin=None)
        s = c["services"]["app"]
        self.assertEqual(pinned, [("atta-local/my-app:latest", "b42")])
        self.assertEqual(s["image"], "atta-qualified/my-app:b42")
        self.assertEqual(s["pull_policy"], "never")

    def test_port_falls_back_to_the_images_exposed_port(self):
        c, web = prov.compose_for("x", {"kind": "image", "image": "i"}, "b", image_ports=lambda i: [5678], skin=None)
        self.assertIn("SERVICE_URL_APP_5678", c["services"]["app"]["environment"])
        c, web = prov.compose_for("x", {"kind": "image", "image": "i"}, "b", image_ports=lambda i: [], skin=None)
        self.assertIsNone(web)

    def test_compose_app_uses_the_hardened_rendered_file_minus_host_wiring(self):
        rendered = {"name": "atta-x", "services": {
            "db": {"image": "postgres:16", "container_name": "x-db", "networks": {"default": None},
                   "security_opt": ["no-new-privileges:true"]},
            "web": {"image": "x/web", "ports": [{"target": 3000, "published": "40001", "host_ip": "127.0.0.1"}],
                    "environment": {"DB": "db"}, "networks": {"default": None}}},
            "volumes": {"data": {"name": "atta-x_data"}}, "networks": {"default": {"name": "atta-x_default"}}}
        c, web = prov.compose_for("x", {"kind": "compose", "file": "docker-compose.yml"}, "b", rendered=rendered, skin=None)
        self.assertEqual(web, "web")
        self.assertNotIn("ports", c["services"]["web"])
        self.assertNotIn("container_name", c["services"]["db"])
        self.assertNotIn("networks", c)
        self.assertEqual(c["services"]["db"]["security_opt"], ["no-new-privileges:true"])  # fence carried over
        self.assertEqual(c["services"]["web"]["environment"], {"DB": "db", "SERVICE_URL_WEB_3000": ""})
        self.assertEqual(c["volumes"], {"data": None})     # Coolify names volumes per service

    def test_compose_app_without_its_rendered_file_says_why(self):
        with self.assertRaises(prov.ProvisionError) as e:
            prov.compose_for("x", {"kind": "compose"}, "b", rendered=None, skin=None)
        self.assertIn("compose.rendered.json", str(e.exception))

    def test_yaml_writer_round_trips(self):
        doc = {"services": {"a-b": {"image": "x:1", "environment": {"K": "v: with # stuff", "E": ""},
                                    "command": ["sh", "-c", "echo 'hi'"], "pids_limit": 1024, "init": True,
                                    "volumes": ["/data"], "cap_add": []}}, "volumes": {"v": None}}
        self.assertEqual(yaml.safe_load(prov.to_yaml(doc)), doc)


class SkinAndAdapterGoLive(unittest.TestCase):
    """What runs live is what was checked: the app BEHIND its skin/capability proxy."""

    def test_image_app_is_published_only_through_its_skin_proxy(self):
        overlay("Grafana")
        c, web = prov.compose_for("Grafana", {"kind": "image", "image": "g", "port": 3000}, "b7")
        app, skin = c["services"]["app"], c["services"]["atta-skin"]
        self.assertEqual(web, "atta-skin")
        self.assertFalse([k for k in (app.get("environment") or {}) if k.startswith("SERVICE_URL_")])
        self.assertIn("SERVICE_URL_ATTA_SKIN_8100", skin["environment"])
        cmd = skin["command"]
        self.assertEqual(cmd[cmd.index("--target") + 1], "http://app:3000")     # the app inside its network
        self.assertEqual(cmd[cmd.index("--host") + 1], "0.0.0.0")
        self.assertEqual(skin["depends_on"], ["app"])
        snap = cmd[1].rsplit("/ui-bridge/", 1)[0]
        self.assertEqual(skin["volumes"], [f"{snap}:{snap}:ro"])                # same path on host and inside
        self.assertTrue(Path(snap, "ui-bridge", "proxy.js").is_file())
        self.assertIn("/b7/", snap)                                            # frozen per qualified build

    def test_live_skin_files_are_a_frozen_copy(self):
        overlay("Memos")
        c, _ = prov.compose_for("Memos", {"kind": "image", "image": "m", "port": 5230}, "b1")
        snap = Path(c["services"]["atta-skin"]["command"][1])
        (app_runner.LIB / "Memos" / ".ui-capability" / "ui-bridge" / "proxy.js").write_text("// repaired later")
        self.assertEqual(snap.read_text(), "// skin proxy")

    def test_compose_app_skin_points_at_its_web_service(self):
        overlay("Plane")
        rendered = {"services": {"web": {"image": "w", "ports": [{"target": 3000}]}, "api": {"image": "a"}}}
        c, web = prov.compose_for("Plane", {"kind": "compose"}, "b1", rendered=rendered)
        cmd = c["services"]["atta-skin"]["command"]
        self.assertEqual(cmd[cmd.index("--target") + 1], "http://web:3000")
        self.assertNotIn("environment", c["services"]["web"])

    def test_no_overlay_means_no_live_copy(self):
        with self.assertRaises(prov.ProvisionError) as e:
            prov.compose_for("Nope", {"kind": "image", "image": "n", "port": 80}, "b1")
        self.assertIn("overlay", str(e.exception))

    def test_gateway_settings_follow_the_skin_live(self):
        overlay("Grafana")
        os.environ["APP_BUILDER_GATEWAY_SECRET"] = "s3cret"
        try:
            c, _ = prov.compose_for("Grafana", {"kind": "image", "image": "g", "port": 3000}, "b8")
        finally:
            del os.environ["APP_BUILDER_GATEWAY_SECRET"]
        self.assertEqual(c["services"]["atta-skin"]["environment"]["APP_BUILDER_GATEWAY_SECRET"], "s3cret")

    def test_skin_runs_in_atta_image_when_under_coolify(self):
        self.assertEqual(yaml.safe_load((HERE.parent / "docker-compose.coolify.yml").read_text())
                         ["services"]["atta"]["environment"]["ATTA_IMAGE"], "atta:v121")


class Provisioning(Base):
    def test_a_new_qualified_app_gets_its_own_service_and_is_started(self):
        recipe("Grafana", kind="image", image="grafana/grafana:11", port=3000)
        bid, apps = qualified_build(["Grafana"])
        c = h.hand_off(bid, apps)
        self.assertEqual(c["status"], h.DISPATCHED)
        self.assertEqual(c["apps"]["Grafana"]["status"], "ACCEPTED")
        self.assertEqual(c["apps"]["Grafana"]["url"], "http://atta-grafana.1.2.3.4.sslip.io")
        self.assertEqual([s["name"] for s in self.c.services], ["atta-grafana"])
        create = [x for x in self.c.calls if x[:2] == ("POST", "/api/v1/services")][0][2]
        self.assertEqual(create["server_uuid"], "srv-local")        # localhost preferred
        self.assertEqual(create["project_uuid"], "prj-1")
        self.assertTrue(create["instant_deploy"])
        self.assertEqual(self.c.projects[0]["name"], prov.PROJECT_NAME)
        res = json.loads(h.RESOURCES_FILE.read_text())
        self.assertEqual(res["apps"]["Grafana"], "svc-1")
        self.assertEqual(res["managed_by_atta"]["Grafana"]["build_id"], bid)
        self.assertEqual(oct(h.RESOURCES_FILE.stat().st_mode & 0o777), "0o600")

    def test_each_app_gets_a_separate_service(self):
        recipe("One", kind="image", image="a", port=80)
        recipe("Two", kind="image", image="b", port=80)
        bid, apps = qualified_build(["One", "Two"])
        h.hand_off(bid, apps)
        self.assertEqual(sorted(s["name"] for s in self.c.services), ["atta-one", "atta-two"])
        self.assertEqual(len(self.c.projects), 1)                    # one project, found then reused

    def test_requalified_app_updates_its_own_service_instead_of_making_another(self):
        recipe("Grafana", kind="image", image="grafana/grafana:11", port=3000)
        bid, apps = qualified_build(["Grafana"]); h.hand_off(bid, apps)
        recipe("Grafana", kind="image", image="grafana/grafana:12", port=3000)
        bid2, apps2 = qualified_build(["Grafana"]); c = h.hand_off(bid2, apps2)
        self.assertEqual(len(self.c.services), 1)
        self.assertEqual(self.c.compose()["services"]["app"]["image"], "grafana/grafana:12")
        self.assertEqual(c["apps"]["Grafana"]["status"], "ACCEPTED")    # "Service ... started" counts
        self.assertTrue(any(x[0] == "POST" and x[1].startswith("/api/v1/deploy?uuid=svc-1") for x in self.c.calls))

    def test_a_service_lost_from_the_record_is_found_again_by_name(self):
        recipe("Grafana", kind="image", image="g", port=3000)
        self.c.services.append({"uuid": "svc-old", "name": "atta-grafana", "compose": ""})
        bid, apps = qualified_build(["Grafana"]); h.hand_off(bid, apps)
        self.assertEqual(len(self.c.services), 1)
        self.assertEqual(json.loads(h.RESOURCES_FILE.read_text())["apps"]["Grafana"], "svc-old")

    def test_hand_made_resources_are_never_touched(self):
        recipe("Grafana", kind="image", image="g", port=3000)
        h.RESOURCES_FILE.write_text(json.dumps({"apps": {"Grafana": "svc-mine"}}))
        bid, apps = qualified_build(["Grafana"]); c = h.hand_off(bid, apps)
        self.assertFalse([x for x in self.c.calls if x[0] == "PATCH" or x[:2] == ("POST", "/api/v1/services")])
        self.assertEqual(c["apps"]["Grafana"]["uuid"], "svc-mine")

    def test_no_recipe_is_reported_plainly_not_crashed(self):
        (app_runner.RECIPES / "ghost.json").unlink(missing_ok=True)
        bid, apps = qualified_build(["ghost"]); c = h.hand_off(bid, apps)
        self.assertEqual(c["apps"]["ghost"]["status"], "UNPROVISIONABLE")
        self.assertEqual(c["status"], h.UNPROVISIONABLE)
        self.assertIn("recipe", c["apps"]["ghost"]["detail"])

    def test_coolify_failing_is_retried_later(self):
        recipe("Grafana", kind="image", image="g", port=3000)
        self.c.fail_create = True
        bid, apps = qualified_build(["Grafana"]); c = h.hand_off(bid, apps)
        self.assertEqual(c["status"], h.RETRYING)
        self.assertGreater(c["next_attempt_at"], 0)
        self.c.fail_create = False
        c = h.dispatch(bid)
        self.assertEqual(c["status"], h.DISPATCHED)

    def test_autocreate_off_is_the_old_behaviour(self):
        prov.AUTOCREATE = False
        recipe("Grafana", kind="image", image="g", port=3000)
        bid, apps = qualified_build(["Grafana"]); c = h.hand_off(bid, apps)
        self.assertEqual(c["status"], h.UNMAPPED)
        self.assertEqual(self.c.services, [])


class DeployAnswer(Base):
    def test_service_started_message_counts_as_accepted(self):
        ok, detail = h._deploy("svc-9")
        self.assertTrue(ok, detail)
        self.assertIn("started", detail)


class LibraryRun(unittest.TestCase):
    def test_library_run_is_picked_up_from_the_inbox(self):
        pipeline.INBOX.mkdir(parents=True, exist_ok=True)
        f = pipeline.INBOX / "lib20260926.library.json"; f.write_text("{}")
        done = pipeline.INBOX / "lib20260925.processed.json"; done.write_text("{}")
        try:
            items = [p.name for p in pipeline._inbox_items()]
            self.assertIn(f.name, items)
            self.assertNotIn(done.name, items)
        finally:
            f.unlink(); done.unlink()

    def test_process_knows_the_library_kind(self):
        import inspect
        self.assertIn(".library.json", inspect.getsource(pipeline.process))


class ContainerStart(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(dir=TMP))

    def test_seed_makes_a_private_data_folder_with_a_real_env(self):
        container_main.seed(self.root, DEP)
        env = (self.root / ".env").read_text()
        self.assertIn("APP_BUILDER_HOST=0.0.0.0", env)
        self.assertIn("COOLIFY_AUTOCREATE=true", env)
        self.assertRegex(env, r"APP_BUILDER_SESSION_SECRET=\S{40,}")
        self.assertEqual(oct((self.root / ".env").stat().st_mode & 0o777), "0o600")
        self.assertEqual(oct(self.root.stat().st_mode & 0o777), "0o700")
        self.assertTrue((self.root / "upstream_apps.json").is_file())
        before = (self.root / ".env").read_text()
        container_main.seed(self.root, DEP)                            # restart: nothing regenerated
        self.assertEqual((self.root / ".env").read_text(), before)

    def test_first_start_queues_one_run_over_the_whole_list(self):
        container_main.seed(self.root, DEP)
        self.assertTrue(container_main.queue_first_library_run(self.root))
        self.assertFalse(container_main.queue_first_library_run(self.root))
        self.assertEqual(len(list((self.root / "inbox").glob("*.library.json"))), 1)

    def test_coolify_connection_file_wins_over_env(self):
        container_main.seed(self.root, DEP)
        (self.root / ".coolify-connection").write_text('# from deploy-atta.sh\nCOOLIFY_URL="http://127.0.0.1:8000"\n'
                                                       'COOLIFY_TOKEN="3|abcDEF"\n')
        saved = {k: os.environ.pop(k) for k in ("COOLIFY_URL", "COOLIFY_TOKEN") if k in os.environ}
        try:
            v = container_main.load_env(self.root)
        finally:
            os.environ.update(saved)
        self.assertEqual(v["COOLIFY_URL"], "http://127.0.0.1:8000")
        self.assertEqual(v["COOLIFY_TOKEN"], "3|abcDEF")


class Packaging(unittest.TestCase):
    ATTA = HERE.parent

    def test_coolify_compose_is_valid_and_wired_for_the_host(self):
        d = yaml.safe_load((self.ATTA / "docker-compose.coolify.yml").read_text())
        s = d["services"]["atta"]
        self.assertEqual(s["network_mode"], "host")
        self.assertIn("/var/run/docker.sock:/var/run/docker.sock", s["volumes"])
        self.assertIn("/srv/app-builder:/srv/app-builder", s["volumes"])   # same path in and out

    def test_dockerfile_pins_compose_to_the_installer_checksums(self):
        df = (self.ATTA / "Dockerfile").read_text()
        lib = (DEP / "bootstrap-lib.sh").read_text()
        for key in ("ATTA_COMPOSE_SHA256_X86_64", "ATTA_COMPOSE_SHA256_AARCH64"):
            want = lib.split(key + '="')[1].split('"')[0]
            self.assertIn(f"{key}={want}", df)
        self.assertIn("container_main.py", df)

    def test_release_is_v117(self):
        # v118 carries v117 forward: the version moved on, the v117 feature must still be listed.
        rel = json.loads((self.ATTA / "release.json").read_text())
        self.assertEqual(rel["version"], "v121.2")
        self.assertTrue(any(f.startswith("v117_runs_under_coolify") for f in rel["features"]))

    def test_single_app_list(self):
        kit = self.ATTA / "05-coolify" / "kit"
        self.assertFalse((kit / "config" / "app-list.json").exists())
        self.assertFalse((kit / "scripts" / "deploy-app-list.sh").exists())


if __name__ == "__main__":
    unittest.main()
