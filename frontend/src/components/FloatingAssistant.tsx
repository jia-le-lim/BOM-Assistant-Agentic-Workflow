"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useApi } from "@/lib/api";
import { can } from "@/lib/session";
import { pageIdentity, useAssistantContext } from "@/lib/assistant-context";
import { notifyChatHistoryChanged } from "@/lib/history";
import type { ChatResponse, PendingChangePage, StagedAction } from "@/lib/types";
import { ChatAnswer } from "./ChatAnswer";
import { ActionCard } from "./ActionCard";

interface Turn {
  id: number;
  question: string;
  answer: string;
  sources: Record<string, unknown>[];
  stagedAction: StagedAction | null;
  page: string;
  notice: string;
}

function AgentIcon() {
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor"
         strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M12 3v3M8 3h8M5 9h14a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2Z" />
      <path d="M8 14h.01M16 14h.01M9 17h6" />
    </svg>
  );
}

function sourceHref(source: Record<string, unknown>): string | null {
  if (typeof source.path === "string" && /^\/(?:config(?:\/dormant)?|chat|batches\/[1-9]\d*(?:\/items\/[\w.%~-]+)?)?$/.test(source.path)) return source.path;
  if (!source.batch_id) return null;
  return source.item_id
    ? `/batches/${source.batch_id}/items/${source.item_id}`
    : `/batches/${source.batch_id}`;
}

function sourceLabel(source: Record<string, unknown>): string {
  const label = String(source.type ?? "source").replaceAll("_", " ");
  return source.item_id ? `${label} · ${source.item_id}` : label;
}

export function FloatingAssistant() {
  const { call, role } = useApi();
  const path = usePathname();
  const { capture, applyActions } = useAssistantContext();
  const { title, batch_id: batchId, item_id: itemId } = pageIdentity(path);
  const [attention, setAttention] = useState({ path: "", section: "", selection: "" });
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [sessionId, setSessionId] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const messages = useRef<HTMLDivElement>(null);
  const container = useRef<HTMLDetailsElement>(null);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);

  useEffect(() => {
    let frame = 0;
    const refresh = () => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        const page = capture(false);
        setAttention({ path: page.path, section: page.active_section, selection: page.selected_text });
      });
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && container.current?.open) {
        container.current.open = false;
        container.current.querySelector("summary")?.focus();
      }
    };
    refresh();
    const root = document.querySelector("main.shell-content");
    const observer = new MutationObserver(refresh);
    if (root) observer.observe(root, { childList: true, subtree: true, characterData: true });
    window.addEventListener("scroll", refresh, true);
    window.addEventListener("resize", refresh);
    document.addEventListener("selectionchange", refresh);
    document.addEventListener("focusin", refresh);
    document.addEventListener("keydown", onKey);
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      window.removeEventListener("scroll", refresh, true);
      window.removeEventListener("resize", refresh);
      document.removeEventListener("selectionchange", refresh);
      document.removeEventListener("focusin", refresh);
      document.removeEventListener("keydown", onKey);
    };
  }, [capture, path]);

  useEffect(() => {
    messages.current?.scrollTo({ top: messages.current.scrollHeight, behavior: "auto" });
  }, [turns, busy]);

  async function ask() {
    const clean = question.trim();
    if (!clean || busy) return;

    setQuestion("");
    setBusy(true);
    setError(undefined);
    const page = capture();
    try {
      const result = await call<ChatResponse>("chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: clean,
          batch_id: page.batch_id,
          session_id: sessionId,
          page_context: page,
        }),
      });
      if (!alive.current) return;
      const notice = applyActions(result.page_actions ?? [], page);
      let action = result.staged_action;
      const pendingSource = result.sources.find((source) => source.type === "pending_change");
      if (!action && pendingSource && can.review(role)) {
        try {
          const pending = await call<PendingChangePage>(`pending-changes?batch_id=${result.batch_id}&status=pending`);
          const proposal = pending.pending.find((p) => p.pending_id === pendingSource.pending_id);
          if (proposal) action = { ...proposal, kind: "confirm_pending", executed: false };
        } catch { /* full chat can still load the proposal tray */ }
      }
      if (!alive.current) return;
      setSessionId(result.session_id);
      setTurns((current) => [...current, {
        id: result.turn_id,
        question: clean,
        answer: result.answer,
        sources: result.sources,
        stagedAction: action,
        page: page.title,
        notice,
      }]);
      notifyChatHistoryChanged();
    } catch (cause) {
      setQuestion(clean);
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function reviewAction(action: StagedAction, discard = false) {
    if (busy || action.pending_id === null || !can.review(role)) return;
    setBusy(true); setError(undefined);
    try {
      const item = encodeURIComponent(action.item_id);
      if (discard) {
        await call(`review/${item}/discard-pending?pending_id=${action.pending_id}`, { method: "POST" });
      } else {
        await call(`review/${item}/confirm-pending`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ pending_id: action.pending_id, decision: "override" }),
        });
      }
      setTurns((current) => current.map((turn) => turn.stagedAction?.pending_id === action.pending_id
        ? { ...turn, stagedAction: null, notice: discard ? "Proposal discarded." : "Review recorded. An override still needs senior approval." } : turn));
      notifyChatHistoryChanged();
    } catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  }

  const section = attention.path === path ? attention.section : "";
  const isSettings = path.startsWith("/config");
  const suggestion = path === "/config/dormant" ? "Help me fill dormant rules for a list of parts. What should I paste?"
    : isSettings ? `Explain ${section || "the settings on this page"}`
    : itemId ? "Why was this item flagged?" : batchId ? "What should I review first?" : "What can I do on this page?";

  const fullChat = sessionId
    ? `/chat?session=${encodeURIComponent(sessionId)}`
    : `/chat?q=${encodeURIComponent(itemId ? `Tell me about item ${itemId} in batch ${batchId}` : batchId ? `Summarise batch ${batchId}` : `Help me with ${title}`)}`;

  if (path === "/chat" || path.startsWith("/chat/")) return null;

  return (
    <details ref={container} className="assistant-float">
      <summary title="Open or close NYRA assistant">
        <AgentIcon />
        <span>Ask NYRA</span>
      </summary>

      <section className="assistant-panel" aria-label="NYRA review assistant">
        <header className="assistant-head">
          <span className="assistant-mark"><AgentIcon /></span>
          <div>
            <strong>NYRA</strong>
            <span title={title}>{title}</span>
          </div>
          <span className="assistant-online"><i /> Grounded</span>
        </header>

        <div className="assistant-context" aria-label="Current page context">
          <span>Viewing: <strong>{section || title}</strong></span>
          {attention.path === path && attention.selection && <span title={attention.selection}>Using selected text</span>}
        </div>

        <div ref={messages} className="assistant-messages" aria-live="polite"
             aria-busy={busy}>
          {turns.length === 0 && (
            <div className="assistant-empty">
              <span className="assistant-mark"><AgentIcon /></span>
              <strong>How can I help on this page?</strong>
              <p>
                I can see this page, its visible sections, selected text and form fields.
                {isSettings && " Paste a list or describe your changes, and I’ll fill an editable draft."}
              </p>
              <button type="button" onClick={() => setQuestion(suggestion)}>{suggestion}</button>
            </div>
          )}

          {turns.map((turn) => (
            <article className="assistant-turn" key={turn.id}>
              <p className="assistant-question">{turn.question}</p>
              <span className="assistant-turn-context">Asked on {turn.page}</span>
              <div className="assistant-answer">
                <ChatAnswer>{turn.answer}</ChatAnswer>
                {turn.sources.length > 0 && (
                  <div className="assistant-sources" aria-label="Answer sources">
                    {turn.sources.slice(0, 4).map((source, index) => {
                      const href = sourceHref(source);
                      const label = sourceLabel(source);
                      return href
                        ? <Link href={href} key={index}>{label}</Link>
                        : <span key={index}>{label}</span>;
                    })}
                  </div>
                )}
                {turn.notice && <div className="assistant-staged" role="status">{turn.notice}</div>}
                {turn.stagedAction && can.review(role) && <ActionCard action={turn.stagedAction}
                  busy={busy} onConfirm={(action) => void reviewAction(action)}
                  onDiscard={(action) => void reviewAction(action, true)} />}
              </div>
            </article>
          ))}

          {busy && (
            <div className="assistant-thinking" role="status">
              <span className="typing-dots" aria-hidden><i /><i /><i /></span>
              Reading page context and recorded data…
            </div>
          )}
          {error && <p className="assistant-error" role="alert">{error}</p>}
        </div>

        <form className="assistant-composer" onSubmit={(event) => {
          event.preventDefault();
          void ask();
        }}>
          <label className="sr-only" htmlFor="assistant-question">Ask NYRA</label>
          <textarea id="assistant-question" rows={3} value={question} maxLength={20000}
                    placeholder={isSettings ? "Paste parts, rules or settings to fill…" : "Ask about what you’re viewing…"}
                    onChange={(event) => setQuestion(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                        event.preventDefault();
                        void ask();
                      }
                    }} />
          <div>
            <Link href={fullChat}>Open full chat</Link>
            <button className="btn btn-primary" disabled={busy || !question.trim()}>
              {busy ? "Working…" : "Send"}
            </button>
          </div>
        </form>
      </section>
    </details>
  );
}
