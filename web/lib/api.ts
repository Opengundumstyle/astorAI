import type {
  ChatItem,
  ChatMessage,
  ChatResponse,
  ChatStreamHandlers,
  IngestResult,
  LandedCost,
  ProductDetail,
  ProductProtocols,
  ProductsPage,
  ProtocolMaterials,
  ProtocolsListPage,
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

  listProtocols: (opts: { q?: string; page?: number } = {}) => {
    const p = new URLSearchParams();
    if (opts.q) p.set("q", opts.q);
    p.set("page", String(opts.page ?? 1));
    return get<ProtocolsListPage>(`/api/protocols?${p.toString()}`);
  },

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

  sendChat: async (messages: ChatMessage[]): Promise<ChatResponse> => {
    const res = await fetch(`${BASE}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages }),
    });
    if (!res.ok) {
      const b = await res.json().catch(() => ({}));
      throw new Error(b.detail ?? `Chat failed: ${res.status}`);
    }
    return res.json() as Promise<ChatResponse>;
  },

  sendChatStream: async (messages: ChatMessage[], h: ChatStreamHandlers): Promise<void> => {
    const res = await fetch(`${BASE}/api/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages }),
    });
    if (!res.ok || !res.body) {
      const b = await res.json().catch(() => ({}));
      h.onError?.(b.detail ?? `Chat failed: ${res.status}`);
      return;
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx: number;
      while ((idx = buffer.indexOf("\n\n")) >= 0) {
        const chunk = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        const line = chunk.split("\n").find((l) => l.startsWith("data:"));
        if (!line) continue;
        let ev: { type: string; text?: string; items?: ChatItem[]; detail?: string };
        try {
          ev = JSON.parse(line.slice(5).trim());
        } catch {
          continue;
        }
        if (ev.type === "delta" && ev.text != null) h.onDelta(ev.text);
        else if (ev.type === "status" && ev.text != null) h.onStatus?.(ev.text);
        else if (ev.type === "items" && ev.items != null) h.onItems?.(ev.items);
        else if (ev.type === "error") h.onError?.(ev.detail ?? "Something went wrong.");
      }
    }
  },
};
