"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { ProductSummary } from "@/lib/types";

export function ProductsTable() {
  const [q, setQ] = useState("");
  const [items, setItems] = useState<ProductSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const t = setTimeout(() => {
      api
        .listProducts({ q, page: 1 })
        .then((page) => {
          if (!active) return;
          setItems(page.items);
          setTotal(page.total);
        })
        .catch((e) => active && setError(e.message));
    }, 250);
    return () => {
      active = false;
      clearTimeout(t);
    };
  }, [q]);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">Products <span style={{ color: "var(--muted)" }}>({total})</span></h2>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search name / brand / MPN"
          className="rounded-md px-3 py-1.5 text-sm"
          style={{ background: "var(--panel)", border: "1px solid var(--border)", color: "var(--text)" }}
        />
      </div>

      {error && <p className="text-sm" style={{ color: "#fca5a5" }}>{error}</p>}

      <div className="card overflow-hidden">
        <div className="grid grid-cols-5 px-3 py-2 text-[10px] uppercase tracking-wide" style={{ color: "var(--muted)" }}>
          <span>Astor SKU</span><span>Name</span><span>Category</span><span>Offers</span><span>Best landed</span>
        </div>
        {items.map((p) => (
          <Link key={p.id} href={`/products/${p.id}`} className="grid grid-cols-5 px-3 py-2 text-sm" style={{ borderTop: "1px solid var(--border)" }}>
            <span style={{ color: "var(--teal)" }}>{p.astor_sku}</span>
            <span>{p.name}</span>
            <span>{p.category}</span>
            <span>{p.offer_count}</span>
            <span>{p.best_landed != null ? `$${p.best_landed.toFixed(2)}` : "—"}</span>
          </Link>
        ))}
        {items.length === 0 && !error && (
          <div className="px-3 py-4 text-sm" style={{ color: "var(--muted)", borderTop: "1px solid var(--border)" }}>
            No products match.
          </div>
        )}
      </div>
    </div>
  );
}
