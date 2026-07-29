"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useApi } from "@/lib/api";
import { can } from "@/lib/session";
import type {
  ChatResponse, ConfirmPendingResult, PendingChange, PendingChangePage,
} from "@/lib/types";
import { Banner } from "@/components/ui";

const EXAMPLES = [
  "why item 500840315?",
  "history 500840315",
  "top exposure items",
  "set item 500840315 max to 3",
];

interface Turn {
  q: string;
  a: string;
  sources: Record<string, unknown>[];
  staged: boolean;
}

/** Non-authoritative sources are rendered as such — see memory.py. */
function sourceLabel(s: Record<string, unknown>): string {
  const t = String(s.type ?? "source");
  if (t === "mem0") return `recalled context (not a record) ×${s.count ?? 0}`;
  if (t === "pending_change") return `staged proposal #${s.pending_id}`;
  if (s.item_id) return `${t} · ${s.item_id}`;
  return t;
}

export default function ChatPage() {
  const { call, role } = useApi();
  const [q, setQ] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [pending, setPending] = useState<PendingChange[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const session = useRef<string | undefined>(undefined);

  // Mirrors backend REVIEW_ROLES; the backend enforces it regardless.
  const canReview = can.review(role);

  const refreshPending = useCallback(async () => {
    try {
      const r = await call<PendingChangePage>("pending-changes?status=pending");
      setPending(r.pending);
    } catch { /* tray is secondary; never block the conversation on it */ }
  }, [call]);

  useEffect(() => { void refreshPending(); }, [refreshPending]);

  async function ask(question: string) {
    if (!question.trim()) return;
    setBusy(true); setErr(null); setNote(null);
    try {
      const r = await call<ChatResponse>("chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, session_id: session.current }),
      });
      session.current = r.session_id;
      const staged = r.sources.some((s) => s.type === "pending_change");
      setTurns((t) => [...t, { q: question, a: r.answer, sources: r.sources, staged }]);
      setQ("");
      if (staged) await refreshPending();
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  async function confirm(p: PendingChange) {
    setBusy(true); setErr(null);
    try {
      const r = await call<ConfirmPendingResult>(
        `review/${p.item_id}/confirm-pending`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            pending_id: p.pending_id,
            decision: "override",
            final_max: p.proposed_max,
            final_rop: p.proposed_rop ?? 0,
            final_min: p.proposed_min ?? 0,
          }),
        });
      setNote(
        r.requires_senior_approval
          ? `Recorded as review #${r.review_id}. It needs senior approval before it can be exported.`
          : `Recorded as review #${r.review_id}.`);
      await refreshPending();
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  async function discard(p: PendingChange) {
    setBusy(true); setErr(null);
    try {
      await call(`review/${p.item_id}/discard-pending?pending_id=${p.pending_id}`,
                 { method: "POST" });
      await refreshPending();
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  return (
    <div className="flex flex-col gap-5 max-w-3xl">
      <div>
        <h1 className="text-xl font-semibold">Ask about a recommendation</h1>
        <p className="text-sm mt-1" style={{ color: "var(--text-secondary)" }}>
          Explains what the engine decided, with sources. It never calculates
          Min/Max/ROP itself, and it never writes to WINGS. Tell it a change you
          want and it records your words as a proposal — you confirm it below,
          and it then follows the normal approval path.
        </p>
      </div>

      {err && <Banner kind="error">{err}</Banner>}
      {note && <Banner kind="info">{note}</Banner>}

      {pending.length > 0 && (
        <div className="card p-4">
          <div className="text-sm font-medium mb-1">
            Staged changes · {pending.length}
          </div>
          <p className="text-xs mb-3" style={{ color: "var(--text-muted)" }}>
            Not applied. Nothing here reaches a WINGS export until you confirm it.
          </p>
          <div className="flex flex-col gap-3">
            {pending.map((p) => (
              <div key={p.pending_id} className="flex flex-col gap-2 p-3"
                   style={{ border: "1px solid var(--gridline)", borderRadius: 6 }}>
                <div className="text-sm">
                  <strong>{p.item_id}</strong>
                  {p.proposed_max !== null && <> · max → {p.proposed_max}</>}
                  {p.proposed_rop !== null && <> · rop → {p.proposed_rop}</>}
                  {p.proposed_min !== null && <> · min → {p.proposed_min}</>}
                </div>
                <div className="text-xs" style={{ color: "var(--text-secondary)" }}>
                  “{p.source_utterance}”
                </div>
                <div className="text-xs" style={{ color: "var(--text-muted)" }}>
                  staged by {p.created_by} · parsed by {p.parsed_by || "unknown"} · {p.created_at}
                </div>
                <div className="flex gap-2">
                  <button className="btn btn-primary text-xs" disabled={busy || !canReview}
                          onClick={() => confirm(p)}>
                    Confirm
                  </button>
                  <button className="btn text-xs" disabled={busy || !canReview}
                          onClick={() => discard(p)}>
                    Discard
                  </button>
                  <a className="btn text-xs"
                     href={`/batches/${p.batch_id}/items/${p.item_id}`}>
                    Open in review
                  </a>
                </div>
                {!canReview && (
                  <div className="text-xs" style={{ color: "var(--text-muted)" }}>
                    Your role cannot confirm changes.
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        {EXAMPLES.map((e) => (
          <button key={e} className="btn text-xs" onClick={() => ask(e)} disabled={busy}>
            {e}
          </button>
        ))}
      </div>

      <form className="flex gap-2"
            onSubmit={(e) => { e.preventDefault(); void ask(q); }}>
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
            {t.staged && (
              <p className="text-xs mt-2" style={{ color: "var(--text-muted)" }}>
                Staged only — confirm it above to make it a decision.
              </p>
            )}
            {t.sources.length > 0 && (
              <div className="mt-3 pt-3 text-xs" style={{ borderTop: "1px solid var(--gridline)" }}>
                <span style={{ color: "var(--text-muted)" }}>Sources: </span>
                {t.sources.map((s, j) => (
                  <code key={j} className="mr-2">{sourceLabel(s)}</code>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
