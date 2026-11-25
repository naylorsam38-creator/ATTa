"""Local / project research sources (§27) — integration interface for Stage 07.

Stage 07 owns project knowledge, uploaded files, storage and indexing. Stage 11 does NOT duplicate any of that:
it defines how research consumes authorised documents, and works today only with files explicitly attached to a
research run (ResearchService.attach_local_source: content placed in the objective workspace, hash recorded, path
listed in the research contract). These sources are usable in OFFLINE / AIR_GAPPED mode."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .errors import ResearchError, ResearchErrorCode


@dataclass(frozen=True)
class AuthorisedDocument:
    document_id: str
    title: str | None
    content_type: str
    sha256: str
    data: bytes
    project_id: str | None = None
    authorised_for: str | None = None        # research_id or principal the authorisation applies to


@runtime_checkable
class ProjectKnowledgeSource(Protocol):
    """Implemented by Stage 07. Must enforce tenant/project authorisation itself (Stage 02 context)."""
    def list_documents(self, project_id: str, *, research_id: str) -> list[dict]: ...
    def read_document(self, document_id: str, *, research_id: str) -> AuthorisedDocument: ...
    def search(self, project_id: str, query: str, *, research_id: str, limit: int) -> list[dict]: ...


class UnavailableProjectKnowledge:
    """Default until Stage 07 is merged: every call is BLOCKED (never an empty success)."""

    def _blocked(self):
        raise ResearchError(ResearchErrorCode.BLOCKED_DEPENDENCY, "project knowledge / uploaded files are provided "
                            "by Stage 07, which is not integrated", {"dependency": "S07"})

    def list_documents(self, project_id, *, research_id):
        self._blocked()

    def read_document(self, document_id, *, research_id):
        self._blocked()

    def search(self, project_id, query, *, research_id, limit):
        self._blocked()
