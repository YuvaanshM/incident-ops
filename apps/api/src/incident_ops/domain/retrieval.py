"""Pure invariants for the fixed embedding space."""

import math
from collections.abc import Sequence

EMBEDDING_DIMENSIONS = 1536
EMBEDDING_TEMPLATE = "remediation-v1"


def validate_vector(value: object) -> list[float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError("invalid embedding")
    if len(value) != EMBEDDING_DIMENSIONS:
        raise ValueError("invalid embedding dimensions")
    if any(type(item) not in (int, float) for item in value):
        raise ValueError("invalid embedding values")
    vector = [float(item) for item in value]
    norm = math.hypot(*vector)
    if not math.isfinite(norm) or norm == 0:
        raise ValueError("invalid embedding norm")
    # Normalize before float32 storage to avoid overflow/underflow in pgvector.
    return [item / norm for item in vector]
