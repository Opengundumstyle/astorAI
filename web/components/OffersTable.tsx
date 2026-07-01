import type { Offer } from "@/lib/types";

export function OffersTable({ offers }: { offers: Offer[] }) {
  if (offers.length === 0) {
    return <p className="text-sm" style={{ color: "var(--muted)" }}>No supplier offers.</p>;
  }
  return (
    <div className="card overflow-hidden">
      <div className="grid grid-cols-6 px-3 py-2 text-[10px] uppercase tracking-wide" style={{ color: "var(--muted)" }}>
        <span>Supplier</span><span>Region</span><span>SKU</span><span>Pack</span><span>Cost</span><span>Lead</span>
      </div>
      {offers.map((o) => (
        <div key={o.supplier_sku} className="grid grid-cols-6 px-3 py-2 text-sm" style={{ borderTop: "1px solid var(--border)" }}>
          <span>{o.supplier}</span>
          <span>{o.region}</span>
          <span>{o.supplier_sku}</span>
          <span>{o.pack_size ?? "—"}</span>
          <span>{o.cost} {o.currency}</span>
          <span>{o.lead_time_days != null ? `${o.lead_time_days}d` : "—"}</span>
        </div>
      ))}
    </div>
  );
}
