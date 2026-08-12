import { ChatPanel } from "@/components/ChatPanel";

export default function ChatPage() {
  return (
    <div className="flex flex-col gap-6">
      <section className="card p-6">
        <h1 className="text-xl font-bold">Assistant</h1>
        <p className="mt-1 text-sm" style={{ color: "var(--muted)" }}>
          Describe an experiment or a product need — grounded in your protocols and catalog.
        </p>
      </section>
      <ChatPanel />
    </div>
  );
}
