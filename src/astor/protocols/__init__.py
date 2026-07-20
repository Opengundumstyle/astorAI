"""Protocol ingest — the offline pipeline that turns a source protocol into a
neutral RawProtocol (ARCHITECTURE.md §9, stage 1). Parallel to `astor.catalog`:
a source-adapter boundary keeps source schemas (protocols.io today) out of the
engine, exactly as catalog extraction keeps supplier formats out.

v1 scope (see ARCHITECTURE.md §4 override): single source = protocols.io,
ranked by the source's own review/engagement signal; scientific-grounding
(citation) layer deferred.
"""
