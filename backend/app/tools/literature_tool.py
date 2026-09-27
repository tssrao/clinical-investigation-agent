"""Literature Tool (design doc 4.1): hybrid retrieval (OpenAI embeddings for
semantic similarity + keyword search) over the small PubMed abstract corpus in
app/db/models/literature.py. Returns supporting evidence + citations.

Retrieval is the only real logic here - no generative LLM call, just an
embedding call (a representation, not free-text generation) plus a SQL query.
Results are merged by taking the union of the top semantic and top keyword
matches, since a pure blend-by-score would need normalizing pgvector cosine
distance against a keyword relevance score on a comparable scale, which isn't
worth the complexity for a corpus this small (~35 rows).
"""

from dataclasses import dataclass

from openai import OpenAI
from sqlalchemy import select, text

from app.core.config import settings
from app.db.models.literature import LiteratureAbstract
from app.db.session import SessionLocal

EMBEDDING_MODEL = "text-embedding-3-small"


@dataclass
class LiteratureResult:
    pmid: str
    title: str
    abstract: str
    topic: str
    url: str
    match_type: str  # "semantic" | "keyword" | "both"


def _embed_query(query: str) -> list[float]:
    client = OpenAI(api_key=settings.openai_api_key)
    return client.embeddings.create(model=EMBEDDING_MODEL, input=query).data[0].embedding


def search_literature(query: str, top_k: int = 5) -> list[LiteratureResult]:
    query_embedding = _embed_query(query)
    session = SessionLocal()
    try:
        semantic_rows = session.execute(
            select(LiteratureAbstract)
            .order_by(LiteratureAbstract.embedding.cosine_distance(query_embedding))
            .limit(top_k)
        ).scalars().all()

        keyword_rows = session.execute(
            select(LiteratureAbstract)
            .where(
                text('title ILIKE :pattern OR abstract ILIKE :pattern')
            )
            .params(pattern=f"%{query}%")
            .limit(top_k)
        ).scalars().all()

        semantic_pmids = {r.pmid for r in semantic_rows}
        keyword_pmids = {r.pmid for r in keyword_rows}

        results = []
        for row in semantic_rows:
            match_type = "both" if row.pmid in keyword_pmids else "semantic"
            results.append(LiteratureResult(
                pmid=row.pmid, title=row.title, abstract=row.abstract,
                topic=row.topic, url=row.url, match_type=match_type,
            ))
        for row in keyword_rows:
            if row.pmid not in semantic_pmids:
                results.append(LiteratureResult(
                    pmid=row.pmid, title=row.title, abstract=row.abstract,
                    topic=row.topic, url=row.url, match_type="keyword",
                ))
        return results
    finally:
        session.close()
