import Link from "next/link";
import { ConfidenceBar } from "@/components/ConfidenceBar";
import { KindBadge } from "@/components/KindBadge";
import type { MaterialLink } from "@/lib/types";

export function ProtocolCartPanel({ items }: { items: MaterialLink[] }) {
  if (items.length === 0) {
    return (
      <p className="text-sm" style={{ color: "var(--muted)" }}>
        No catalog products matched this protocol&apos;s materials yet.
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-2">
      {items.map((m, i) => (
        <Link
          key={`${m.product_id}-${i}`}
          href={`/products/${m.product_id}`}
          className="card flex items-center justify-between gap-4 p-3"
        >
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold">{m.product_name}</div>
            <div className="truncate text-xs" style={{ color: "var(--muted)" }}>
              needs “{m.material_name}”{m.brand ? ` · ${m.brand}` : ""}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-3">
            <ConfidenceBar value={m.confidence} />
            <KindBadge kind={m.kind} />
          </div>
        </Link>
      ))}
    </div>
  );
}
