export function KpiCard({
  label,
  value,
  accent = false,
  sub,
}: {
  label: string;
  value: string | number;
  accent?: boolean;
  sub?: string;
}) {
  return (
    <div
      className="card p-4"
      style={accent ? { background: "rgba(94,234,212,0.07)", borderColor: "rgba(94,234,212,0.2)" } : undefined}
    >
      <div className="text-[11px] uppercase tracking-wide" style={{ color: accent ? "var(--teal)" : "var(--muted)" }}>
        {label}
      </div>
      <div className="mt-1 text-2xl font-extrabold">{value}</div>
      {sub && <div className="mt-1 text-xs" style={{ color: "var(--muted)" }}>{sub}</div>}
    </div>
  );
}
