"""Filesystem cache of raw protocols.io payloads.

Persist-before-transform: every fetched page/detail is written here first, so
re-ranking or a mapping fix re-reads from disk and never re-hits the API. The
detail filename carries the version, which is what makes skip-if-persisted
version-aware — an updated protocol is a new file, a re-run of the same version
is a no-op."""
from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "q"


class RawStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def detail_path(self, protocol_id: str, version_id: str) -> Path:
        return self.root / "details" / f"{protocol_id}-v{version_id}.json"

    def has_detail(self, protocol_id: str, version_id: str) -> bool:
        return self.detail_path(protocol_id, version_id).exists()

    def write_detail(self, protocol_id: str, version_id: str, payload: dict) -> Path:
        path = self.detail_path(protocol_id, version_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def read_detail(self, protocol_id: str, version_id: str) -> dict:
        return json.loads(self.detail_path(protocol_id, version_id).read_text(encoding="utf-8"))

    def write_search_page(self, category_id: str, query: str, page: int, body: dict) -> Path:
        path = self.root / "searches" / category_id / f"{_slug(query)}-p{page}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        return path

    def write_manifest(self, manifest: dict, name: str = "manifest.json") -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def iter_details(self) -> Iterator[dict]:
        details = self.root / "details"
        if not details.exists():
            return
        for path in sorted(details.glob("*.json")):
            yield json.loads(path.read_text(encoding="utf-8"))
