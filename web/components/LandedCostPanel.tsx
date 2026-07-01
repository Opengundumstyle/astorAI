"use client";

import { useEffect, useState } from "react";
import { LandedCostWaterfall } from "@/components/LandedCostWaterfall";
import { api } from "@/lib/api";
import type { LandedCost } from "@/lib/types";

export function LandedCostPanel({ productId }: { productId: string }) {
  const [qty, setQty] = useState(1);
  const [data, setData] = useState<LandedCost | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    api
      .getLandedCost(productId, qty)
      .then((d) => active && setData(d))
      .catch((e) => active && setError(e.message));
    return () => {
      active = false;
    };
  }, [productId, qty]);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2 text-sm">
        <label style={{ color: "var(--muted)" }}>Qty</label>
        <input
          type="number"
          min={1}
          value={qty}
          onChange={(e) => setQty(Math.max(1, Number(e.target.value)))}
          className="w-20 rounded-md px-2 py-1"
          style={{ background: "var(--panel)", border: "1px solid var(--border)", color: "var(--text)" }}
        />
      </div>
      {error && <p className="text-sm" style={{ color: "#fca5a5" }}>{error}</p>}
      {data && <LandedCostWaterfall data={data} />}
    </div>
  );
}
