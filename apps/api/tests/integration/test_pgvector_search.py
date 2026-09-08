"""The schema supports provider-isolated exact cosine retrieval for RAG."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from incident_ops.infrastructure.database.models import HistoricalIncidentModel


def _embedding(*leading_values: float) -> list[float]:
    return [*leading_values, *([0.0] * (1536 - len(leading_values)))]


async def test_exact_cosine_search_filters_to_one_embedding_space(
    database_engine: AsyncEngine,
    clean_database: None,
) -> None:
    async with database_engine.begin() as connection:
        await connection.execute(
            HistoricalIncidentModel.__table__.insert(),
            [
                {
                    "source_id": "google-nearest",
                    "title": "Nearest Google precedent",
                    "description": "Synthetic checkout latency.",
                    "resolution_summary": "Restarted a synthetic worker.",
                    "service": "checkout",
                    "severity": "low",
                    "resolution_steps": ["restart synthetic worker"],
                    "embedding_provider": "google",
                    "embedding_model": "gemini-embedding-2",
                    "embedding": _embedding(1.0, 0.0),
                },
                {
                    "source_id": "google-farther",
                    "title": "Farther Google precedent",
                    "description": "Synthetic checkout queue delay.",
                    "resolution_summary": "Drained a synthetic queue.",
                    "service": "checkout",
                    "severity": "low",
                    "resolution_steps": ["drain synthetic queue"],
                    "embedding_provider": "google",
                    "embedding_model": "gemini-embedding-2",
                    "embedding": _embedding(0.2, 0.98),
                },
                {
                    "source_id": "openai-nearest-but-excluded",
                    "title": "Nearest other-space precedent",
                    "description": "Synthetic record in another vector space.",
                    "resolution_summary": "Must not be mixed with Google vectors.",
                    "service": "checkout",
                    "severity": "low",
                    "resolution_steps": [],
                    "embedding_provider": "openai",
                    "embedding_model": "text-embedding-3-small",
                    "embedding": _embedding(1.0, 0.0),
                },
            ],
        )

    distance = HistoricalIncidentModel.embedding.cosine_distance(_embedding(1.0, 0.0))
    statement = (
        select(HistoricalIncidentModel.source_id, distance.label("distance"))
        .where(
            HistoricalIncidentModel.embedding_provider == "google",
            HistoricalIncidentModel.embedding_model == "gemini-embedding-2",
            HistoricalIncidentModel.service == "checkout",
        )
        .order_by(distance, HistoricalIncidentModel.id)
        .limit(2)
    )
    async with database_engine.connect() as connection:
        results = (await connection.execute(statement)).all()

    assert [row.source_id for row in results] == ["google-nearest", "google-farther"]
    assert results[0].distance < results[1].distance
