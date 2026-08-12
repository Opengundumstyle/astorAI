export type Role = "ops" | "buyer";

export interface Stats {
  products: number;
  offers: number;
  equivalences: { exact: number; substitute: number; total: number };
  suppliers: number;
  avg_savings: number;
}

export interface ProductSummary {
  id: string;
  astor_sku: string;
  name: string;
  category: string;
  brand?: string | null;
  mpn?: string | null;
  region?: string | null;
  offer_count: number;
  best_landed: number | null;
}

export interface Offer {
  supplier: string;
  region: string;
  supplier_sku: string;
  pack_size: string | null;
  cost: number;
  currency: string;
  stock: number | null;
  lead_time_days: number | null;
}

export interface Equivalent {
  id: string;
  astor_sku: string;
  name: string;
  brand?: string | null;
  confidence: number;
  kind: "exact" | "substitute";
}

export interface ProductDetail {
  id: string;
  astor_sku: string;
  name: string;
  category: string;
  brand?: string | null;
  mpn?: string | null;
  specs: Record<string, unknown>;
  offers?: Offer[];
  equivalents: Equivalent[];
}

export interface LandedCost {
  currency: string;
  qty: number;
  ex_works?: number;
  tariff?: number;
  duty_rate?: number;
  freight?: number;
  margin?: number;
  unit_price: number;
  line_total: number;
}

export interface IngestResult {
  extracted: number;
  products: number;
  offers: number;
  equivalences_written: number;
}

export interface ProductsPage {
  items: ProductSummary[];
  total: number;
  page: number;
  page_size: number;
}

// Browse list of protocols.
export interface ProtocolSummary {
  id: string;
  title: string;
  source: string;
  rank_score: number;
  product_count: number;
}

export interface ProtocolsListPage {
  items: ProtocolSummary[];
  total: number;
  page: number;
  page_size: number;
}

// A protocol that uses a product (reverse) — rendered on the product page.
export interface ProtocolLink {
  protocol_id: string;
  title: string;
  source_uri: string;
  material_name: string;
  confidence: number;
  kind: "exact" | "substitute";
}

export interface ProductProtocols {
  product_id: string;
  product_name: string;
  count: number;
  protocols: ProtocolLink[];
}

// A product a protocol needs (forward) — a line in the protocol's cart.
export interface MaterialLink {
  material_name: string;
  product_id: string;
  product_name: string;
  brand?: string | null;
  confidence: number;
  kind: "exact" | "substitute";
}

export interface ProtocolMaterials {
  protocol_id: string;
  protocol_title: string;
  source_uri: string;
  count: number;
  materials: MaterialLink[];
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface ChatItem {
  type: "product" | "protocol";
  id: string;
  name: string;
}

export interface ChatResponse {
  reply: string;
  items: ChatItem[];
}

export interface ChatStreamHandlers {
  onStatus?: (text: string) => void;
  onDelta: (text: string) => void;
  onItems?: (items: ChatItem[]) => void;
  onError?: (detail: string) => void;
}
