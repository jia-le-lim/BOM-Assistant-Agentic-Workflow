"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useApi } from "@/lib/api";
import { can } from "@/lib/session";
import { rememberAsk } from "@/lib/history";
import type {
  ChatResponse, ConfirmPendingResult, PendingChange, PendingChangePage,
  UploadSummary,
} from "@/lib/types";
import { Banner } from "@/components/ui";
import { SuggestionCards } from "@/components/SuggestionCards";

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

/* useSearchParams needs a Suspense boundary to prerender (Next 16). */
export default function ChatPage() {
  return (
    <Suspense fallback={null}>
      <Chat />
    </Suspense>
  );
}

function Chat() {
  const { call, role, user } = useApi();
  const params = useSearchParams();
  const [q, setQ] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [pending, setPending] = useState<PendingChange[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const session = useRef<string | undefined>(undefined);
  const composer = useRef<HTMLTextAreaElement>(null);
  const filePicker = useRef<HTMLInputElement>(null);

  // Mirrors backend REVIEW_ROLES / UPLOAD_ROLES; the backend enforces regardless.
  const canReview = can.review(role);
  const canUpload = can.upload(role);

  const refreshPending = useCallback(async () => {
    try {
      const r = await call<PendingChangePage>("pending-changes?status=pending");
      setPending(r.pending);
    } catch { /* tray is secondary; never block the conversation on it */ }
  }, [call]);

  useEffect(() => { void refreshPending(); }, [refreshPending]);

  // Reopening a recent ask PREFILLS it. Re-sending "set item X max to 3" on a
  // navigation would stage a second proposal the engineer never asked for.
  // Adjusted during render rather than in an effect (the ?q= change and the
  // input value are one update, not two renders).
  const qParam = params.get("q");
  const [seenQParam, setSeenQParam] = useState<string | null>(null);
  if (qParam !== seenQParam) {
    setSeenQParam(qParam);
    if (qParam) setQ(qParam);
  }

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
      rememberAsk(question);
      setQ("");
      if (staged) await refreshPending();
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  /** A card that needs an argument hands the stem over rather than sending it. */
  function fill(stem: string) {
    setQ(stem);
    const el = composer.current;
    if (!el) return;
    el.focus();
    el.setSelectionRange(stem.length, stem.length);
  }

  /** "Attach file" means the one file this product ingests: a BOM extract. */
  async function attach(file: File) {
    setBusy(true); setErr(null); setNote(null);
    try {
      const body = new FormData();
      body.append("file", file);
      body.append("label", file.name.replace(/\.csv$/i, ""));
      body.append("module_filter", "TCB");
      const r = await call<UploadSummary>("upload-bom-file", { method: "POST", body });
      setNote(`Loaded ${r.rows_loaded.toLocaleString()} rows as batch ${r.batch_id}`
              + (r.rows_quarantined ? `, ${r.rows_quarantined} quarantined.` : ".")
              + " Score it from Batches to ask about it.");
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  /** The conversation, as a file you can paste into a review note. */
  function exportChat() {
    const md = turns.map((t) => {
      const src = t.sources.map(sourceLabel).join(", ") || "none";
      return `## ${t.q}\n\n${t.a}\n\n_Sources: ${src}_`
             + (t.staged ? "\n\n_Staged only — not a decision._" : "");
    }).join("\n\n---\n\n");
    const head = `# BOM review conversation\n\n_${user} · ${role} · exported `
               + `${new Date().toISOString().slice(0, 16).replace("T", " ")}_\n\n`;
    const url = URL.createObjectURL(new Blob([head + md], { type: "text/markdown" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "bom-review-conversation.md";
    a.click();
    URL.revokeObjectURL(url);
  }

  function newChat() {
    session.current = undefined;
    setTurns([]); setQ(""); setErr(null); setNote(null);
    composer.current?.focus();
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

  const empty = turns.length === 0;

  return (
    <div className="flex flex-col gap-5 max-w-3xl mx-auto w-full">
      {empty ? (
        <header className="pt-8 pb-1 text-center">
          <span className="orb" aria-hidden />
          <h1 className="text-2xl font-semibold tracking-tight mt-5">
            <span style={{ color: "var(--text-secondary)" }}>Hello, {user}.</span>{" "}
            What are we reviewing?
          </h1>
          <p className="text-sm mt-2 mx-auto max-w-xl" style={{ color: "var(--text-secondary)" }}>
            Explains what the engine decided, with its sources. It never calculates
            Min/Max/ROP itself and never writes to WINGS — tell it a change and it
            records your words as a proposal for you to confirm.
          </p>
        </header>
      ) : (
        <div className="flex items-center justify-between gap-2 pt-1">
          <h2 className="text-sm font-medium">Review conversation</h2>
          <div className="flex gap-2">
            <button type="button" className="btn text-xs" onClick={exportChat}>
              Export chat
            </button>
            <button type="button" className="btn text-xs" onClick={newChat} disabled={busy}>
              New chat
            </button>
          </div>
        </div>
      )}

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

      <form className="composer"
            onSubmit={(e) => { e.preventDefault(); void ask(q); }}>
        <label htmlFor="ask" className="sr-only">Ask about a recommendation</label>
        <textarea
          id="ask"
          ref={composer}
          rows={2}
          className="composer-input"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Ask about an item, or state a change in your own words…"
          onKeyDown={(e) => {
            // Enter sends; Shift+Enter is a newline. A proposal is often two
            // sentences, and losing it to a stray Enter is worse than the extra key.
            if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void ask(q); }
          }}
        />
        <div className="flex items-center justify-between gap-3 px-1 pb-0.5">
          <span className="text-[11px]" style={{ color: "var(--text-muted)" }}>
            {canReview
              ? "Proposals are staged, never applied. Enter to send."
              : `Read-only as ${role}. Enter to send.`}
          </span>
          <div className="flex items-center gap-2">
            {canUpload && (
              <>
                <input ref={filePicker} type="file" accept=".csv,text/csv" className="hidden"
                       onChange={(e) => {
                         const f = e.target.files?.[0];
                         e.target.value = "";        // same file twice must re-fire
                         if (f) void attach(f);
                       }} />
                <button type="button" className="btn text-xs whitespace-nowrap" disabled={busy}
                        title="Upload a BOM extract (.csv) as a new batch"
                        onClick={() => filePicker.current?.click()}>
                  Attach CSV
                </button>
              </>
            )}
            <button className="btn btn-primary text-sm" disabled={busy || !q.trim()}>
              {busy ? "Asking…" : "Ask"}
            </button>
          </div>
        </div>
      </form>

      {empty && (
        <SuggestionCards onSend={(p) => void ask(p)} onFill={fill}
                         canReview={canReview} disabled={busy} />
      )}

      <div className="flex flex-col gap-4">
        {turns.map((t, i) => (
          <div key={i} className="card p-4 turn-in">
            <div className="text-sm font-medium mb-2">{t.q}</div>
            <p className="text-sm whitespace-pre-wrap" style={{ color: "var(--text-secondary)" }}>{t.a}</p>
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
