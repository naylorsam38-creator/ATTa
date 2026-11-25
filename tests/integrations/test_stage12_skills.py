"""Stage 12 — skills (structured, versioned, references resolved through the catalogue), reusable scripts run through
the controller, skill version pinning, plugin packaging/contributions."""
from __future__ import annotations

import shutil

import pytest
import yaml

from stage12_harness import build12, canonical_hash, record, write_notes

from backend.integrations.manifest import IntegrationManifestError, load_integration_manifest, write_integrity
from backend.integrations.skills import SkillError

pytestmark = pytest.mark.requires_services
CONN, SKILL = "test.notes_connector", "test.find_notes_skill"


def _skill_ready(tmp_path, extra=()):
    H = build12(tmp_path, fixtures=["valid_connector", "valid_skill", *extra])
    H.install("valid_connector", enable=True)
    res = H.install("valid_skill")
    assert res["status"] == "INSTALLED", res
    H.enable(SKILL)
    return H, res


def test_S12_T50_skill_loading_and_tool_reference_resolution(tmp_path):
    H, res = _skill_ready(tmp_path)
    search, read = H.number(CONN, "search"), H.number(CONN, "read")
    assert res["skill"]["tool_references"] == {"test.notes_connector:search": search,
                                               "test.notes_connector:read": read}
    assert H.mgr.get_installed(SKILL).capability_id is None, "a skill registers no executable capability"
    sc = H.svc.skill_content("find_notes")
    assert sc.version == "1.0.0" and "Find notes" in sc.instructions and "templates/report.md" in sc.templates
    assert H.svc.health(SKILL)["status"] == "HEALTHY"
    scripts = H.svc.list_scripts()
    assert scripts == [{"skill_id": "find_notes", "skill_version": "1.0.0", "workflow_id": "find_notes",
                        "workflow_version": "1.0.0", "kind": "script",
                        "description": "Search the notes file for a query.",
                        "parameters": sc.workflows[0]["parameters"], "tool_ids": [search]}]
    H.mgr.disable(CONN, "admin")
    assert H.svc.health(SKILL)["status"] == "DEGRADED", "a skill reports unavailable referenced tools"
    record("S12-T50", {"skill": res["skill"], "scripts": scripts})


def test_S12_T51_invalid_skill_references_are_rejected(tmp_path):
    H = build12(tmp_path, fixtures=["valid_connector", "invalid_skill", "valid_skill"])
    H.install("valid_connector", enable=True)
    bad = H.install("invalid_skill")
    assert bad["status"] == "FAILED" and any("test.missing_integration:tool" in p for p in bad["problems"])
    d = tmp_path / "s2"
    shutil.copytree(H.src / "valid_skill", d)
    m = yaml.safe_load((d / "integration.yaml").read_text())
    m["skill"]["workflows"][0]["steps"][0]["tool"] = "test.notes_connector:create"
    (d / "integration.yaml").write_text(yaml.safe_dump(m))
    with pytest.raises(IntegrationManifestError) as e:
        load_integration_manifest(d / "integration.yaml")
    assert any("not in skill.tool_references" in p for p in e.value.problems)
    m["skill"]["workflows"][0]["steps"][0]["tool"] = "test.notes_connector:search"
    m["tools"] = [{"name": "x"}]
    (d / "integration.yaml").write_text(yaml.safe_dump(m))
    with pytest.raises(IntegrationManifestError):
        load_integration_manifest(d / "integration.yaml")
    record("S12-T51", {"unresolved": bad["problems"], "step_not_referenced": e.value.problems})


def test_S12_T52_reusable_script_runs_only_through_the_controller(tmp_path):
    H, _ = _skill_ready(tmp_path)
    search = H.number(CONN, "search")
    o = H.objective()
    spec = H.svc.script_task_spec("find_notes", "find_notes", task_id="TASK-0001", requirement_ids=["FR-01"],
                                  task_type="connector.notes", permitted_filesystem_paths=["notes.json"],
                                  acceptance_criteria=[{"id": "TC-01", "description": "search ran",
                                                        "requirement_ids": ["FR-01"], "verifier": "tool_verify",
                                                        "params": {"tool_id": search}}])
    H.h.controller.plan(o.objective_id, [spec])
    c = H.h.store.contract(o.objective_id, "TASK-0001")
    assert c.permitted_tool_ids == [search], "the skill referenced 2 tools; the allocator gave the task only 1"
    assert c.input_checksums == {"skill:find_notes@1.0.0": H.svc.skill_content("find_notes").content_digest}
    write_notes(H.workspace(o.objective_id))
    with pytest.raises(SkillError):
        H.svc.script_source(c, "find_notes", "find_notes", {"query": ""})
    with pytest.raises(SkillError):
        H.svc.script_source(c, "find_notes", "find_notes", {"query": "x", "extra": 1})
    src = H.svc.script_source(c, "find_notes", "find_notes", {"query": "holiday"})
    assert src.identity().provider == "atta-script"
    H.h.controller.release(o.objective_id)
    state = H.h.controller.run_task(o.objective_id, "TASK-0001", src)
    assert state == "COMPLETED", H.h.store.task(o.objective_id, "TASK-0001")
    ex = H.h.store.executions(o.objective_id, "TASK-0001")
    assert [(e["tool_number"], e["state"]) for e in ex] == [(search, "succeeded")]
    assert ex[0]["result"]["count"] == 1
    with H.h.store.db.tx() as x:
        props = x.execute("SELECT source, accepted FROM control.proposals WHERE objective_id=%s AND task_id=%s",
                          (o.objective_id, "TASK-0001")).fetchall()
    assert props and {p["source"] for p in props} == {"atta-script"} and all(p["accepted"] for p in props)
    record("S12-T52", {"pool": c.permitted_tool_ids, "pin": c.input_checksums, "state": state,
                       "proposal_sources": sorted({p.get("source") for p in props})})


def test_S12_T53_skill_upgrade_never_changes_an_active_tasks_pinned_version(tmp_path):
    H, _ = _skill_ready(tmp_path)
    v1 = H.svc.skill_content("find_notes")
    o = H.objective()
    spec = H.svc.script_task_spec("find_notes", "find_notes", task_id="TASK-0001", requirement_ids=["FR-01"],
                                  task_type="connector.notes", permitted_filesystem_paths=["notes.json"],
                                  acceptance_criteria=[{"id": "TC-01", "description": "search ran",
                                                        "requirement_ids": ["FR-01"],
                                                        "verifier": "execution_succeeded", "params": {}}])
    H.h.controller.plan(o.objective_id, [spec])
    c = H.h.store.contract(o.objective_id, "TASK-0001")
    d = H.src / "valid_skill_v2"
    shutil.copytree(H.src / "valid_skill", d)
    m = yaml.safe_load((d / "integration.yaml").read_text())
    m["version"] = "1.1.0"
    m["skill"]["workflows"][0]["description"] = "Search notes (improved)."
    (d / "integration.yaml").write_text(yaml.safe_dump(m))
    (d / "SKILL.md").write_text("# Find notes v1.1\n")
    write_integrity(d)
    up = H.upgrade("valid_skill_v2")
    assert up["status"] == "INSTALLED" and up["skill"]["version"] == "1.1.0"
    v2 = H.svc.skill_content("find_notes")
    assert v2.version == "1.1.0" and v2.content_digest != v1.content_digest
    src = H.svc.script_source(c, "find_notes", "find_notes", {"query": "budget"})
    assert src.skill.version == "1.0.0" and src.skill.content_digest == v1.content_digest
    assert [v["version"] for v in H.store.skill_versions("find_notes")] == ["1.0.0", "1.1.0"]
    spec2 = H.svc.script_task_spec("find_notes", "find_notes", task_id="TASK-0002", requirement_ids=["FR-01"],
                                   task_type="connector.notes", acceptance_criteria=spec.acceptance_criteria)
    assert spec2.input_checksums == {"skill:find_notes@1.1.0": v2.content_digest}
    record("S12-T53", {"pinned": c.input_checksums, "current": v2.version})


def test_S12_T54_plugin_packaging_contributions_and_inert_ui_metadata(tmp_path):
    H = build12(tmp_path, fixtures=["valid_plugin"])
    res = H.install("valid_plugin", enable=True)
    p = H.store.plugin("test.text_plugin")
    assert p["state"] == "ENABLED" and p["isolation_required"] is False
    versions = H.svc.plugins()
    with H.h.store.db.tx() as x:
        pv = x.execute("SELECT contributes, package_digest FROM integrations.plugin_versions WHERE namespace=%s",
                       (H.h.namespace,)).fetchone()
    contrib = pv["contributes"]
    assert contrib["ui_extensions"][0]["slot"] == "integration.panel"
    assert contrib["tools"] == ["summarize", "poison", "large", "bad_output"]
    assert pv["package_digest"] == H.mgr.get_installed("test.text_plugin").package_digest
    gen = H.mgr.packages_dir / "integration.test.text_plugin"
    names = sorted(x.name for x in gen.iterdir() if x.is_file())
    assert "capability.yaml" in names and "_atta_verify.py" in names and "plugin.py" in names
    cap = yaml.safe_load((gen / "capability.yaml").read_text())
    assert all(t["verify"].startswith("_atta_tools_verify:") for t in cap["tools"]), \
        "plugins never verify themselves"
    record("S12-T54", {"plugin": versions, "generated_files": names, "contributes": contrib})


def _unused():
    return canonical_hash
