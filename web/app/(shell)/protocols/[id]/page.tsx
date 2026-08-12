import { ProtocolCartPanel } from "@/components/ProtocolCartPanel";
import { api } from "@/lib/api";

export default async function ProtocolPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const protocol = await api.getProtocolMaterials(id);

  return (
    <div className="flex flex-col gap-6">
      <header className="card p-6">
        <div className="text-xs uppercase tracking-wide" style={{ color: "var(--teal)" }}>
          Protocol
        </div>
        <h1 className="mt-1 text-xl font-bold">{protocol.protocol_title}</h1>
        <div className="mt-2 flex flex-wrap items-center gap-3 text-sm" style={{ color: "var(--muted)" }}>
          <span>{protocol.count} catalog products matched</span>
          {protocol.source_uri ? (
            <a
              href={protocol.source_uri}
              target="_blank"
              rel="noreferrer"
              className="underline"
              style={{ color: "var(--teal)" }}
            >
              View on protocols.io ↗
            </a>
          ) : null}
        </div>
      </header>

      <section className="flex flex-col gap-3">
        <h2 className="text-sm font-semibold">Products this protocol needs</h2>
        <ProtocolCartPanel items={protocol.materials} />
      </section>
    </div>
  );
}
