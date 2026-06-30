export function DemoBanner() {
  if (process.env.NEXT_PUBLIC_DEMO !== "1") return null;
  return (
    <div className="px-8 py-2 text-center text-xs" style={{ background: "rgba(94,234,212,0.1)", color: "var(--teal)" }}>
      Demo data — equivalence scores use the offline DevEmbedder and are not semantically meaningful.
    </div>
  );
}
