"""Deterministic research worker — a ModelAdapter that is NOT a model (§5, §26, §30).

Until Stage 03 connects a real model, the mechanical research steps (run this query, retrieve this URL, extract it,
find passages) are driven by this deterministic adapter. It goes through exactly the same Stage 01A path as a model
would: it only ever returns ToolProposal / CompletionClaim DATA, which the controller parses, the drift guard and
policy check, and the execution gateway executes within the task's numbered tool pool. It cannot add tools, change
its contract or raise budgets — it has no API to do so.

It reads retrieved content only as data fields of tool outputs (snapshot paths, ok flags); it never interprets page
text. Every response is labelled source='deterministic-research-worker' so nothing is mistaken for model output."""
from __future__ import annotations

import uuid
from typing import Any

from backend.orchestration.context import ContextPacket
from backend.orchestration.models import ModelCapabilities, ModelIdentity, ProposalResponse

SOURCE = "deterministic-research-worker"


class DeterministicResearchWorker:
    def __init__(self, order: dict, *, context_tokens: int = 8192):
        self.order = order
        self.ctx = context_tokens
        self.nonce = uuid.uuid4().hex[:8]       # new proposal ids per attempt (a resumed attempt never replays)
        self.cancelled = False
        self.seen_packets: list[ContextPacket] = []

    def identity(self) -> ModelIdentity:
        return ModelIdentity("atta", SOURCE, "1")

    def available(self) -> bool:
        return True

    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(self.ctx, True, False)

    def cancel(self) -> None:
        self.cancelled = True

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _outputs(packet: ContextPacket) -> dict[str, list[Any]]:
        out: dict[str, list[Any]] = {}
        for u in packet.untrusted:
            p = u.get("provenance") or {}
            if p.get("kind") == "tool_output":
                out.setdefault(p.get("tool_id"), []).append(u.get("content"))
        return out

    def _proposal(self, task: dict, step: str, tool: str, op: str, args: dict, expected: str) -> dict:
        return {"proposal_id": f"r-{task['task_id']}-{step}-{self.nonce}", "task_id": task["task_id"],
                "contract_version": task["contract_version"], "tool_id": tool, "operation": op, "arguments": args,
                "target_resources": [], "expected_result": expected,
                "referenced_requirement_ids": list(task["requirement_ids"])}

    def _complete(self, task: dict) -> dict:
        return {"proposal_id": f"r-{task['task_id']}-done-{self.nonce}", "task_id": task["task_id"],
                "contract_version": task["contract_version"], "claim": "complete", "evidence_ids": []}

    # ------------------------------------------------------------------ the adapter
    def propose(self, packet: ContextPacket, feedback, timeout_s: float) -> ProposalResponse:
        self.seen_packets.append(packet)
        task = packet.trusted["task"]
        if self.cancelled:
            return ProposalResponse(raw="", source=SOURCE, meta={"cancelled": True})
        raw = self._next(task, self._outputs(packet), feedback)
        return ProposalResponse(raw=raw, tokens_used=packet.estimated_tokens, source=SOURCE)

    def _next(self, task: dict, outs: dict, feedback) -> dict:
        o = self.order
        if feedback:          # a rejected step is not retried blindly: finish and let verification decide
            return self._complete(task)
        kind = o["kind"]
        if kind == "search":
            if o["tool"] not in outs:
                return self._proposal(task, "search", o["tool"], "search",
                                      {"provider_kind": o["provider_kind"], "endpoint": o["endpoint"],
                                       "query": o["query"], "max_results": o["max_results"],
                                       **({"options": o["options"]} if o.get("options") else {})}, "search results")
            return self._complete(task)
        if kind == "compare":
            if o["tool"] not in outs:
                return self._proposal(task, "compare", o["tool"], "compare", {"extracted_paths": o["paths"]},
                                      "pairwise comparison")
            return self._complete(task)
        # retrieve / follow / local: [fetch] -> extract -> find
        fetch_tool, extract_tool, find_tool = o.get("fetch_tool"), o["extract_tool"], o.get("find_tool")
        if kind in ("retrieve", "follow"):
            if fetch_tool not in outs:
                args = {"url": o["url"]} if kind == "retrieve" else {"url": o["url"],
                                                                     "extracted_path": o["from_extracted"]}
                return self._proposal(task, "fetch", fetch_tool, "fetch", args, "snapshot stored")
            fetched = outs[fetch_tool][-1] or {}
            if not fetched.get("ok"):
                return self._complete(task)
            snapshot = fetched["snapshot_path"]
            extract_args = {"snapshot_path": snapshot}
        else:
            extract_args = {"snapshot_path": o["path"], "local_source": True,
                            **({"content_type": o["content_type"]} if o.get("content_type") else {})}
        if extract_tool not in outs:
            return self._proposal(task, "extract", extract_tool, "extract", extract_args, "typed extraction")
        extracted = outs[extract_tool][-1] or {}
        if not extracted.get("ok") or not find_tool or not o.get("terms"):
            return self._complete(task)
        if find_tool not in outs:
            return self._proposal(task, "find", find_tool, "find",
                                  {"extracted_path": extracted["extracted_path"], "terms": o["terms"][:20],
                                   "max_hits": o.get("max_hits", 8)}, "evidence candidates")
        return self._complete(task)
