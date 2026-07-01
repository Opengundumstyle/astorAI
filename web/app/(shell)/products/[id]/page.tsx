import { EquivalentsPanel } from "@/components/EquivalentsPanel";
import { LandedCostPanel } from "@/components/LandedCostPanel";
import { OffersTable } from "@/components/OffersTable";
import { api } from "@/lib/api";

export default async function ProductPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const product = await api.getProduct(id);

  return (
    <div className="flex flex-col gap-6">
      <header className="card p-6">
        <div className="text-xs uppercase tracking-wide" style={{ color: "var(--teal)" }}>
          {product.astor_sku}
        </div>
        <h1 className="mt-1 text-xl font-bold">{product.name}</h1>
        <div className="mt-1 text-sm" style={{ color: "var(--muted)" }}>
          {product.category}
          {product.brand ? ` · ${product.brand}` : ""}
          {product.mpn ? ` · ${product.mpn}` : ""}
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          {Object.entries(product.specs).map(([k, v]) => (
            <span key={k} className="rounded px-2 py-1 text-xs" style={{ background: "var(--panel)", border: "1px solid var(--border)" }}>
              {k}: {String(v)}
            </span>
          ))}
        </div>
      </header>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <section className="flex flex-col gap-3">
          <h2 className="text-sm font-semibold">Landed cost</h2>
          <LandedCostPanel productId={product.id} />
        </section>
        <section className="flex flex-col gap-3">
          <h2 className="text-sm font-semibold">Equivalents</h2>
          <EquivalentsPanel items={product.equivalents} />
        </section>
      </div>

      {product.offers && (
        <section className="flex flex-col gap-3">
          <h2 className="text-sm font-semibold">Supplier offers</h2>
          <OffersTable offers={product.offers} />
        </section>
      )}
    </div>
  );
}
