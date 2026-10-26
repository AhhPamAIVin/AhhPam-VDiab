"""Re-embed every ACTIVE PatientInsight from Postgres into the patient-insights
collection of the currently configured embedding model.

Needed after switching embedding model or vector store: the reflect agent's
create-vs-update decision (upsert_insight.py) searches this collection, and
vectors from the old model are a different size and live in a different
collection (`patient_insights__<model>`), so they are never reused — Postgres
is the source of truth and the text is simply embedded again, locally.

Idempotent: point ids are the insight ids, so a re-run overwrites in place.

    python -m scripts.reindex_patient_insights [--batch-size 64] [--dry-run]
"""

from __future__ import annotations

import argparse
import logging

from sqlmodel import Session, col, select

from codebase.backend.db import engine
from codebase.backend.insights.models import InsightStatus, PatientInsight
from codebase.models.agents.nodes.upsert_insight import _embedding_metadata
from codebase.models.agents.tools.insight_tools import get_insights_collection
from codebase.models.rag.embeddings import get_embeddings_client

logger = logging.getLogger(__name__)


def reindex(*, batch_size: int = 64, dry_run: bool = False) -> int:
    with Session(engine) as session:
        insights = session.exec(select(PatientInsight).where(col(PatientInsight.status) == InsightStatus.ACTIVE)).all()
    logger.info("%d active insights to embed", len(insights))
    if dry_run or not insights:
        return len(insights)

    collection = get_insights_collection()
    embeddings_client = get_embeddings_client()
    for start in range(0, len(insights), batch_size):
        batch = insights[start : start + batch_size]
        vectors = embeddings_client.embed_documents([i.text for i in batch])
        collection.upsert(
            ids=[str(i.id) for i in batch],
            embeddings=vectors,
            documents=[i.text for i in batch],
            metadatas=[_embedding_metadata(i) for i in batch],
        )
        logger.info("embedded %d/%d", min(start + batch_size, len(insights)), len(insights))
    return len(insights)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--dry-run", action="store_true", help="count insights, embed nothing")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    count = reindex(batch_size=args.batch_size, dry_run=args.dry_run)
    print(
        f"{'would embed' if args.dry_run else 'embedded'} {count} active insights into {get_insights_collection().name}"
    )


if __name__ == "__main__":
    main()
