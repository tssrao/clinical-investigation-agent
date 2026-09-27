"""Ingest a small, deliberately narrow PubMed abstract corpus for the Literature
Tool (design doc 4.1) - not a general-purpose literature index. Topics are
scoped to the anchor scenario ("why did creatinine double") and the design
doc's own "well-modeled conditions" list (§4.5). See app/db/models/literature.py
for the reasoning.

Uses PubMed's public E-utilities API (esearch + efetch) - no API key required
at this volume (~35 total abstracts), and NCBI's documented rate limit without
one is ~3 requests/second, which this script respects with a small delay.

Safe to re-run - upserts by pmid (ON CONFLICT DO UPDATE), so re-running with an
adjusted topic list only adds/updates what changed.
"""

import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests
from openai import OpenAI
from sqlalchemy.dialects.postgresql import insert

from app.core.config import settings
from app.db.models.literature import LiteratureAbstract
from app.db.session import SessionLocal

ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
N_PER_TOPIC = 5
EMBEDDING_MODEL = "text-embedding-3-small"

# Scoped deliberately narrow - see module docstring.
TOPICS = {
    "acute_kidney_injury": "acute kidney injury creatinine elevation",
    "nsaid_nephrotoxicity": "NSAID nephrotoxicity kidney injury",
    "ace_inhibitor_diuretic_renal": "ACE inhibitor diuretic acute kidney injury triple whammy",
    "ckd_progression": "chronic kidney disease progression risk factors",
    "diabetic_nephropathy": "diabetic nephropathy diabetes kidney disease",
    "hypertension_renal": "hypertension chronic kidney disease renal function",
    "sepsis_aki": "sepsis associated acute kidney injury",
}


class PubMedSearchUnavailable(RuntimeError):
    pass


def search_pmids(query: str, retmax: int, max_retries: int = 3) -> list[str]:
    last_error = None
    for attempt in range(max_retries):
        resp = requests.get(ESEARCH_URL, params={
            "db": "pubmed", "term": query, "retmax": retmax,
            "retmode": "json", "sort": "relevance", "tool": "clinical-investigation-agent",
        }, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if "idlist" in data.get("esearchresult", {}):
            return data["esearchresult"]["idlist"]
        last_error = data["esearchresult"].get("ERROR", "unknown error")
        time.sleep(2 ** attempt)  # 1s, 2s, 4s
    raise PubMedSearchUnavailable(f"PubMed search backend unavailable after {max_retries} attempts: {last_error}")


def fetch_abstracts(pmids: list[str]) -> list[dict]:
    if not pmids:
        return []
    resp = requests.get(EFETCH_URL, params={
        "db": "pubmed", "id": ",".join(pmids), "rettype": "abstract",
        "retmode": "xml", "tool": "clinical-investigation-agent",
    }, timeout=15)
    resp.raise_for_status()

    root = ET.fromstring(resp.content)
    results = []
    for article in root.findall(".//PubmedArticle"):
        pmid = article.findtext(".//PMID")
        title = article.findtext(".//ArticleTitle") or ""
        # AbstractText can be split into multiple labeled sections (Background/Methods/...)
        abstract_parts = [el.text or "" for el in article.findall(".//AbstractText")]
        abstract = " ".join(part.strip() for part in abstract_parts if part.strip())
        if pmid and abstract:
            results.append({"pmid": pmid, "title": title, "abstract": abstract})
    return results


def main() -> None:
    client = OpenAI(api_key=settings.openai_api_key)
    session = SessionLocal()
    total = 0
    try:
        for topic, query in TOPICS.items():
            pmids = search_pmids(query, N_PER_TOPIC)
            time.sleep(0.4)  # stay comfortably under the ~3 req/s unauthenticated rate limit
            articles = fetch_abstracts(pmids)
            time.sleep(0.4)

            for article in articles:
                text_to_embed = f"{article['title']}\n\n{article['abstract']}"
                embedding = client.embeddings.create(
                    model=EMBEDDING_MODEL, input=text_to_embed
                ).data[0].embedding

                stmt = insert(LiteratureAbstract).values(
                    pmid=article["pmid"],
                    title=article["title"],
                    abstract=article["abstract"],
                    topic=topic,
                    url=f"https://pubmed.ncbi.nlm.nih.gov/{article['pmid']}/",
                    embedding=embedding,
                    created_at=datetime.now(timezone.utc),
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["pmid"],
                    set_={
                        "title": stmt.excluded.title,
                        "abstract": stmt.excluded.abstract,
                        "topic": stmt.excluded.topic,
                        "url": stmt.excluded.url,
                        "embedding": stmt.excluded.embedding,
                    },
                )
                session.execute(stmt)
                total += 1

            print(f"{topic}: {len(articles)} abstract(s)")

        session.commit()
        print(f"Total ingested/updated: {total}")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
