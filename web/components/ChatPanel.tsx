"use client";

import Link from "next/link";
import { useState } from "react";
import { api } from "@/lib/api";
import type { ChatItem, ChatMessage } from "@/lib/types";

interface Turn {
  role: "user" | "assistant";
  content: string;
  items?: ChatItem[];
  status?: string;
  streaming?: boolean;
  error?: boolean;
}

const EXAMPLES = [
  "I need to run a Western blot for phospho-ERK — what do I need?",
  "What products does a BCA protein assay require?",
  "Find protocols that use Trypsin-EDTA",
];

export function ChatPanel() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);

  function patchLast(fn: (t: Turn) => Turn) {
    setTurns((ts) => {
      const copy = [...ts];
      copy[copy.length - 1] = fn(copy[copy.length - 1]);
      return copy;
    });
  }

  async function send(text: string) {
    const q = text.trim();
    if (!q || busy) return;
    const history: ChatMessage[] = [
      ...turns.filter((t) => !t.error).map((t) => ({ role: t.role, content: t.content })),
      { role: "user", content: q },
    ];
    setTurns((t) => [
      ...t,
      { role: "user", content: q },
      { role: "assistant", content: "", streaming: true },
    ]);
    setInput("");
    setBusy(true);
    try {
      await api.sendChatStream(history, {
        onStatus: (s) => patchLast((x) => ({ ...x, status: s })),
        onDelta: (d) => patchLast((x) => ({ ...x, content: x.content + d, status: undefined })),
        onItems: (items) => patchLast((x) => ({ ...x, items })),
        onError: (detail) =>
          patchLast((x) => ({ ...x, content: x.content || detail, error: true, status: undefined })),
      });
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Something went wrong.";
      patchLast((x) => ({ ...x, content: x.content || msg, error: true, status: undefined }));
    } finally {
      patchLast((x) => ({ ...x, streaming: false, status: undefined }));
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      {turns.length === 0 && (
        <div className="flex flex-col gap-2">
          {EXAMPLES.map((ex) => (
            <button
              key={ex}
              onClick={() => send(ex)}
              className="card p-3 text-left text-sm"
              style={{ color: "var(--muted)" }}
            >
              {ex}
            </button>
          ))}
        </div>
      )}

      <div className="flex flex-col gap-3">
        {turns.map((t, i) => (
          <div key={i} className={t.role === "user" ? "self-end" : "self-start"} style={{ maxWidth: "85%" }}>
            {t.role === "assistant" && !t.content && (t.status || t.streaming) && (
              <div className="px-1 pb-1 text-xs" style={{ color: "var(--muted)" }}>
                {t.status ?? "thinking…"}
              </div>
            )}
            {t.content && (
              <div
                className="rounded-lg px-3 py-2 text-sm"
                style={{
                  background: t.role === "user" ? "rgba(94,234,212,0.12)" : "var(--panel)",
                  border: "1px solid var(--border)",
                  color: t.error ? "#fca5a5" : "inherit",
                  whiteSpace: "pre-wrap",
                }}
              >
                {t.content}
              </div>
            )}
            {t.items && t.items.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-2">
                {t.items.map((it) => (
                  <Link
                    key={`${it.type}-${it.id}`}
                    href={it.type === "product" ? `/products/${it.id}` : `/protocols/${it.id}`}
                    className="rounded px-2 py-1 text-xs"
                    style={{ background: "var(--bg-elev)", border: "1px solid var(--border)", color: "var(--teal)" }}
                  >
                    {it.type === "product" ? "◈" : "⚗"} {it.name}
                  </Link>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>

      <form onSubmit={(e) => { e.preventDefault(); send(input); }} className="flex gap-2">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Describe your experiment or a product you need…"
          className="w-full rounded-lg px-3 py-2 text-sm"
          style={{ background: "var(--panel)", border: "1px solid var(--border)", color: "inherit" }}
        />
        <button
          type="submit"
          disabled={busy}
          className="rounded-lg px-4 py-2 text-sm font-semibold"
          style={{ background: "var(--panel)", border: "1px solid var(--border)", opacity: busy ? 0.5 : 1 }}
        >
          Send
        </button>
      </form>
    </div>
  );
}
