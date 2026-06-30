export function KindBadge({ kind }: { kind: "exact" | "substitute" }) {
  const isExact = kind === "exact";
  return (
    <span
      className="rounded px-2 py-0.5 text-[10px] font-bold uppercase"
      style={{
        color: isExact ? "#0d1322" : "var(--teal)",
        background: isExact ? "var(--teal)" : "rgba(94,234,212,0.12)",
      }}
    >
      {kind}
    </span>
  );
}
