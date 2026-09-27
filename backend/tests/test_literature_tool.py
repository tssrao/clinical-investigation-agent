"""Literature Tool tests. Inserts a small set of test-only abstracts (clearly
fake pmids, never real PubMed data) via a fixture with setup/teardown, so
nothing lingers in the real literature_abstracts table regardless of test
outcome. Requires OPENAI_API_KEY (used for embeddings, not text generation -
this tool calls no generative LLM).
"""

from datetime import datetime, timezone

import pytest
from openai import OpenAI

from app.core.config import settings
from app.db.models.literature import LiteratureAbstract
from app.db.session import SessionLocal
from app.tools.literature_tool import search_literature

requires_openai_key = pytest.mark.skipif(
    not settings.openai_api_key, reason="OPENAI_API_KEY not configured"
)

FIXTURES = [
    ("TEST001", "Acute Kidney Injury from NSAID Use",
     "NSAIDs can cause acute kidney injury by reducing renal blood flow via "
     "prostaglandin inhibition, especially in volume-depleted patients.",
     "test_aki"),
    ("TEST002", "Diabetic Retinopathy Screening Guidelines",
     "Annual dilated eye examinations are recommended for patients with type "
     "2 diabetes to screen for diabetic retinopathy.",
     "test_diabetes_eye"),
    ("TEST003", "The Triple Whammy: ACE inhibitors, Diuretics, and NSAIDs",
     "Concurrent use of ACE inhibitors, diuretics, and NSAIDs significantly "
     "increases risk of acute kidney injury through combined hemodynamic "
     "effects on the kidney.",
     "test_triple_whammy"),
]


@pytest.fixture
def literature_fixtures():
    client = OpenAI(api_key=settings.openai_api_key)
    session = SessionLocal()
    pmids = [f[0] for f in FIXTURES]
    try:
        for pmid, title, abstract, topic in FIXTURES:
            embedding = client.embeddings.create(
                model="text-embedding-3-small", input=f"{title}\n\n{abstract}"
            ).data[0].embedding
            session.merge(LiteratureAbstract(
                pmid=pmid, title=title, abstract=abstract, topic=topic,
                url=f"https://example.test/{pmid}", embedding=embedding,
                created_at=datetime.now(timezone.utc),
            ))
        session.commit()
        yield
    finally:
        session.query(LiteratureAbstract).filter(LiteratureAbstract.pmid.in_(pmids)).delete(
            synchronize_session=False
        )
        session.commit()
        session.close()


@requires_openai_key
class TestSearchLiterature:
    def test_semantic_search_ranks_most_relevant_first(self, literature_fixtures):
        """A query about combined-medication kidney injury should rank the
        'triple whammy' abstract above a merely kidney-related one, and both
        above the completely unrelated diabetic retinopathy abstract.
        """
        results = search_literature(
            "kidney injury from combined blood pressure medications", top_k=3
        )
        pmids_in_order = [r.pmid for r in results]
        assert pmids_in_order[0] == "TEST003"
        assert "TEST002" in pmids_in_order  # returned (top_k=3, only 3 fixtures) but ranked last
        assert pmids_in_order[-1] == "TEST002"

    def test_keyword_match_is_found_even_if_not_top_semantic_hit(self, literature_fixtures):
        results = search_literature("diabetic retinopathy", top_k=1)
        pmids = [r.pmid for r in results]
        assert "TEST002" in pmids

    def test_results_carry_citation_fields(self, literature_fixtures):
        results = search_literature("acute kidney injury", top_k=1)
        assert results
        r = results[0]
        assert r.pmid
        assert r.title
        assert r.url.startswith("https://")
