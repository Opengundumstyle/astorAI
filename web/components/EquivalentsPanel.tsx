import Link from "next/link";
import { ConfidenceBar } from "@/components/ConfidenceBar";
import { KindBadge } from "@/components/KindBadge";
import type { Equivalent } from "@/lib/types";

export function EquivalentsPanel({ items }: { items: Equivalent[] }) {
  if (items.length === 0) {
    return <p className="text-sm" style={{ color: "var(--muted)" }}>No equivalents found.</p>;
  }
  return (
    <div className="flex flex-col gap-2">
      {items.map((e) => (
        <Link key={e.id} href={`/products/${e.id}`} className="card flex items-center justify-between p-3">
          <div>
            <div className="text-sm font-semibold">{e.name}</div>
            <div className="text-xs" style={{ color: "var(--muted)" }}>
              {e.astor_sku}{e.brand ? ` · ${e.brand}` : ""}
            </div>
          </div>
          <div className="flex items-center gap-3">
            <ConfidenceBar value={e.confidence} />
            <KindBadge kind={e.kind} />
          </div>
        </Link>
      ))}
    </div>
  );
}
