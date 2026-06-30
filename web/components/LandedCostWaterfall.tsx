import type { LandedCost } from "@/lib/types";

function money(n: number) {
  return `$${n.toFixed(2)}`;
}

function Row({ label, value, strong = false }: { label: string; value: string; strong?: boolean }) {
  return (
    <div
      className="flex items-center justify-between px-3 py-2 text-sm"
      style={{ borderTop: "1px solid var(--border)", fontWeight: strong ? 700 : 400 }}
    >
      <span style={{ color: strong ? "var(--text)" : "var(--muted)" }}>{label}</span>
      <span>{value}</span>
    </div>
  );
}

export function LandedCostWaterfall({ data }: { data: LandedCost }) {
  const hasInternals = data.ex_works !== undefined;
  return (
    <div className="card overflow-hidden">
      <div className="px-3 py-2 text-[11px] uppercase tracking-wide" style={{ color: "var(--teal)" }}>
        Landed cost ({data.currency})
      </div>
      {hasInternals && (
        <>
          <Row label="Ex-works" value={money(data.ex_works!)} />
          <Row
            label={`Tariff (${Math.round((data.duty_rate ?? 0) * 100)}%)`}
            value={money(data.tariff ?? 0)}
          />
          <Row label="Freight" value={money(data.freight ?? 0)} />
          <Row label="Margin" value={money(data.margin ?? 0)} />
        </>
      )}
      <Row label="Unit price" value={money(data.unit_price)} strong />
      <Row label={`Line total (qty ${data.qty})`} value={money(data.line_total)} strong />
    </div>
  );
}
