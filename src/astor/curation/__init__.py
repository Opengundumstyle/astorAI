"""Curated domain knowledge (docs/curation/*.csv) as a process-wide, validated singleton.

`tables()` loads on first use and caches. `create_app` calls it at startup so a
malformed CSV fails boot. Tests point CURATION_DIR at a temp dir and call reset().
"""
from __future__ import annotations

from pathlib import Path

from astor.curation.loader import CurationError, CurationTables, load_all

CURATION_DIR: Path = Path(__file__).resolve().parents[3] / "docs" / "curation"

_cache: CurationTables | None = None


def tables() -> CurationTables:
    global _cache
    if _cache is None:
        _cache = load_all(CURATION_DIR)
    return _cache


def reset() -> None:
    global _cache
    _cache = None


__all__ = ["CURATION_DIR", "CurationError", "CurationTables", "reset", "tables"]
