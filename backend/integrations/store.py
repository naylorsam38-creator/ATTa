"""Persistence for Stage 12 (schema `integrations`, migration 0012). Same conventions as the Stage 01A ControlStore:
one transaction per call through backend.database.session.Database, runtime role only, append-only history."""
from __future__ import annotations

import json
from typing import Any

from psycopg.types.json import Jsonb

from backend.contracts.identifiers import _ulid
from backend.database.session import Database

from .content import redact


def _j(v: Any) -> Jsonb:
    return Jsonb(json.loads(json.dumps(v, default=str)))


class IntegrationStore:
    def __init__(self, db: Database, namespace: str = "default"):
        self.db, self.ns = db, namespace

    # ------------------------------------------------------------------ integrations (current state)
    def upsert_integration(self, row: dict) -> None:
        cols = ["integration_id", "integration_type", "name", "provider", "current_version", "state", "trust_class",
                "capability_id", "approved_manifest_digest", "approved_package_digest", "approval_binding",
                "disabled_by_admin", "problems", "installed_at"]
        defaults = {"disabled_by_admin": False, "trust_class": "UNTRUSTED"}
        vals = [_j(row.get("problems") or []) if c == "problems" else row.get(c, defaults.get(c)) for c in cols]
        with self.db.tx() as x:
            x.execute(f"INSERT INTO integrations.integrations (namespace, {', '.join(cols)}, updated_at) "
                      f"VALUES (%s, {', '.join(['%s'] * len(cols))}, now()) "
                      f"ON CONFLICT (namespace, integration_id) DO UPDATE SET "
                      + ", ".join(f"{c} = EXCLUDED.{c}" for c in cols[1:]) + ", updated_at = now()",
                      [self.ns, *vals])

    def integration(self, integration_id: str) -> dict | None:
        with self.db.tx() as x:
            return x.execute("SELECT * FROM integrations.integrations WHERE namespace=%s AND integration_id=%s",
                             (self.ns, integration_id)).fetchone()

    def integrations(self) -> list[dict]:
        with self.db.tx() as x:
            return x.execute("SELECT * FROM integrations.integrations WHERE namespace=%s ORDER BY integration_id",
                             (self.ns,)).fetchall()

    def set_state(self, integration_id: str, to_state: str, reason: str, actor: str, *, version: str | None = None,
                  from_state: str | None = None, problems: list[str] | None = None, **fields) -> None:
        sets, vals = ["state = %s", "updated_at = now()"], [to_state]
        if problems is not None:
            sets.append("problems = %s")
            vals.append(_j(problems))
        for k, v in fields.items():
            sets.append(f"{k} = %s")
            vals.append(v)
        with self.db.tx() as x:
            x.execute(f"UPDATE integrations.integrations SET {', '.join(sets)} WHERE namespace=%s AND integration_id=%s",
                      [*vals, self.ns, integration_id])
            x.execute("INSERT INTO integrations.installation_events (namespace, integration_id, version, from_state, "
                      "to_state, reason, actor) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                      (self.ns, integration_id, version, from_state, to_state, reason[:2000], actor))

    def installation_events(self, integration_id: str) -> list[dict]:
        with self.db.tx() as x:
            return x.execute("SELECT * FROM integrations.installation_events WHERE namespace=%s AND integration_id=%s "
                             "ORDER BY id", (self.ns, integration_id)).fetchall()

    # ------------------------------------------------------------------ manifests and versions (append-only)
    def add_manifest(self, m, source: str) -> None:
        with self.db.tx() as x:
            x.execute("INSERT INTO integrations.integration_manifests (namespace, manifest_digest, integration_id, version, "
                      "manifest, source, package_digest) VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                      (self.ns, m.manifest_digest, m.integration_id, m.version, _j(m.raw), source, m.package_digest))

    def manifest(self, manifest_digest: str) -> dict | None:
        with self.db.tx() as x:
            r = x.execute("SELECT * FROM integrations.integration_manifests WHERE namespace=%s AND manifest_digest=%s",
                          (self.ns, manifest_digest)).fetchone()
        return r

    def add_version(self, integration_id: str, version: str, manifest_digest: str, package_digest: str | None,
                    trust_class: str, binding: str, approved_by: str, upgrade_report: dict | None = None) -> None:
        with self.db.tx() as x:
            x.execute("INSERT INTO integrations.integration_versions (namespace, integration_id, version, manifest_digest, "
                      "package_digest, trust_class, approval_binding, approved_by, upgrade_report) "
                      "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                      (self.ns, integration_id, version, manifest_digest, package_digest, trust_class, binding,
                       approved_by, _j(upgrade_report or {})))

    def versions(self, integration_id: str) -> list[dict]:
        with self.db.tx() as x:
            return x.execute("SELECT * FROM integrations.integration_versions WHERE namespace=%s AND integration_id=%s "
                             "ORDER BY approved_at", (self.ns, integration_id)).fetchall()

    def record_registration(self, m, capability_id: str, numbers: dict[str, str], schema_hashes: dict[str, str],
                            credential_refs: dict[str, str]) -> None:
        """Capabilities, tools, permissions and credential references of one approved version, in one transaction."""
        iid, ver = m.integration_id, m.version
        with self.db.tx() as x:
            for c in m.raw.get("capabilities") or []:
                x.execute("INSERT INTO integrations.integration_capabilities VALUES (%s,%s,%s,%s,%s,%s) "
                          "ON CONFLICT DO NOTHING", (self.ns, iid, ver, c["id"], capability_id, c["description"]))
            for t in m.tools:
                x.execute("INSERT INTO integrations.integration_tools (namespace, integration_id, version, tool_name, "
                          "tool_string, tool_number, side_effect, risk, schema_hash, permissions, status) "
                          "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'registered') "
                          "ON CONFLICT (namespace, integration_id, version, tool_name) DO UPDATE SET status='registered'",
                          (self.ns, iid, ver, t["name"], f"{capability_id}:{t['name']}", numbers[t["name"]],
                           t["side_effect"], t["risk"], schema_hashes[t["name"]], _j(sorted(t["permissions"]))))
            for perm in sorted(m.permissions):
                for source in ("requested", "approved"):
                    x.execute("INSERT INTO integrations.integration_permissions VALUES (%s,%s,%s,%s,%s) "
                              "ON CONFLICT DO NOTHING", (self.ns, iid, ver, perm, source))
            for c in m.raw.get("credential_requirements") or []:
                x.execute("INSERT INTO integrations.integration_credentials_refs VALUES (%s,%s,%s,%s,%s,%s,%s) "
                          "ON CONFLICT DO NOTHING", (self.ns, iid, ver, c["name"], c["kind"],
                                                     credential_refs[c["name"]], c["required"]))

    def set_tool_status(self, integration_id: str, version: str | None, status: str, *, only: str | None = None) -> None:
        q = "UPDATE integrations.integration_tools SET status=%s WHERE namespace=%s AND integration_id=%s"
        args: list = [status, self.ns, integration_id]
        if version is not None:
            q += " AND version=%s"
            args.append(version)
        if only is not None:
            q += " AND status=%s"
            args.append(only)
        with self.db.tx() as x:
            x.execute(q, args)

    def tools(self, integration_id: str | None = None) -> list[dict]:
        with self.db.tx() as x:
            if integration_id:
                return x.execute("SELECT * FROM integrations.integration_tools WHERE namespace=%s AND integration_id=%s "
                                 "ORDER BY version, tool_name", (self.ns, integration_id)).fetchall()
            return x.execute("SELECT * FROM integrations.integration_tools WHERE namespace=%s ORDER BY integration_id",
                             (self.ns,)).fetchall()

    def capabilities(self, integration_id: str) -> list[dict]:
        with self.db.tx() as x:
            return x.execute("SELECT * FROM integrations.integration_capabilities WHERE namespace=%s AND integration_id=%s",
                             (self.ns, integration_id)).fetchall()

    def permissions(self, integration_id: str) -> list[dict]:
        with self.db.tx() as x:
            return x.execute("SELECT * FROM integrations.integration_permissions WHERE namespace=%s AND integration_id=%s",
                             (self.ns, integration_id)).fetchall()

    def credential_refs(self, integration_id: str) -> list[dict]:
        with self.db.tx() as x:
            return x.execute("SELECT * FROM integrations.integration_credentials_refs WHERE namespace=%s AND "
                             "integration_id=%s", (self.ns, integration_id)).fetchall()

    # ------------------------------------------------------------------ numbering
    def numbers(self) -> list[dict]:
        with self.db.tx() as x:
            return x.execute("SELECT number, tool_string, integration_id FROM integrations.tool_numbers WHERE namespace=%s",
                             (self.ns,)).fetchall()

    def assign_numbers(self, integration_id: str, wanted: list[tuple[str, str]], taken_elsewhere: set[str],
                       band: tuple[int, int]) -> dict[str, str]:
        """wanted: [(tool_string, capability_number)]. Existing assignments are reused (stable identity); new ones take
        the lowest free TTT in the band for their CCC. Serialised with an advisory lock."""
        out: dict[str, str] = {}
        with self.db.tx() as x:
            x.execute("SELECT pg_advisory_xact_lock(7212001)")
            rows = x.execute("SELECT number, tool_string FROM integrations.tool_numbers WHERE namespace=%s",
                             (self.ns,)).fetchall()
            ctl = x.execute("SELECT number, tool_string FROM control.tool_identifiers WHERE namespace=%s",
                            (self.ns,)).fetchall()
            by_tool = {r["tool_string"]: r["number"] for r in rows}
            ctl_by_tool = {r["tool_string"]: r["number"] for r in ctl}
            used = {r["number"] for r in rows} | {r["number"] for r in ctl} | set(taken_elsewhere)
            for tool_string, ccc in wanted:
                if tool_string in by_tool:
                    out[tool_string] = by_tool[tool_string]
                    continue
                if tool_string in ctl_by_tool:        # already permanently numbered by the operator file
                    out[tool_string] = ctl_by_tool[tool_string]
                    continue
                for ttt in range(band[0], band[1] + 1):
                    n = f"{ccc}.{ttt:03d}"
                    if n not in used:
                        break
                else:
                    raise ValueError(f"no free tool number for {tool_string} in {ccc}.{band[0]}-{band[1]}")
                x.execute("INSERT INTO integrations.tool_numbers (namespace, number, tool_string, integration_id) "
                          "VALUES (%s,%s,%s,%s)", (self.ns, n, tool_string, integration_id))
                used.add(n)
                out[tool_string] = n
        return out

    # ------------------------------------------------------------------ events, health, MCP
    def event(self, integration_id: str, event_type: str, actor: str, details: dict | None = None, *,
              version: str | None = None, correlation_id: str | None = None) -> str:
        eid = f"ige_{_ulid()}"
        with self.db.tx() as x:
            x.execute("INSERT INTO integrations.integration_events (event_id, namespace, integration_id, version, "
                      "event_type, actor, details, correlation_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                      (eid, self.ns, integration_id, version, event_type, actor, _j(redact(details or {})),
                       correlation_id))
        return eid

    def events(self, integration_id: str | None = None, event_type: str | None = None) -> list[dict]:
        q, a = "SELECT * FROM integrations.integration_events WHERE namespace=%s", [self.ns]
        if integration_id:
            q += " AND integration_id=%s"
            a.append(integration_id)
        if event_type:
            q += " AND event_type=%s"
            a.append(event_type)
        with self.db.tx() as x:
            return x.execute(q + " ORDER BY at, event_id", a).fetchall()

    def add_health(self, integration_id: str, status: str, detail: dict) -> None:
        with self.db.tx() as x:
            x.execute("INSERT INTO integrations.integration_health (namespace, integration_id, status, detail) "
                      "VALUES (%s,%s,%s,%s)", (self.ns, integration_id, status, _j(redact(detail))))

    def health_history(self, integration_id: str, limit: int = 20) -> list[dict]:
        with self.db.tx() as x:
            return x.execute("SELECT * FROM integrations.integration_health WHERE namespace=%s AND integration_id=%s "
                             "ORDER BY checked_at DESC, id DESC LIMIT %s", (self.ns, integration_id, limit)).fetchall()

    def upsert_mcp_server(self, integration_id: str, transport: str, endpoint: dict, lifecycle: str,
                          protocol_version: str | None = None, server_info: dict | None = None,
                          last_error: str | None = None) -> None:
        with self.db.tx() as x:
            x.execute("INSERT INTO integrations.mcp_servers (namespace, integration_id, transport, endpoint, lifecycle, "
                      "protocol_version, server_info, last_error) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                      "ON CONFLICT (namespace, integration_id) DO UPDATE SET transport=EXCLUDED.transport, "
                      "endpoint=EXCLUDED.endpoint, lifecycle=EXCLUDED.lifecycle, "
                      "protocol_version=COALESCE(EXCLUDED.protocol_version, integrations.mcp_servers.protocol_version), "
                      "server_info=CASE WHEN EXCLUDED.server_info = '{}'::jsonb THEN integrations.mcp_servers.server_info "
                      "ELSE EXCLUDED.server_info END, last_error=EXCLUDED.last_error, updated_at=now()",
                      (self.ns, integration_id, transport, _j(endpoint), lifecycle, protocol_version,
                       _j(redact(server_info or {})), last_error))

    def mcp_server(self, integration_id: str) -> dict | None:
        with self.db.tx() as x:
            return x.execute("SELECT * FROM integrations.mcp_servers WHERE namespace=%s AND integration_id=%s",
                             (self.ns, integration_id)).fetchone()

    def mcp_servers(self) -> list[dict]:
        with self.db.tx() as x:
            return x.execute("SELECT * FROM integrations.mcp_servers WHERE namespace=%s ORDER BY integration_id",
                             (self.ns,)).fetchall()

    def add_mcp_capability(self, integration_id: str, version: str, kind: str, name: str, schema_hash: str,
                           metadata: dict, accepted: bool) -> None:
        with self.db.tx() as x:
            x.execute("INSERT INTO integrations.mcp_capabilities (namespace, integration_id, version, kind, name, "
                      "schema_hash, metadata, accepted) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                      (self.ns, integration_id, version, kind, name, schema_hash, _j(redact(metadata)), accepted))

    def mcp_capabilities(self, integration_id: str) -> list[dict]:
        with self.db.tx() as x:
            return x.execute("SELECT * FROM integrations.mcp_capabilities WHERE namespace=%s AND integration_id=%s "
                             "ORDER BY kind, name", (self.ns, integration_id)).fetchall()

    # ------------------------------------------------------------------ skills and plugins
    def put_skill_version(self, skill_id: str, integration_id: str, version: str, digest: str, content: dict) -> None:
        with self.db.tx() as x:
            existing = x.execute("SELECT content_digest FROM integrations.skill_versions WHERE namespace=%s AND "
                                 "skill_id=%s AND version=%s", (self.ns, skill_id, version)).fetchone()
            if existing and existing["content_digest"] != digest:
                raise ValueError(f"skill {skill_id} version {version} already exists with different content; "
                                 "a changed skill needs a new version")
            if not existing:
                x.execute("INSERT INTO integrations.skill_versions (namespace, skill_id, version, content_digest, content) "
                          "VALUES (%s,%s,%s,%s,%s)", (self.ns, skill_id, version, digest, _j(content)))

    def set_skill(self, skill_id: str, integration_id: str, version: str | None, state: str) -> None:
        with self.db.tx() as x:
            x.execute("INSERT INTO integrations.skills (namespace, skill_id, integration_id, current_version, state) "
                      "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (namespace, skill_id) DO UPDATE SET "
                      "current_version=EXCLUDED.current_version, state=EXCLUDED.state, updated_at=now()",
                      (self.ns, skill_id, integration_id, version, state))

    def skill(self, skill_id: str) -> dict | None:
        with self.db.tx() as x:
            return x.execute("SELECT * FROM integrations.skills WHERE namespace=%s AND skill_id=%s",
                             (self.ns, skill_id)).fetchone()

    def skills(self) -> list[dict]:
        with self.db.tx() as x:
            return x.execute("SELECT * FROM integrations.skills WHERE namespace=%s ORDER BY skill_id", (self.ns,)).fetchall()

    def skill_version(self, skill_id: str, version: str) -> dict | None:
        with self.db.tx() as x:
            return x.execute("SELECT * FROM integrations.skill_versions WHERE namespace=%s AND skill_id=%s AND version=%s",
                             (self.ns, skill_id, version)).fetchone()

    def skill_versions(self, skill_id: str) -> list[dict]:
        with self.db.tx() as x:
            return x.execute("SELECT skill_id, version, content_digest, created_at FROM integrations.skill_versions "
                             "WHERE namespace=%s AND skill_id=%s ORDER BY created_at", (self.ns, skill_id)).fetchall()

    def put_plugin_version(self, plugin_id: str, version: str, package_digest: str, contributes: dict,
                           isolation_required: bool, state: str) -> None:
        with self.db.tx() as x:
            x.execute("INSERT INTO integrations.plugin_versions (namespace, plugin_id, version, package_digest, contributes) "
                      "VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                      (self.ns, plugin_id, version, package_digest, _j(contributes)))
            x.execute("INSERT INTO integrations.plugins (namespace, plugin_id, current_version, state, isolation_required) "
                      "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (namespace, plugin_id) DO UPDATE SET "
                      "current_version=EXCLUDED.current_version, state=EXCLUDED.state, "
                      "isolation_required=EXCLUDED.isolation_required, updated_at=now()",
                      (self.ns, plugin_id, version, state, isolation_required))

    def set_plugin_state(self, plugin_id: str, state: str) -> None:
        with self.db.tx() as x:
            x.execute("UPDATE integrations.plugins SET state=%s, updated_at=now() WHERE namespace=%s AND plugin_id=%s",
                      (state, self.ns, plugin_id))

    def plugin(self, plugin_id: str) -> dict | None:
        with self.db.tx() as x:
            return x.execute("SELECT * FROM integrations.plugins WHERE namespace=%s AND plugin_id=%s",
                             (self.ns, plugin_id)).fetchone()

    def plugins(self) -> list[dict]:
        with self.db.tx() as x:
            return x.execute("SELECT * FROM integrations.plugins WHERE namespace=%s ORDER BY plugin_id", (self.ns,)).fetchall()

    # ------------------------------------------------------------------ task pins
    def pin(self, objective_id: str, task_id: str, tool_number: str, integration_id: str, version: str,
            package_digest: str) -> dict:
        with self.db.tx() as x:
            x.execute("INSERT INTO integrations.task_pins (namespace, objective_id, task_id, tool_number, integration_id, "
                      "version, package_digest) VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                      (self.ns, objective_id, task_id, tool_number, integration_id, version, package_digest))
            return x.execute("SELECT * FROM integrations.task_pins WHERE namespace=%s AND objective_id=%s AND task_id=%s "
                             "AND tool_number=%s", (self.ns, objective_id, task_id, tool_number)).fetchone()

    def pins(self, integration_id: str | None = None) -> list[dict]:
        q, a = "SELECT * FROM integrations.task_pins WHERE namespace=%s", [self.ns]
        if integration_id:
            q += " AND integration_id=%s"
            a.append(integration_id)
        with self.db.tx() as x:
            return x.execute(q + " ORDER BY pinned_at", a).fetchall()

    def active_tasks_using(self, tool_numbers: list[str]) -> list[dict]:
        """Non-terminal Stage 01A tasks of THIS namespace pinned to any of these tool numbers whose CURRENT contract
        pool still contains the number. Pins are written when the controller plans the task (and at first execution
        as a fallback), so planned-but-not-started tasks count too."""
        if not tool_numbers:
            return []
        with self.db.tx() as x:
            return x.execute(
                "SELECT DISTINCT t.objective_id, t.task_id, t.state FROM integrations.task_pins p "
                "JOIN control.tasks t ON t.objective_id=p.objective_id AND t.task_id=p.task_id "
                "JOIN control.task_contracts c ON c.objective_id=t.objective_id AND c.task_id=t.task_id AND "
                "c.version=t.current_version "
                "WHERE p.namespace=%s AND p.tool_number = ANY(%s) AND "
                "t.state NOT IN ('COMPLETED','FAILED','CANCELLED') AND c.contract->'permitted_tool_ids' ? p.tool_number "
                "ORDER BY t.objective_id, t.task_id", (self.ns, list(tool_numbers))).fetchall()
