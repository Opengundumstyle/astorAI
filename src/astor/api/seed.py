"""Demo seed: ingest the sample CN catalog with the offline DevEmbedder.

Requires a running Postgres + pgvector (the matcher uses vector ops). It only
removes the need for embedding-provider API keys, not the need for a database.
"""
from __future__ import annotations

import logging
from pathlib import Path

from astor.api import repo

log = logging.getLogger(__name__)

_SAMPLE = Path("data/sample_supplier_cn.csv")


def seed_demo(session) -> dict:
    if not _SAMPLE.exists():
        log.warning("demo seed skipped: %s not found", _SAMPLE)
        return {"extracted": 0, "products": 0, "offers": 0, "equivalences_written": 0}
    result = repo.run_ingest(session, _SAMPLE, "Sample CN", "CN", "public", run_match=True)
    log.info("demo seed: %s", result)
    return result
