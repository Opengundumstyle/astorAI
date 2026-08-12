import Link from "next/link";
import { api } from "@/lib/api";
import type { ProtocolsListPage } from "@/lib/types";

export default async function ProtocolsBrowsePage({
  searchParams,
}: {
  searchParams: Promise<{ q?: string; page?: string }>;
}) {
  const { q, page } = await searchParams;
  const pageNum = Number(page) || 1;

  let data: ProtocolsListPage | null = null;
  let error: string | null = null;
  try {
    data = await api.listProtocols({ q, page: pageNum });
  } catch (e) {
    error = e instanceof Error ? e.message : "Failed to load protocols";
  }

  const pageSize = data?.page_size ?? 20;
  const total = data?.total ?? 0;
  const hasPrev = pageNum > 1;
  const hasNext = pageNum * pageSize < total;
  const qs = (p: number) => {
    const s = new URLSearchParams();
    if (q) s.set("q", q);
    s.set("page", String(p));
    return `/protocols?${s.toString()}`;
  };

  return (
    <div className="flex flex-col gap-6">
      <section className="card p-6">
        <h1 className="text-xl font-bold">Protocols</h1>
        <p className="mt-1 text-sm" style={{ color: "var(--muted)" }}>
          Review-ranked protocols from protocols.io, mapped to your catalog.
          {total ? ` ${total} total.` : ""}
        </p>
        <form action="/protocols" method="get" className="mt-4 flex gap-2">
          <input
            type="search"
            name="q"
            defaultValue={q ?? ""}
            placeholder="Search protocols…"
            className="w-full rounded-lg px-3 py-2 text-sm"
            style={{ background: "var(--panel)", border: "1px solid var(--border)", color: "inherit" }}
          />
          <button
            type="submit"
            className="rounded-lg px-4 py-2 text-sm font-semibold"
            style={{ background: "var(--panel)", border: "1px solid var(--border)" }}
          >
            Search
          </button>
        </form>
      </section>

      {error && (
        <div className="card p-4" style={{ borderColor: "#7f1d1d", color: "#fca5a5" }}>
          {error} — is the API running on {process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}?
        </div>
      )}

      {data && data.items.length === 0 && (
        <div className="card p-6 text-sm" style={{ color: "var(--muted)" }}>
          No protocols match{q ? ` “${q}”` : ""}.
        </div>
      )}

      {data && data.items.length > 0 && (
        <div className="flex flex-col gap-2">
          {data.items.map((p) => (
            <Link key={p.id} href={`/protocols/${p.id}`} className="card flex items-center justify-between gap-4 p-4">
              <div className="min-w-0">
                <div className="truncate text-sm font-semibold">{p.title}</div>
                <div className="text-xs" style={{ color: "var(--muted)" }}>
                  {p.source} · review score {p.rank_score.toFixed(1)}
                </div>
              </div>
              <span
                className="shrink-0 rounded px-2 py-1 text-xs font-bold"
                style={{
                  color: p.product_count > 0 ? "var(--teal)" : "var(--muted)",
                  background: p.product_count > 0 ? "rgba(94,234,212,0.12)" : "var(--panel)",
                  border: "1px solid var(--border)",
                }}
              >
                {p.product_count} products
              </span>
            </Link>
          ))}
        </div>
      )}

      {data && (hasPrev || hasNext) && (
        <div className="flex items-center justify-between text-sm">
          {hasPrev ? (
            <Link href={qs(pageNum - 1)} style={{ color: "var(--teal)" }}>← Previous</Link>
          ) : <span />}
          <span style={{ color: "var(--muted)" }}>Page {pageNum}</span>
          {hasNext ? (
            <Link href={qs(pageNum + 1)} style={{ color: "var(--teal)" }}>Next →</Link>
          ) : <span />}
        </div>
      )}
    </div>
  );
}
