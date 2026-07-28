"use client";

import { useState } from "react";
import { useApi } from "@/lib/api";
import type { ChatResponse } from "@/lib/types";
import { Banner } from "@/components/ui";

const EXAMPLES = [
  "why item 500840315?",
  "history 500840315",
  "top exposure items",
];

interface Turn { q: string; a: string; sources: Record<string, unknown>[] }

export default function ChatPage() {
  const { call } = useApi();
  const [q, setQ] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function ask(question: string) {
    if (!question.trim()) return;
    setBusy(true); setErr(null);
    try {
      const r = await call<ChatResponse>("chat", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question }),
      });
      setTurns((t) => [...t, { q: question, a: r.answer, sources: r.sources }]);
      setQ("");
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  return (
    <div className="flex flex-col gap-5 max-w-3xl">
      <div>
        <h1 className="text-xl font-semibold">Ask about a recommendation</h1>
        <p className="text-sm mt-1" style={{ color: "var(--text-secondary)" }}>
          Retrieval only. This never generates Min/Max/ROP values and never writes to
          WINGS — it reads back what the engine already decided, with sources. When no
          source matches it says so rather than inventing an answer.
        </p>
      </div>

      <Banner kind="info">
        NYRA/RAG is not wired up yet — this is the deterministic stub that keeps the
        read-only tool contract. The LLM slots in behind the same surface.
      </Banner>

      {err && <Banner kind="error">{err}</Banner>}

      <div className="flex flex-wrap gap-2">
        {EXAMPLES.map((e) => (
          <button key={e} className="btn text-xs" onClick={() => ask(e)} disabled={busy}>
            {e}
          </button>
        ))}
      </div>

      <form className="flex gap-2"
            onSubmit={(e) => { e.preventDefault(); ask(q); }}>
        <input className="field flex-1" value={q} onChange={(e) => setQ(e.target.value)}
               placeholder="e.g. why item 500840315?" />
        <button className="btn btn-primary" disabled={busy || !q.trim()}>
          {busy ? "Asking…" : "Ask"}
        </button>
      </form>

      <div className="flex flex-col gap-4">
        {turns.map((t, i) => (
          <div key={i} className="card p-4">
            <div className="text-sm font-medium mb-2">{t.q}</div>
            <p className="text-sm" style={{ color: "var(--text-secondary)" }}>{t.a}</p>
            {t.sources.length > 0 && (
              <div className="mt-3 pt-3 text-xs" style={{ borderTop: "1px solid var(--gridline)" }}>
                <span style={{ color: "var(--text-muted)" }}>Sources: </span>
                {t.sources.map((s, j) => (
                  <code key={j} className="mr-2">{JSON.stringify(s)}</code>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
