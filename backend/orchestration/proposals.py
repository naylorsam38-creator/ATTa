"""Structured proposal parsing (Stage 01A §12). Malformed output is rejected with field-level errors and the
permitted alternatives; it is never interpreted as a command."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from .contracts import CompletionClaim, TaskContract, ToolProposal


@dataclass
class ParseResult:
    proposal: ToolProposal | CompletionClaim | None
    errors: list[dict] = field(default_factory=list)
    raw: Any = None


def parse_proposal(raw: Any, contract: TaskContract, tool_ops: dict[str, list[str]]) -> ParseResult:
    """raw: a JSON string or dict produced by a model adapter (or an explicit test proposal)."""
    data = raw
    if isinstance(raw, (bytes, str)):
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            return ParseResult(None, [{"field": "<root>", "message": f"not JSON: {exc.msg if hasattr(exc, 'msg') else exc}",
                                       "permitted": ["a JSON object: ToolProposal or CompletionClaim"]}], raw)
    if not isinstance(data, dict):
        return ParseResult(None, [{"field": "<root>", "message": "proposal must be a JSON object",
                                   "permitted": ["ToolProposal", "CompletionClaim"]}], raw)
    model = CompletionClaim if "claim" in data else ToolProposal
    try:
        p = model.model_validate(data)
    except ValidationError as exc:
        errs = []
        for e in exc.errors():
            fld = ".".join(str(x) for x in e["loc"]) or "<root>"
            permitted = []
            if fld == "tool_id":
                permitted = list(contract.permitted_tool_ids)
            elif fld == "operation":
                permitted = sorted({o for n in contract.permitted_tool_ids for o in tool_ops.get(n, [])})
            elif e["type"] == "extra_forbidden":
                permitted = sorted(model.model_fields)
            errs.append({"field": fld, "message": e["msg"], "permitted": permitted})
        return ParseResult(None, errs, raw)
    return ParseResult(p, [], raw)
