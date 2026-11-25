"""Model-independent execution interface (Stage 01A §22). No inference is implemented here — adapters for Llama,
Qwen and others arrive in Stage 03. Models without reliable native tool calling are driven through validated
structured-output proposals with bounded correction attempts (the controller's run loop)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .context import ContextPacket


@dataclass(frozen=True)
class ModelIdentity:
    provider: str
    model: str
    version: str


@dataclass(frozen=True)
class ModelCapabilities:
    context_tokens: int
    structured_output: bool
    native_tool_calling: bool


@dataclass
class ProposalResponse:
    raw: Any                       # JSON text or object; ALWAYS parsed and validated by the controller
    tokens_used: int = 0
    source: str = "unknown"
    meta: dict = field(default_factory=dict)


@runtime_checkable
class ModelAdapter(Protocol):
    def identity(self) -> ModelIdentity: ...
    def available(self) -> bool: ...
    def capabilities(self) -> ModelCapabilities: ...
    def propose(self, packet: ContextPacket, feedback: list[dict] | None, timeout_s: float) -> ProposalResponse: ...
    def cancel(self) -> None: ...


class ExplicitTestProposals:
    """TEST FIXTURE — NOT A MODEL. Replays proposals written explicitly by a test so the controller can be verified
    without claiming any LLM produced them (Stage 01A §22). Every response is labelled source='explicit-test'."""

    def __init__(self, proposals: list[Any], context_tokens: int = 8192):
        self._items = list(proposals)
        self.seen_packets: list[ContextPacket] = []
        self.seen_feedback: list[list[dict] | None] = []
        self._ctx = context_tokens

    def identity(self) -> ModelIdentity:
        return ModelIdentity("test", "explicit-proposals", "1")

    def available(self) -> bool:
        return True

    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(self._ctx, True, False)

    def propose(self, packet: ContextPacket, feedback, timeout_s: float) -> ProposalResponse:
        self.seen_packets.append(packet)
        self.seen_feedback.append(feedback)
        if not self._items:
            return ProposalResponse(raw="", source="explicit-test", meta={"exhausted": True})
        item = self._items.pop(0)
        if callable(item):
            item = item(packet, feedback)
        return ProposalResponse(raw=item, tokens_used=packet.estimated_tokens, source="explicit-test")

    def cancel(self) -> None:
        self._items.clear()
