"""Literature Tool corpus (design doc 4.1): a small, deliberately narrow set of
PubMed abstracts, not a general-purpose literature index. Scoped to topics that
directly support the anchor scenario ("why did creatinine double") and the
design doc's own "well-modeled conditions" list (§4.5): acute kidney injury,
NSAID/ACE-inhibitor/diuretic nephrotoxicity (the "triple whammy" pattern,
§4.3.3), CKD, diabetic nephropathy, hypertension, sepsis-associated AKI. See
scripts/ingest_literature.py for the exact search queries used.

embedding is a pgvector column (OpenAI text-embedding-3-small, 1536 dims) for
semantic search; title/abstract are also plain-indexed for the keyword half of
the design doc's "hybrid retrieval (embeddings + keyword search)".
"""

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

EMBEDDING_DIM = 1536


class LiteratureAbstract(Base):
    __tablename__ = "literature_abstracts"

    pmid: Mapped[str] = mapped_column(String(16), primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    abstract: Mapped[str] = mapped_column(Text, nullable=False)
    topic: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    url: Mapped[str] = mapped_column(String(128), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
