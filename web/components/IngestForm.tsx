"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type { IngestResult } from "@/lib/types";

const INPUT = {
  background: "var(--panel)",
  border: "1px solid var(--border)",
  color: "var(--text)",
} as const;

export function IngestForm() {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<IngestResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const form = new FormData(e.currentTarget);
      form.set("run_match", form.get("run_match") ? "true" : "false");
      setResult(await api.ingest(form));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ingest failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="card flex flex-col gap-3 p-5">
      <h2 className="text-sm font-semibold">Ingest supplier catalog</h2>
      <div className="grid grid-cols-2 gap-3">
        <input name="supplier" placeholder="Supplier name" required className="rounded-md px-3 py-2 text-sm" style={INPUT} />
        <select name="region" className="rounded-md px-3 py-2 text-sm" style={INPUT} defaultValue="CN">
          <option value="CN">CN</option>
          <option value="US">US</option>
          <option value="OTHER">OTHER</option>
        </select>
        <select name="tier" className="rounded-md px-3 py-2 text-sm" style={INPUT} defaultValue="public">
          <option value="public">public</option>
          <option value="authorized">authorized</option>
          <option value="deep">deep</option>
        </select>
        <input name="file" type="file" accept=".csv,.tsv,.xlsx,.xlsm" required className="text-sm" />
      </div>
      <label className="flex items-center gap-2 text-sm" style={{ color: "var(--muted)" }}>
        <input name="run_match" type="checkbox" defaultChecked /> Run matcher on new products
      </label>
      <button
        type="submit"
        disabled={busy}
        className="gradient-accent w-fit rounded-lg px-4 py-2 text-sm font-bold"
        style={{ color: "#0d1322", opacity: busy ? 0.6 : 1 }}
      >
        {busy ? "Ingesting…" : "Ingest"}
      </button>

      {error && <p className="text-sm" style={{ color: "#fca5a5" }}>{error}</p>}
      {result && (
        <p className="text-sm" style={{ color: "var(--teal)" }}>
          extracted {result.extracted} · products {result.products} · offers {result.offers} · equivalences {result.equivalences_written}
        </p>
      )}
    </form>
  );
}
