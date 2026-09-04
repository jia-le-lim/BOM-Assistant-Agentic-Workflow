"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useApi } from "@/lib/api";
import { notifyChatHistoryChanged } from "@/lib/history";
import type { ChatResponse, StagedAction } from "@/lib/types";
import { ChatAnswer } from "./ChatAnswer";

interface Turn {
  id: number;
  question: string;
  answer: string;
  sources: Record<string, unknown>[];
  stagedAction: StagedAction | null;
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
  if (!source.batch_id) return null;
  return source.item_id
    ? `/batches/${source.batch_id}/items/${source.item_id}`
    : `/batches/${source.batch_id}`;
}

function sourceLabel(source: Record<string, unknown>): string {
  const label = String(source.type ?? "source").replaceAll("_", " ");
  return source.item_id ? `${label} · ${source.item_id}` : label;
}

export function FloatingAssistant({ batchId, itemId }: {
  batchId: number;
  itemId?: string;
}) {
  const { call } = useApi();
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [sessionId, setSessionId] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const messages = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messages.current?.scrollTo({ top: messages.current.scrollHeight, behavior: "auto" });
  }, [turns, busy]);

  async function ask() {
    const clean = question.trim();
    if (!clean || busy) return;

    setQuestion("");
    setBusy(true);
    setError(undefined);
    try {
      const scopedQuestion = itemId
        ? `For item ${itemId} in batch ${batchId}: ${clean}`
        : clean;
      const result = await call<ChatResponse>("chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: scopedQuestion,
          batch_id: batchId,
          session_id: sessionId,
        }),
      });
      setSessionId(result.session_id);
      setTurns((current) => [...current, {
        id: result.turn_id,
        question: clean,
        answer: result.answer,
        sources: result.sources,
        stagedAction: result.staged_action,
      }]);
      notifyChatHistoryChanged();
    } catch (cause) {
      setQuestion(clean);
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const fullChat = sessionId
    ? `/chat?session=${encodeURIComponent(sessionId)}`
    : `/chat?q=${encodeURIComponent(itemId ? `Tell me about item ${itemId}` : `Summarise batch ${batchId}`)}`;

  return (
    <details className="assistant-float">
      <summary title="Open or close NYRA assistant">
        <AgentIcon />
        <span>Ask NYRA</span>
      </summary>

      <section className="assistant-panel" aria-label="NYRA review assistant">
        <header className="assistant-head">
          <span className="assistant-mark"><AgentIcon /></span>
          <div>
            <strong>NYRA</strong>
            <span>Batch #{batchId}{itemId ? ` · Item ${itemId}` : ""}</span>
          </div>
          <span className="assistant-online"><i /> Grounded</span>
        </header>

        <div ref={messages} className="assistant-messages" aria-live="polite"
             aria-busy={busy}>
          {turns.length === 0 && (
            <div className="assistant-empty">
              <span className="assistant-mark"><AgentIcon /></span>
              <strong>How can I help with this review?</strong>
              <p>
                {itemId
                  ? `I already have item ${itemId} and batch ${batchId} in context.`
                  : `I already have batch ${batchId} in context.`}
              </p>
              <button type="button" onClick={() => setQuestion(itemId
                ? "Why was this item flagged?"
                : "What should I review first?")}>Use a suggested question</button>
            </div>
          )}

          {turns.map((turn) => (
            <article className="assistant-turn" key={turn.id}>
              <p className="assistant-question">{turn.question}</p>
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
                {turn.stagedAction && (
                  <div className="assistant-staged">
                    A change was staged, not applied. Open the full conversation to review it.
                  </div>
                )}
              </div>
            </article>
          ))}

          {busy && (
            <div className="assistant-thinking" role="status">
              <span className="typing-dots" aria-hidden><i /><i /><i /></span>
              Checking recorded BOM data…
            </div>
          )}
          {error && <p className="assistant-error" role="alert">{error}</p>}
        </div>

        <form className="assistant-composer" onSubmit={(event) => {
          event.preventDefault();
          void ask();
        }}>
          <label className="sr-only" htmlFor="assistant-question">Ask NYRA</label>
          <textarea id="assistant-question" rows={2} value={question}
                    placeholder={itemId ? "Ask about this spare part…" : "Ask about this batch…"}
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
