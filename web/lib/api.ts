import type {
  IngestResult,
  LandedCost,
  ProductDetail,
  ProductProtocols,
  ProductsPage,
  ProtocolMaterials,
  Role,
  Stats,
} from "./types";

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { cache: "no-store" });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `Request failed: ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  getStats: () => get<Stats>("/api/stats"),

  listProducts: (opts: {
    q?: string;
    category?: string;
    page?: number;
    role?: Role;
  } = {}) => {
    const p = new URLSearchParams();
    if (opts.q) p.set("q", opts.q);
    if (opts.category) p.set("category", opts.category);
    p.set("page", String(opts.page ?? 1));
    p.set("role", opts.role ?? "ops");
    return get<ProductsPage>(`/api/products?${p.toString()}`);
  },

  getProduct: (id: string, role: Role = "ops") =>
    get<ProductDetail>(`/api/products/${id}?role=${role}`),

  getLandedCost: (id: string, qty: number, role: Role = "ops") =>
    get<LandedCost>(`/api/products/${id}/landed-cost?qty=${qty}&role=${role}`),

  getProductProtocols: (id: string, limit = 50) =>
    get<ProductProtocols>(`/api/products/${id}/protocols?limit=${limit}`),

  getProtocolMaterials: (id: string, limit = 100) =>
    get<ProtocolMaterials>(`/api/protocols/${id}/materials?limit=${limit}`),

  ingest: async (form: FormData): Promise<IngestResult> => {
    const res = await fetch(`${BASE}/api/ingest`, { method: "POST", body: form });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail ?? `Ingest failed: ${res.status}`);
    }
    return res.json() as Promise<IngestResult>;
  },
};
