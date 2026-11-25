# MASTER IMPLEMENTATION REGISTER

Generated from `documentation/requirements/requirement_register.yaml` by `python scripts/requirement_register.py render`. Edit the YAML, not this file.

Register version: 1 — updated 2026-11-25 — current stage: S01

Status counts: BLOCKED 0, IMPLEMENTED 0, NOT_STARTED 38, PARTIAL 2, VERIFIED 0

No capability is marked IMPLEMENTED or VERIFIED until its stage gate passes with recorded evidence.

## Summary

| ID | Capability | Stage | Depends on | Status |
|---|---|---|---|---|
| CAP-01 | AI conversation | S03 | FOUNDATION, CAP-02, CAP-35, CAP-39 | **NOT_STARTED** |
| CAP-02 | Model selection | S03 | FOUNDATION, CAP-39 | **NOT_STARTED** |
| CAP-03 | Extended thinking | S03 | CAP-01, CAP-02 | **NOT_STARTED** |
| CAP-04 | Chat management | S04 | CAP-01, CAP-35 | **NOT_STARTED** |
| CAP-05 | Memory | S05 | CAP-01, CAP-38 | **NOT_STARTED** |
| CAP-06 | Personalisation | S05 | CAP-35, CAP-05 | **NOT_STARTED** |
| CAP-07 | Projects | S06 | CAP-04, CAP-35 | **NOT_STARTED** |
| CAP-08 | Knowledge base | S06 | CAP-07, CAP-09 | **NOT_STARTED** |
| CAP-09 | File uploads | S06 | CAP-35, CAP-39 | **NOT_STARTED** |
| CAP-10 | Vision | S07 | CAP-09, CAP-02 | **NOT_STARTED** |
| CAP-11 | Web search | S08 | CAP-01, CAP-39 | **NOT_STARTED** |
| CAP-12 | Deep research | S08 | CAP-11, CAP-26 | **NOT_STARTED** |
| CAP-13 | Artifacts | S09 | CAP-01 | **NOT_STARTED** |
| CAP-14 | Document creation | S10 | CAP-13 | **NOT_STARTED** |
| CAP-15 | Spreadsheet creation | S10 | CAP-13 | **NOT_STARTED** |
| CAP-16 | Presentation creation | S10 | CAP-13 | **NOT_STARTED** |
| CAP-17 | Design workspace | S09 | CAP-13, CAP-10 | **NOT_STARTED** |
| CAP-18 | Application creation | S11 | CAP-13, CAP-19, CAP-20 | **NOT_STARTED** |
| CAP-19 | Code execution | S11 | CAP-39, CAP-40 | **NOT_STARTED** |
| CAP-20 | Coding agent | S12 | CAP-19, CAP-26, CAP-29 | **NOT_STARTED** |
| CAP-21 | Connectors | S13 | CAP-35, CAP-39 | **NOT_STARTED** |
| CAP-22 | MCP | S13 | CAP-21, CAP-39 | **NOT_STARTED** |
| CAP-23 | Skills | S14 | CAP-24, CAP-39 | **NOT_STARTED** |
| CAP-24 | Plugins | S14 | CAP-23, CAP-39 | **NOT_STARTED** |
| CAP-25 | Cowork | S15 | CAP-26, CAP-29, CAP-33 | **NOT_STARTED** |
| CAP-26 | Parallel agents | S15 | CAP-01, CAP-40 | **NOT_STARTED** |
| CAP-27 | Browser control | S16 | CAP-39, CAP-19 | **NOT_STARTED** |
| CAP-28 | Computer control | S16 | CAP-39, CAP-27 | **NOT_STARTED** |
| CAP-29 | Local file access | S16 | CAP-39 | **NOT_STARTED** |
| CAP-30 | Scheduling | S17 | CAP-26, CAP-31 | **NOT_STARTED** |
| CAP-31 | Notifications | S17 | CAP-35 | **NOT_STARTED** |
| CAP-32 | Voice | S07 | CAP-01 | **NOT_STARTED** |
| CAP-33 | Collaboration | S18 | CAP-07, CAP-35 | **NOT_STARTED** |
| CAP-34 | Cross-device synchronisation | S18 | CAP-04, CAP-35 | **NOT_STARTED** |
| CAP-35 | Account management | S02 | FOUNDATION, CAP-39 | **NOT_STARTED** |
| CAP-36 | Subscription management | S19 | CAP-35, CAP-37 | **NOT_STARTED** |
| CAP-37 | Administration | S19 | CAP-35, CAP-39 | **NOT_STARTED** |
| CAP-38 | Data management | S19 | CAP-35, CAP-09 | **NOT_STARTED** |
| CAP-39 | Security and approvals | S02 | FOUNDATION | **PARTIAL** |
| CAP-40 | Operational management | S20 | FOUNDATION | **PARTIAL** |

## Detail

### CAP-01 — AI conversation

- **Implementation stage:** S03
- **Status:** NOT_STARTED
- **Dependencies:** FOUNDATION, CAP-02, CAP-35, CAP-39
- **Source files:** **planned:** `backend/orchestration/conversation_graph.py`, `backend/model_gateway/gateway.py`, `apps/web/app/chat/`
- **Interface controls:** Chat composer<br>Streaming message list<br>Stop / regenerate controls
- **API endpoints:** **planned:** `POST /v1/conversations`, `POST /v1/conversations/{id}/messages`, `GET /v1/conversations/{id}/stream (SSE)`
- **Security controls:** Authenticated principal required<br>Per-tenant isolation (RLS)<br>Prompt/response size limits<br>Output streamed through policy engine
- **Acceptance tests:** S03-CAP01-T01: primary user journey works end to end<br>S03-CAP01-T02: security controls enforced (negative tests)<br>S03-CAP01-T03: regression suite unchanged
- **Evidence:** none yet

### CAP-02 — Model selection

- **Implementation stage:** S03
- **Status:** NOT_STARTED — Stage 01 only configures model endpoints (Ollama, llama.cpp, vLLM) and probes their health; no selection logic yet.
- **Dependencies:** FOUNDATION, CAP-39
- **Source files:** **planned:** `backend/model_gateway/gateway.py`, `backend/model_gateway/catalog.py`
- **Interface controls:** Model picker<br>Per-conversation model badge
- **API endpoints:** **planned:** `GET /v1/models`, `PATCH /v1/conversations/{id} {model}`
- **Security controls:** Only operator-registered endpoints selectable<br>API keys read from env/secret store only<br>Paid endpoints require allow_paid_infrastructure
- **Acceptance tests:** S03-CAP02-T01: primary user journey works end to end<br>S03-CAP02-T02: security controls enforced (negative tests)<br>S03-CAP02-T03: regression suite unchanged
- **Evidence:** none yet

### CAP-03 — Extended thinking

- **Implementation stage:** S03
- **Status:** NOT_STARTED
- **Dependencies:** CAP-01, CAP-02
- **Source files:** **planned:** `backend/orchestration/thinking.py`
- **Interface controls:** Thinking toggle and budget<br>Collapsible reasoning display
- **API endpoints:** **planned:** `POST /v1/conversations/{id}/messages {thinking}`
- **Security controls:** Thinking budget bounded by deadlines<br>Reasoning content tenant-scoped
- **Acceptance tests:** S03-CAP03-T01: primary user journey works end to end<br>S03-CAP03-T02: security controls enforced (negative tests)<br>S03-CAP03-T03: regression suite unchanged
- **Evidence:** none yet

### CAP-04 — Chat management

- **Implementation stage:** S04
- **Status:** NOT_STARTED
- **Dependencies:** CAP-01, CAP-35
- **Source files:** **planned:** `backend/api/routes_conversations.py`, `apps/web/app/(chat)/sidebar.tsx`
- **Interface controls:** Sidebar history<br>Rename, star, delete, search
- **API endpoints:** **planned:** `GET /v1/conversations`, `PATCH /v1/conversations/{id}`, `DELETE /v1/conversations/{id}`
- **Security controls:** Owner-only access<br>Soft delete with retention policy<br>Idempotent mutations
- **Acceptance tests:** S04-CAP04-T01: primary user journey works end to end<br>S04-CAP04-T02: security controls enforced (negative tests)<br>S04-CAP04-T03: regression suite unchanged
- **Evidence:** none yet

### CAP-05 — Memory

- **Implementation stage:** S05
- **Status:** NOT_STARTED
- **Dependencies:** CAP-01, CAP-38
- **Source files:** **planned:** `backend/orchestration/memory.py`, `database/migrations/ (memory tables, pgvector)`
- **Interface controls:** Memory settings panel<br>View / edit / forget memories
- **API endpoints:** **planned:** `GET /v1/memory`, `DELETE /v1/memory/{id}`, `PATCH /v1/settings/memory`
- **Security controls:** User-controlled opt-in<br>Per-user encryption scope<br>Forget is verifiable deletion
- **Acceptance tests:** S05-CAP05-T01: primary user journey works end to end<br>S05-CAP05-T02: security controls enforced (negative tests)<br>S05-CAP05-T03: regression suite unchanged
- **Evidence:** none yet

### CAP-06 — Personalisation

- **Implementation stage:** S05
- **Status:** NOT_STARTED
- **Dependencies:** CAP-35, CAP-05
- **Source files:** **planned:** `backend/api/routes_preferences.py`
- **Interface controls:** Profile preferences<br>Custom instructions<br>Style selection
- **API endpoints:** **planned:** `GET /v1/preferences`, `PUT /v1/preferences`
- **Security controls:** Instructions treated as user data, never as system policy<br>Length limits
- **Acceptance tests:** S05-CAP06-T01: primary user journey works end to end<br>S05-CAP06-T02: security controls enforced (negative tests)<br>S05-CAP06-T03: regression suite unchanged
- **Evidence:** none yet

### CAP-07 — Projects

- **Implementation stage:** S06
- **Status:** NOT_STARTED
- **Dependencies:** CAP-04, CAP-35
- **Source files:** **planned:** `backend/api/routes_projects.py`
- **Interface controls:** Project list<br>Project workspace with instructions and files
- **API endpoints:** **planned:** `POST /v1/projects`, `GET /v1/projects`, `PATCH /v1/projects/{id}`
- **Security controls:** Project membership checks<br>Tenant RLS
- **Acceptance tests:** S06-CAP07-T01: primary user journey works end to end<br>S06-CAP07-T02: security controls enforced (negative tests)<br>S06-CAP07-T03: regression suite unchanged
- **Evidence:** none yet

### CAP-08 — Knowledge base

- **Implementation stage:** S06
- **Status:** NOT_STARTED
- **Dependencies:** CAP-07, CAP-09
- **Source files:** **planned:** `workers/ingestion/`, `backend/orchestration/retrieval.py`
- **Interface controls:** Project knowledge panel<br>Source citations in answers
- **API endpoints:** **planned:** `POST /v1/projects/{id}/knowledge`, `GET /v1/projects/{id}/knowledge`
- **Security controls:** Ingestion in sandboxed worker<br>Content-type validation<br>Embeddings tenant-scoped (pgvector + RLS)
- **Acceptance tests:** S06-CAP08-T01: primary user journey works end to end<br>S06-CAP08-T02: security controls enforced (negative tests)<br>S06-CAP08-T03: regression suite unchanged
- **Evidence:** none yet

### CAP-09 — File uploads

- **Implementation stage:** S06
- **Status:** NOT_STARTED
- **Dependencies:** CAP-35, CAP-39
- **Source files:** **planned:** `backend/api/routes_files.py`, `backend/storage/object_store.py`
- **Interface controls:** Drag-and-drop upload<br>Attachment chips
- **API endpoints:** **planned:** `POST /v1/files (multipart)`, `GET /v1/files/{id}`
- **Security controls:** Size and type limits<br>Malware scanning hook<br>Pre-signed URLs with short expiry<br>S3 bucket private by default
- **Acceptance tests:** S06-CAP09-T01: primary user journey works end to end<br>S06-CAP09-T02: security controls enforced (negative tests)<br>S06-CAP09-T03: regression suite unchanged
- **Evidence:** none yet

### CAP-10 — Vision

- **Implementation stage:** S07
- **Status:** NOT_STARTED
- **Dependencies:** CAP-09, CAP-02
- **Source files:** **planned:** `backend/model_gateway/multimodal.py`
- **Interface controls:** Image attachment preview<br>Image understanding in chat
- **API endpoints:** **planned:** `POST /v1/conversations/{id}/messages (image parts)`
- **Security controls:** Image re-encoding to strip active content<br>EXIF location stripped
- **Acceptance tests:** S07-CAP10-T01: primary user journey works end to end<br>S07-CAP10-T02: security controls enforced (negative tests)<br>S07-CAP10-T03: regression suite unchanged
- **Evidence:** none yet

### CAP-11 — Web search

- **Implementation stage:** S08
- **Status:** NOT_STARTED
- **Dependencies:** CAP-01, CAP-39
- **Source files:** **planned:** `capabilities/web_search/`
- **Interface controls:** Search toggle<br>Inline citations
- **API endpoints:** **planned:** `POST /v1/capabilities/web.search/executions`
- **Security controls:** Egress allow-list<br>Result content treated as untrusted data<br>Rate limits
- **Acceptance tests:** S08-CAP11-T01: primary user journey works end to end<br>S08-CAP11-T02: security controls enforced (negative tests)<br>S08-CAP11-T03: regression suite unchanged
- **Evidence:** none yet

### CAP-12 — Deep research

- **Implementation stage:** S08
- **Status:** NOT_STARTED
- **Dependencies:** CAP-11, CAP-26
- **Source files:** **planned:** `backend/orchestration/research_graph.py`, `workers/research/`
- **Interface controls:** Research progress view<br>Report with sources
- **API endpoints:** **planned:** `POST /v1/research`, `GET /v1/research/{id}`
- **Security controls:** Temporal workflow with budget limits<br>Per-task cost ceiling<br>Untrusted-content isolation
- **Acceptance tests:** S08-CAP12-T01: primary user journey works end to end<br>S08-CAP12-T02: security controls enforced (negative tests)<br>S08-CAP12-T03: regression suite unchanged
- **Evidence:** none yet

### CAP-13 — Artifacts

- **Implementation stage:** S09
- **Status:** NOT_STARTED
- **Dependencies:** CAP-01
- **Source files:** **planned:** `backend/api/routes_artifacts.py`, `apps/web/app/artifacts/`
- **Interface controls:** Artifact side panel<br>Versions<br>Share link
- **API endpoints:** **planned:** `POST /v1/artifacts`, `GET /v1/artifacts/{id}`, `GET /v1/artifacts/{id}/versions`
- **Security controls:** Sandboxed iframe with CSP<br>No ambient credentials in artifact runtime
- **Acceptance tests:** S09-CAP13-T01: primary user journey works end to end<br>S09-CAP13-T02: security controls enforced (negative tests)<br>S09-CAP13-T03: regression suite unchanged
- **Evidence:** none yet

### CAP-14 — Document creation

- **Implementation stage:** S10
- **Status:** NOT_STARTED
- **Dependencies:** CAP-13
- **Source files:** **planned:** `capabilities/document_create/`
- **Interface controls:** Document artifact editor<br>Export DOCX/PDF/MD
- **API endpoints:** **planned:** `POST /v1/capabilities/document.create/executions`
- **Security controls:** Generated files scanned before download<br>Template injection guards
- **Acceptance tests:** S10-CAP14-T01: primary user journey works end to end<br>S10-CAP14-T02: security controls enforced (negative tests)<br>S10-CAP14-T03: regression suite unchanged
- **Evidence:** none yet

### CAP-15 — Spreadsheet creation

- **Implementation stage:** S10
- **Status:** NOT_STARTED
- **Dependencies:** CAP-13
- **Source files:** **planned:** `capabilities/spreadsheet_create/`
- **Interface controls:** Spreadsheet artifact grid<br>Export XLSX/CSV
- **API endpoints:** **planned:** `POST /v1/capabilities/spreadsheet.create/executions`
- **Security controls:** Formula injection (CSV) neutralised<br>Size limits
- **Acceptance tests:** S10-CAP15-T01: primary user journey works end to end<br>S10-CAP15-T02: security controls enforced (negative tests)<br>S10-CAP15-T03: regression suite unchanged
- **Evidence:** none yet

### CAP-16 — Presentation creation

- **Implementation stage:** S10
- **Status:** NOT_STARTED
- **Dependencies:** CAP-13
- **Source files:** **planned:** `capabilities/presentation_create/`
- **Interface controls:** Slide artifact viewer<br>Export PPTX/PDF
- **API endpoints:** **planned:** `POST /v1/capabilities/presentation.create/executions`
- **Security controls:** Embedded media validated<br>Size limits
- **Acceptance tests:** S10-CAP16-T01: primary user journey works end to end<br>S10-CAP16-T02: security controls enforced (negative tests)<br>S10-CAP16-T03: regression suite unchanged
- **Evidence:** none yet

### CAP-17 — Design workspace

- **Implementation stage:** S09
- **Status:** NOT_STARTED
- **Dependencies:** CAP-13, CAP-10
- **Source files:** **planned:** `apps/web/app/design/`, `packages/design_system/`
- **Interface controls:** Canvas<br>Component palette<br>Design tokens
- **API endpoints:** **planned:** `POST /v1/designs`, `GET /v1/designs/{id}`
- **Security controls:** Sandboxed rendering<br>Asset upload via CAP-09 controls
- **Acceptance tests:** S09-CAP17-T01: primary user journey works end to end<br>S09-CAP17-T02: security controls enforced (negative tests)<br>S09-CAP17-T03: regression suite unchanged
- **Evidence:** none yet

### CAP-18 — Application creation

- **Implementation stage:** S11
- **Status:** NOT_STARTED
- **Dependencies:** CAP-13, CAP-19, CAP-20
- **Source files:** **planned:** `capabilities/app_create/`, `04-deployment/ (ATTa app builder pipeline, existing)`
- **Interface controls:** App builder flow<br>Preview and deploy status
- **API endpoints:** **planned:** `POST /v1/apps`, `GET /v1/apps/{id}`
- **Security controls:** Builds in isolated containers<br>Qualification gate before deploy (existing ATTa QUALIFIED gate)<br>Deployment needs approval
- **Acceptance tests:** S11-CAP18-T01: primary user journey works end to end<br>S11-CAP18-T02: security controls enforced (negative tests)<br>S11-CAP18-T03: regression suite unchanged
- **Evidence:** none yet

### CAP-19 — Code execution

- **Implementation stage:** S11
- **Status:** NOT_STARTED
- **Dependencies:** CAP-39, CAP-40
- **Source files:** **planned:** `workers/sandbox/`, `capabilities/code_execute/`
- **Interface controls:** Code cell output<br>Execution log
- **API endpoints:** **planned:** `POST /v1/capabilities/code.execute/executions`
- **Security controls:** Container/microVM sandbox, no host mounts<br>CPU/memory/time limits from workers settings<br>No network by default
- **Acceptance tests:** S11-CAP19-T01: primary user journey works end to end<br>S11-CAP19-T02: security controls enforced (negative tests)<br>S11-CAP19-T03: regression suite unchanged
- **Evidence:** none yet

### CAP-20 — Coding agent

- **Implementation stage:** S12
- **Status:** NOT_STARTED
- **Dependencies:** CAP-19, CAP-26, CAP-29
- **Source files:** **planned:** `backend/orchestration/coding_agent_graph.py`
- **Interface controls:** Agent task view<br>Diff review<br>Approve changes
- **API endpoints:** **planned:** `POST /v1/agents/coding/tasks`, `GET /v1/agents/coding/tasks/{id}`
- **Security controls:** Writes limited to approved workspace<br>Every file change reviewed/approved<br>Build-controller style protected tests
- **Acceptance tests:** S12-CAP20-T01: primary user journey works end to end<br>S12-CAP20-T02: security controls enforced (negative tests)<br>S12-CAP20-T03: regression suite unchanged
- **Evidence:** none yet

### CAP-21 — Connectors

- **Implementation stage:** S13
- **Status:** NOT_STARTED
- **Dependencies:** CAP-35, CAP-39
- **Source files:** **planned:** `backend/connectors/`
- **Interface controls:** Connector gallery<br>OAuth connect/disconnect
- **API endpoints:** **planned:** `GET /v1/connectors`, `POST /v1/connectors/{id}/connect`, `DELETE /v1/connectors/{id}`
- **Security controls:** OAuth with PKCE<br>Tokens encrypted at rest<br>Least-privilege scopes
- **Acceptance tests:** S13-CAP21-T01: primary user journey works end to end<br>S13-CAP21-T02: security controls enforced (negative tests)<br>S13-CAP21-T03: regression suite unchanged
- **Evidence:** none yet

### CAP-22 — MCP

- **Implementation stage:** S13
- **Status:** NOT_STARTED
- **Dependencies:** CAP-21, CAP-39
- **Source files:** **planned:** `backend/connectors/mcp_client.py`
- **Interface controls:** MCP server management<br>Tool approval prompts
- **API endpoints:** **planned:** `GET /v1/mcp/servers`, `POST /v1/mcp/servers`
- **Security controls:** Tool calls pass policy engine<br>Server allow-list<br>Tool output treated as untrusted
- **Acceptance tests:** S13-CAP22-T01: primary user journey works end to end<br>S13-CAP22-T02: security controls enforced (negative tests)<br>S13-CAP22-T03: regression suite unchanged
- **Evidence:** none yet

### CAP-23 — Skills

- **Implementation stage:** S14
- **Status:** NOT_STARTED
- **Dependencies:** CAP-24, CAP-39
- **Source files:** **planned:** `backend/capability_registry/ (skill manifests)`, `capabilities/`
- **Interface controls:** Skill library<br>Enable/disable per user
- **API endpoints:** **planned:** `GET /v1/skills`, `PATCH /v1/skills/{id}`
- **Security controls:** Manifest validation (S01 registry)<br>Signed skill packages
- **Acceptance tests:** S14-CAP23-T01: primary user journey works end to end<br>S14-CAP23-T02: security controls enforced (negative tests)<br>S14-CAP23-T03: regression suite unchanged
- **Evidence:** none yet

### CAP-24 — Plugins

- **Implementation stage:** S14
- **Status:** NOT_STARTED
- **Dependencies:** CAP-23, CAP-39
- **Source files:** **planned:** `backend/capability_registry/plugins.py`
- **Interface controls:** Plugin marketplace<br>Install/uninstall
- **API endpoints:** **planned:** `GET /v1/plugins`, `POST /v1/plugins/{id}/install`
- **Security controls:** Signature verification<br>Permission review at install<br>No self-granted permissions (S01 policy rule)
- **Acceptance tests:** S14-CAP24-T01: primary user journey works end to end<br>S14-CAP24-T02: security controls enforced (negative tests)<br>S14-CAP24-T03: regression suite unchanged
- **Evidence:** none yet

### CAP-25 — Cowork

- **Implementation stage:** S15
- **Status:** NOT_STARTED
- **Dependencies:** CAP-26, CAP-29, CAP-33
- **Source files:** **planned:** `backend/orchestration/cowork.py`, `apps/desktop/`
- **Interface controls:** Cowork task board<br>Shared agent workspace
- **API endpoints:** **planned:** `POST /v1/cowork/sessions`, `GET /v1/cowork/sessions/{id}`
- **Security controls:** Human approval for side effects<br>Workspace-scoped file access
- **Acceptance tests:** S15-CAP25-T01: primary user journey works end to end<br>S15-CAP25-T02: security controls enforced (negative tests)<br>S15-CAP25-T03: regression suite unchanged
- **Evidence:** none yet

### CAP-26 — Parallel agents

- **Implementation stage:** S15
- **Status:** NOT_STARTED
- **Dependencies:** CAP-01, CAP-40
- **Source files:** **planned:** `backend/orchestration/parallel.py`, `workers/agents/`
- **Interface controls:** Parallel task monitor
- **API endpoints:** **planned:** `POST /v1/agents/batches`, `GET /v1/agents/batches/{id}`
- **Security controls:** Per-agent permission scoping<br>Concurrency limits (workers.max_concurrency)<br>Temporal durable state
- **Acceptance tests:** S15-CAP26-T01: primary user journey works end to end<br>S15-CAP26-T02: security controls enforced (negative tests)<br>S15-CAP26-T03: regression suite unchanged
- **Evidence:** none yet

### CAP-27 — Browser control

- **Implementation stage:** S16
- **Status:** NOT_STARTED
- **Dependencies:** CAP-39, CAP-19
- **Source files:** **planned:** `workers/browser/ (Playwright)`, `capabilities/browser_control/`
- **Interface controls:** Live browser view<br>Step approvals
- **API endpoints:** **planned:** `POST /v1/capabilities/browser.control/executions`
- **Security controls:** Isolated browser profile<br>Navigation allow-list<br>Approval for form submission and purchases
- **Acceptance tests:** S16-CAP27-T01: primary user journey works end to end<br>S16-CAP27-T02: security controls enforced (negative tests)<br>S16-CAP27-T03: regression suite unchanged
- **Evidence:** none yet

### CAP-28 — Computer control

- **Implementation stage:** S16
- **Status:** NOT_STARTED
- **Dependencies:** CAP-39, CAP-27
- **Source files:** **planned:** `apps/desktop/computer_control/`
- **Interface controls:** Screen view<br>Action approvals
- **API endpoints:** **planned:** `Desktop IPC only (no public endpoint)`
- **Security controls:** Explicit per-session consent<br>Critical-risk actions always require approval<br>Kill switch
- **Acceptance tests:** S16-CAP28-T01: primary user journey works end to end<br>S16-CAP28-T02: security controls enforced (negative tests)<br>S16-CAP28-T03: regression suite unchanged
- **Evidence:** none yet

### CAP-29 — Local file access

- **Implementation stage:** S16
- **Status:** NOT_STARTED
- **Dependencies:** CAP-39
- **Source files:** **planned:** `apps/desktop/local_files/`
- **Interface controls:** Folder grant dialog<br>File browser
- **API endpoints:** **planned:** `Desktop IPC only (no public endpoint)`
- **Security controls:** User-granted folders only<br>Path traversal prevention<br>Read-only by default
- **Acceptance tests:** S16-CAP29-T01: primary user journey works end to end<br>S16-CAP29-T02: security controls enforced (negative tests)<br>S16-CAP29-T03: regression suite unchanged
- **Evidence:** none yet

### CAP-30 — Scheduling

- **Implementation stage:** S17
- **Status:** NOT_STARTED
- **Dependencies:** CAP-26, CAP-31
- **Source files:** **planned:** `backend/task_manager/scheduler.py`
- **Interface controls:** Scheduled tasks list<br>Cron editor
- **API endpoints:** **planned:** `POST /v1/schedules`, `GET /v1/schedules`, `DELETE /v1/schedules/{id}`
- **Security controls:** Scheduled runs re-checked against current permissions<br>Temporal schedules
- **Acceptance tests:** S17-CAP30-T01: primary user journey works end to end<br>S17-CAP30-T02: security controls enforced (negative tests)<br>S17-CAP30-T03: regression suite unchanged
- **Evidence:** none yet

### CAP-31 — Notifications

- **Implementation stage:** S17
- **Status:** NOT_STARTED
- **Dependencies:** CAP-35
- **Source files:** **planned:** `backend/notifications/`
- **Interface controls:** Notification centre<br>Preferences
- **API endpoints:** **planned:** `GET /v1/notifications`, `PATCH /v1/notifications/{id}`
- **Security controls:** No sensitive content in push payloads<br>Per-channel opt-in
- **Acceptance tests:** S17-CAP31-T01: primary user journey works end to end<br>S17-CAP31-T02: security controls enforced (negative tests)<br>S17-CAP31-T03: regression suite unchanged
- **Evidence:** none yet

### CAP-32 — Voice

- **Implementation stage:** S07
- **Status:** NOT_STARTED
- **Dependencies:** CAP-01
- **Source files:** **planned:** `backend/model_gateway/speech.py`, `apps/mobile/`
- **Interface controls:** Push-to-talk<br>Voice mode
- **API endpoints:** **planned:** `POST /v1/voice/sessions (WebRTC/WebSocket)`
- **Security controls:** Microphone consent<br>Audio retention policy
- **Acceptance tests:** S07-CAP32-T01: primary user journey works end to end<br>S07-CAP32-T02: security controls enforced (negative tests)<br>S07-CAP32-T03: regression suite unchanged
- **Evidence:** none yet

### CAP-33 — Collaboration

- **Implementation stage:** S18
- **Status:** NOT_STARTED
- **Dependencies:** CAP-07, CAP-35
- **Source files:** **planned:** `backend/collaboration/`
- **Interface controls:** Share dialog<br>Presence<br>Comments
- **API endpoints:** **planned:** `POST /v1/shares`, `GET /v1/shares/{id}`
- **Security controls:** Role-based sharing<br>Link expiry<br>Audit trail
- **Acceptance tests:** S18-CAP33-T01: primary user journey works end to end<br>S18-CAP33-T02: security controls enforced (negative tests)<br>S18-CAP33-T03: regression suite unchanged
- **Evidence:** none yet

### CAP-34 — Cross-device synchronisation

- **Implementation stage:** S18
- **Status:** NOT_STARTED
- **Dependencies:** CAP-04, CAP-35
- **Source files:** **planned:** `backend/sync/`, `packages/api_client/`
- **Interface controls:** Sync status indicator
- **API endpoints:** **planned:** `GET /v1/sync/changes?cursor=`, `WebSocket /v1/sync/stream`
- **Security controls:** Device registration and revocation<br>Cursor-based, tenant-scoped change feed
- **Acceptance tests:** S18-CAP34-T01: primary user journey works end to end<br>S18-CAP34-T02: security controls enforced (negative tests)<br>S18-CAP34-T03: regression suite unchanged
- **Evidence:** none yet

### CAP-35 — Account management

- **Implementation stage:** S02
- **Status:** NOT_STARTED
- **Dependencies:** FOUNDATION, CAP-39
- **Source files:** **planned:** `backend/authentication/`, `backend/api/routes_accounts.py`
- **Interface controls:** Sign up / sign in<br>Profile<br>Sessions and devices
- **API endpoints:** **planned:** `POST /v1/auth/sessions`, `DELETE /v1/auth/sessions/{id}`, `GET /v1/accounts/me`
- **Security controls:** Argon2id password hashing<br>MFA (TOTP/WebAuthn)<br>Session rotation<br>Rate-limited login
- **Acceptance tests:** S02-CAP35-T01: primary user journey works end to end<br>S02-CAP35-T02: security controls enforced (negative tests)<br>S02-CAP35-T03: regression suite unchanged
- **Evidence:** none yet

### CAP-36 — Subscription management

- **Implementation stage:** S19
- **Status:** NOT_STARTED
- **Dependencies:** CAP-35, CAP-37
- **Source files:** **planned:** `backend/billing/`
- **Interface controls:** Plan and usage page<br>Upgrade/downgrade
- **API endpoints:** **planned:** `GET /v1/billing/subscription`, `POST /v1/billing/checkout`
- **Security controls:** Payment provider webhooks verified by signature<br>No card data stored
- **Acceptance tests:** S19-CAP36-T01: primary user journey works end to end<br>S19-CAP36-T02: security controls enforced (negative tests)<br>S19-CAP36-T03: regression suite unchanged
- **Evidence:** none yet

### CAP-37 — Administration

- **Implementation stage:** S19
- **Status:** NOT_STARTED
- **Dependencies:** CAP-35, CAP-39
- **Source files:** **planned:** `backend/admin/`, `apps/web/app/admin/`
- **Interface controls:** Admin console<br>User and tenant management
- **API endpoints:** **planned:** `GET /v1/admin/users`, `PATCH /v1/admin/users/{id}`
- **Security controls:** Admin role required<br>All admin actions audited<br>Break-glass procedure
- **Acceptance tests:** S19-CAP37-T01: primary user journey works end to end<br>S19-CAP37-T02: security controls enforced (negative tests)<br>S19-CAP37-T03: regression suite unchanged
- **Evidence:** none yet

### CAP-38 — Data management

- **Implementation stage:** S19
- **Status:** NOT_STARTED
- **Dependencies:** CAP-35, CAP-09
- **Source files:** **planned:** `backend/data_management/`
- **Interface controls:** Export my data<br>Delete account<br>Retention settings
- **API endpoints:** **planned:** `POST /v1/data/exports`, `DELETE /v1/accounts/me`
- **Security controls:** Verified deletion across Postgres, Redis, S3<br>Export encryption
- **Acceptance tests:** S19-CAP38-T01: primary user journey works end to end<br>S19-CAP38-T02: security controls enforced (negative tests)<br>S19-CAP38-T03: regression suite unchanged
- **Evidence:** none yet

### CAP-39 — Security and approvals

- **Implementation stage:** S02
- **Status:** PARTIAL — Stage 01 delivers the central policy interface with default DENY and the no-self-grant rule; authentication, authorisation and approvals are Stage 02.
- **Dependencies:** FOUNDATION
- **Source files:** **implemented:** `backend/policy_engine/engine.py`, `backend/authentication/__init__.py`, `database/policies/0001_default_deny.sql`<br>**planned:** `backend/policy_engine/engine.py`, `backend/authentication/`
- **Interface controls:** Approval prompts<br>Permission review
- **API endpoints:** **implemented:** `All protected /v1 routes return 403 permission_denied (Stage 01 default DENY)`<br>**planned:** `GET /v1/approvals`, `POST /v1/approvals/{id}/decision`
- **Security controls:** Central policy engine on every capability execution<br>Default DENY for protected operations<br>No self-granted permissions<br>Risk classification on every capability
- **Acceptance tests:** S01-T13: protected operations default to DENY (passed in Stage 01)<br>S02-CAP39-T01: primary user journey works end to end<br>S02-CAP39-T02: security controls enforced (negative tests)<br>S02-CAP39-T03: regression suite unchanged
- **Evidence:** STAGE_01_VERIFICATION.md<br>evidence/stage_01/

### CAP-40 — Operational management

- **Implementation stage:** S20
- **Status:** PARTIAL — Stage 01 delivers the service manager, liveness/readiness, healthcheck, structured logging, bounded restarts and the ops.system_status capability; dashboards, metrics and alerting are Stage 20.
- **Dependencies:** FOUNDATION
- **Source files:** **implemented:** `backend/service_manager/manager.py`, `backend/service_manager/control.py`, `backend/service_manager/probes.py`, `backend/api/app.py`, `capabilities/ops_system_status/handler.py`, `start.sh`, `START.bat`, `HEALTHCHECK.bat`<br>**planned:** `backend/service_manager/`, `scripts/`
- **Interface controls:** Status page<br>Health dashboard
- **API endpoints:** **implemented:** `GET /v1/health/live`, `GET /v1/health/ready`, `GET /v1/system/version`<br>**planned:** `GET /v1/health/live`, `GET /v1/health/ready`, `GET /v1/system/version`
- **Security controls:** Loopback binding until S02<br>Readiness from real probes<br>Structured, redacted logs
- **Acceptance tests:** S01-T03, S01-T04, S01-T05, S01-T10, S01-T11, S01-T14 (passed in Stage 01)<br>S20-CAP40-T01: primary user journey works end to end<br>S20-CAP40-T02: security controls enforced (negative tests)<br>S20-CAP40-T03: regression suite unchanged
- **Evidence:** STAGE_01_VERIFICATION.md<br>evidence/stage_01/
