"""Provider-neutral retrieval contracts and the versioned embedding template."""

import hashlib
import json
import re
import unicodedata
from typing import Annotated, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from incident_ops.domain.retrieval import EMBEDDING_TEMPLATE

Text200 = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=200)
]
Text2000 = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=2000)
]
ServiceName = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=100)
]
Severity = Literal["low", "medium", "high", "critical"]


class EmbeddingPort(Protocol):
    @property
    def provider(self) -> str: ...
    @property
    def model(self) -> str: ...
    async def embed(self, text: str) -> list[float]: ...


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class HistoricalRecord(StrictContract):
    id: UUID
    title: Text200
    description: Text2000
    service: ServiceName
    severity: Severity
    status: Literal["resolved"]
    resolution_summary: Text2000
    resolution_steps: Annotated[
        list[
            Annotated[
                str,
                StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=500),
            ]
        ],
        Field(min_length=1, max_length=20),
    ]
    provenance: Literal["seeded_demo_data"]

    @field_validator("title", "description", "service", "resolution_summary", "resolution_steps")
    @classmethod
    def plain_source_text(cls, value: str | list[str]) -> str | list[str]:
        for item in [value] if isinstance(value, str) else value:
            if re.search(
                r"<[^>]*>|https?://|\bBearer\s|\bsk-[A-Za-z0-9]|(?:password|secret|token|api_key)\s*[:=]",
                item,
                re.I,
            ):
                raise ValueError("source must contain plain synthetic text without credentials")
            if any(
                unicodedata.category(char).startswith("C") and char not in "\n\t" for char in item
            ):
                raise ValueError("source must contain visible plain text")
        return value

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class RetrievalRequest(StrictContract):
    query: Text2000 | None = None
    service: ServiceName | None = None
    severity: Severity | None = None
    limit: Annotated[int, Field(strict=True, ge=3, le=5)] = 3


class HistoricalSource(StrictContract):
    source_incident_id: UUID
    source_version: int
    title: str
    description: str
    service: str
    severity: Severity
    resolution_summary: str
    resolution_steps: list[str]
    provenance: Literal["seeded_demo_data"] = "seeded_demo_data"


class RemediationSuggestion(HistoricalSource):
    similarity_score: float = Field(ge=-1, le=1)


class RemediationResponse(StrictContract):
    incident_id: UUID
    incident_version: int
    advisory: Literal[True] = True
    provenance: Literal["seeded_demo_data"] = "seeded_demo_data"
    retrieval_mode: Literal["deterministic_demo", "semantic"]
    status: Literal["matched", "no_matches", "empty_corpus"]
    suggestions: list[RemediationSuggestion]


def _bounded_plain_text(value: str, budget: int) -> str:
    # Input is data, never instructions. Remove markup, invisible controls and common credentials.
    value = re.sub(r"<[^>]*>.*?</[^>]*>|<[^>]*>", " ", value, flags=re.S)
    value = "".join(c for c in value if not unicodedata.category(c).startswith("C") or c in "\n\t")
    value = re.sub(
        r"https?://\S+|\bBearer\s+\S+|\bsk-[A-Za-z0-9_-]+|(?:password|secret|token|api_key)\s*[:=]\s*\S+",
        "[redacted]",
        value,
        flags=re.I,
    )
    return value.encode()[:budget].decode("utf-8", errors="ignore")


def query_text(
    *, title: str, description: str, service: str, severity: str, summary: str = ""
) -> str:
    fields = {
        "template": EMBEDDING_TEMPLATE,
        "title": _bounded_plain_text(title, 600),
        "description": _bounded_plain_text(description, 3000),
        "service": _bounded_plain_text(service, 300),
        "severity": severity,
        "resolution_summary": _bounded_plain_text(summary, 2000),
    }
    # Literal JSON structure preserves field boundaries even with injection-like prose.
    rendered = json.dumps(fields, ensure_ascii=False, sort_keys=True)
    while len(rendered.encode()) > 8000:
        largest = max(
            ("title", "description", "service", "resolution_summary"),
            key=lambda key: len(fields[key].encode()),
        )
        fields[largest] = fields[largest][: len(fields[largest]) // 2]
        rendered = json.dumps(fields, ensure_ascii=False, sort_keys=True)
    return rendered


def embedding_text(record: HistoricalRecord) -> str:
    return query_text(
        title=record.title,
        description=record.description,
        service=record.service,
        severity=record.severity,
        summary=record.resolution_summary,
    )
