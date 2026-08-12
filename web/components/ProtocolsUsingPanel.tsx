import Link from "next/link";
import { ConfidenceBar } from "@/components/ConfidenceBar";
import { KindBadge } from "@/components/KindBadge";
import type { ProtocolLink } from "@/lib/types";

export function ProtocolsUsingPanel({ items }: { items: ProtocolLink[] }) {
  if (items.length === 0) {
    return (
      <p className="text-sm" style={{ color: "var(--muted)" }}>
        No protocols reference this product yet.
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-2">
      {items.map((p) => (
        <Link
          key={p.protocol_id}
          href={`/protocols/${p.protocol_id}`}
          className="card flex items-center justify-between gap-4 p-3"
        >
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold">{p.title}</div>
            <div className="truncate text-xs" style={{ color: "var(--muted)" }}>
              matched on “{p.material_name}”
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-3">
            <ConfidenceBar value={p.confidence} />
            <KindBadge kind={p.kind} />
          </div>
        </Link>
      ))}
    </div>
  );
}
