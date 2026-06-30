import { KpiCard } from "@/components/KpiCard";
import { api } from "@/lib/api";
import type { Stats } from "@/lib/types";

export default async function DashboardPage() {
  let stats: Stats | null = null;
  let error: string | null = null;
  try {
    stats = await api.getStats();
  } catch (e) {
    error = e instanceof Error ? e.message : "Failed to load stats";
  }

  return (
    <div className="flex flex-col gap-6">
      <section className="card p-6">
        <h1 className="text-xl font-bold">China↔US sourcing, priced end to end.</h1>
        <p className="mt-1 text-sm" style={{ color: "var(--muted)" }}>
          Catalog health &amp; sourcing overview
        </p>
      </section>

      {error && (
        <div className="card p-4" style={{ borderColor: "#7f1d1d", color: "#fca5a5" }}>
          {error} — is the API running on {process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}?
        </div>
      )}

      {stats && (
        <section className="grid grid-cols-2 gap-4 md:grid-cols-4">
          <KpiCard label="Products" value={stats.products} />
          <KpiCard label="Offers" value={stats.offers} />
          <KpiCard
            label="Equivalences"
            value={stats.equivalences.total}
            sub={`${stats.equivalences.exact} exact · ${stats.equivalences.substitute} sub`}
          />
          <KpiCard label="Avg savings" value={`${Math.round(stats.avg_savings * 100)}%`} accent />
        </section>
      )}

      {stats && stats.products === 0 && (
        <div className="card p-6 text-sm" style={{ color: "var(--muted)" }}>
          No products yet. Head to <a href="/ingest" style={{ color: "var(--teal)" }}>Ingest &amp; Browse</a> to load a supplier catalog.
        </div>
      )}
    </div>
  );
}
