export function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-20 overflow-hidden rounded" style={{ background: "rgba(255,255,255,0.08)" }}>
        <div className="gradient-accent h-full" style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs font-bold" style={{ color: "var(--teal)" }}>{value.toFixed(2)}</span>
    </div>
  );
}
