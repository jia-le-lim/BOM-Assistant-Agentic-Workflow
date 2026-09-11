"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { ApiError, useApi } from "@/lib/api";
import { useAssistantContext } from "@/lib/assistant-context";
import { can } from "@/lib/session";
import { notifyChatHistoryChanged } from "@/lib/history";
import type {
  ChatHistoryTurn, ChatResponse, ChatSession, ChatStreamComplete, ChatStreamEvent,
  ConfirmPendingResult, NextStepPrediction, PendingChange, PendingChangePage,
  StagedAction, UploadSummary,
} from "@/lib/types";
import { Banner } from "@/components/ui";
import { ChatAnswer } from "@/components/ChatAnswer";
import { AgentActivity, AgentToolCalls } from "@/components/AgentActivity";
import type { AgentTrace, AgentTraceStep } from "@/components/AgentActivity";
import { NextStepSuggestions } from "@/components/NextStepSuggestions";
import { SuggestionCards } from "@/components/SuggestionCards";
import { ActionCard } from "@/components/ActionCard";

interface Turn {
  q: string;
  a: string;
  sources: Record<string, unknown>[];
  staged: boolean;
  trace: AgentTrace;
  nextSteps?: NextStepPrediction;
  /** Staged by the action branch; nothing is recorded until it is pressed. */
  stagedAction?: StagedAction | null;
  error?: string;
}

/** Non-authoritative sources are rendered as such — see memory.py. */
function sourceLabel(s: Record<string, unknown>): string {
  const t = String(s.type ?? "source");
  if (t === "mem0") return `recalled context (not a record) ×${s.count ?? 0}`;
  if (t === "pending_change") return `staged proposal #${s.pending_id}`;
  const label = t.replaceAll("_", " ");
  if (s.item_id) return `${label} · ${s.item_id}`;
  return label;
}

function sourceHref(s: Record<string, unknown>): string | null {
  if (!s.batch_id) return null;
  if (s.item_id) return `/batches/${s.batch_id}/items/${s.item_id}`;
  return `/batches/${s.batch_id}`;
}

function formatTimestamp(value: string): string {
  return value.replace("T", " ").replace(/:\d{2}(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)?$/, "");
}

function toolLabel(name: string) {
  return name.replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase());
}

function upsertTraceStep(trace: AgentTrace, step: AgentTraceStep): AgentTrace {
  const exists = trace.steps.some((candidate) => candidate.id === step.id);
  return {
    ...trace,
    steps: exists
      ? trace.steps.map((candidate) => candidate.id === step.id
        ? { ...candidate, ...step }
        : candidate)
      : [...trace.steps, step],
  };
}

function applyTraceEvent(trace: AgentTrace, event: ChatStreamEvent): AgentTrace {
  if (event.type === "request") {
    return upsertTraceStep({
      ...trace,
      provider: event.provider,
      model: event.model,
      batchId: event.batch_id,
    }, {
      id: "request",
      label: "Request accepted",
      detail: event.batch_id ? `Using scored batch ${event.batch_id}` : "No scored batch selected",
      result: `${event.provider} · ${event.model}`,
      status: "done",
    });
  }
  if (event.type === "classify") {
    return upsertTraceStep({ ...trace, provider: event.provider, model: event.model }, {
      id: "classify",
      label: "Routing the question",
      detail: `${event.provider} · ${event.model}`,
      result: `${event.intent} branch`,
      status: "done",
    });
  }
  if (event.type === "intent_downgraded") {
    return upsertTraceStep(trace, {
      id: "classify-downgrade",
      label: "Read-only route",
      detail: event.reason,
      result: `${event.from} not available`,
      status: "warning",
    });
  }
  if (event.type === "model_start") {
    return upsertTraceStep({ ...trace, provider: event.provider, model: event.model }, {
      id: `model-${event.attempt}`,
      label: event.phase,
      detail: `${event.provider} · ${event.model}`,
      status: "running",
    });
  }
  if (event.type === "model_complete") {
    const current = trace.steps.find((step) => step.id === `model-${event.attempt}`);
    return upsertTraceStep(trace, {
      id: `model-${event.attempt}`,
      label: current?.label ?? `Model call ${event.attempt}`,
      detail: current?.detail,
      result: event.summary,
      status: "done",
    });
  }
  if (event.type === "tool_start") {
    return upsertTraceStep(trace, {
      id: `tool-${event.sequence}`,
      label: `Query ${toolLabel(event.name)}`,
      detail: JSON.stringify(event.args),
      status: "running",
      technical: true,
      kind: "tool",
      toolName: event.name,
      toolArgs: event.args,
    });
  }
  if (event.type === "tool_result") {
    const current = trace.steps.find((step) => step.id === `tool-${event.sequence}`);
    return upsertTraceStep(trace, {
      id: `tool-${event.sequence}`,
      label: current?.label ?? `Query ${toolLabel(event.name)}`,
      detail: current?.detail,
      result: event.summary,
      status: event.status === "ok" ? "done" : event.status === "empty" ? "warning" : "error",
      technical: true,
      kind: "tool",
      toolName: current?.toolName ?? event.name,
      toolArgs: current?.toolArgs,
    });
  }
  if (event.type === "fallback") {
    return upsertTraceStep({ ...trace, fallback: true }, {
      id: "fallback",
      label: "Grounded fallback",
      detail: event.reason,
      status: "warning",
    });
  }
  if (event.type === "agent_complete") {
    return upsertTraceStep({ ...trace, fallback: event.fallback }, {
      id: "grounding",
      label: "Grounding check",
      result: `${event.source_count} authoritative source${event.source_count === 1 ? "" : "s"} · `
            + `${event.tool_count} tool call${event.tool_count === 1 ? "" : "s"}`,
      status: event.fallback ? "warning" : "done",
    });
  }
  if (event.type === "answer_start") {
    return upsertTraceStep(trace, {
      id: "answer",
      label: "Streaming grounded answer",
      status: "running",
    });
  }
  if (event.type === "prediction_start") {
    return upsertTraceStep(trace, {
      id: "prediction",
      label: "Predicting likely next steps",
      detail: `${event.provider} · ${event.model}`,
      status: "running",
    });
  }
  if (event.type === "prediction_complete") {
    return upsertTraceStep(trace, {
      id: "prediction",
      label: "Predicted likely next steps",
      result: event.suggestions.length
        ? `${event.suggestions.length} suggestions generated from the first request`
        : "No grounded follow-up suggestion was generated",
      status: event.suggestions.length ? "done" : "warning",
    });
  }
  if (event.type === "prediction_error") {
    return upsertTraceStep(trace, {
      id: "prediction",
      label: "Next-step prediction unavailable",
      detail: event.message,
      status: "warning",
      technical: true,
    });
  }
  if (event.type === "complete") {
    let completedTrace: AgentTrace = {
      ...trace,
      provider: event.provider,
      model: event.model,
    };
    event.tool_calls.forEach((tool, index) => {
      const id = `tool-${index + 1}`;
      const current = completedTrace.steps.find((step) => step.id === id);
      completedTrace = upsertTraceStep(completedTrace, {
        id,
        label: current?.label ?? `Query ${toolLabel(tool.name)}`,
        detail: current?.detail ?? JSON.stringify(tool.args),
        result: current?.result ?? (tool.ok ? "Tool call completed." : "Tool call failed."),
        status: current?.status ?? (tool.ok ? "done" : "error"),
        technical: true,
        kind: "tool",
        toolName: tool.name,
        toolArgs: tool.args,
      });
    });
    return upsertTraceStep(completedTrace, {
      id: "answer",
      label: "Answer delivered",
      status: "done",
    });
  }
  if (event.type === "error") {
    return upsertTraceStep(trace, {
      id: "error",
      label: `Failed during ${event.stage}`,
      detail: `${event.error_type}: ${event.message}`,
      status: "error",
      technical: true,
    });
  }
  return trace;
}

function restoreTurn(saved: ChatHistoryTurn): Turn {
  const steps: AgentTraceStep[] = [{
    id: "request",
    label: "Saved request",
    detail: saved.batch_id ? `Used scored batch ${saved.batch_id}` : "No scored batch selected",
    result: [saved.provider, saved.model].filter(Boolean).join(" · ") || undefined,
    status: "done",
  }];
  saved.tool_calls.forEach((tool, index) => {
    steps.push({
      id: `tool-${index + 1}`,
      label: `Query ${toolLabel(tool.name)}`,
      detail: JSON.stringify(tool.args ?? {}),
      result: tool.ok ? "Recorded tool call completed." : "Recorded tool call failed.",
      status: tool.ok ? "done" : "error",
      technical: true,
      kind: "tool",
      toolName: tool.name,
      toolArgs: tool.args ?? {},
    });
  });
  steps.push({ id: "answer", label: "Answer delivered", status: "done" });
  return {
    q: saved.question,
    a: saved.answer ?? "",
    sources: [],
    staged: false,
    trace: {
      query: saved.question,
      provider: saved.provider ?? undefined,
      model: saved.model ?? undefined,
      batchId: saved.batch_id,
      steps,
    },
  };
}

function SendIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="m22 2-7 20-4-9-9-4Z" /><path d="M22 2 11 13" />
    </svg>
  );
}

function AttachIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="1.8" strokeLinecap="round" aria-hidden>
      <path d="m20.5 11.5-8.9 8.9a6 6 0 0 1-8.5-8.5l9.6-9.6a4 4 0 0 1 5.7 5.7l-9.7 9.7a2 2 0 0 1-2.8-2.8l8.9-8.9" />
    </svg>
  );
}

function SourceIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v16H6.5A2.5 2.5 0 0 0 4 21.5Z" />
      <path d="M4 5.5v16M8 7h8M8 11h6" />
    </svg>
  );
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
  const { call, stream, role, user } = useApi();
  const { capture, applyActions } = useAssistantContext();
  const params = useSearchParams();
  const [q, setQ] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [pending, setPending] = useState<PendingChange[]>([]);
  const [busy, setBusy] = useState(false);
  const [sending, setSending] = useState<string | null>(null);
  const [activeTrace, setActiveTrace] = useState<AgentTrace | null>(null);
  const [streamedAnswer, setStreamedAnswer] = useState("");
  const [activePrediction, setActivePrediction] = useState<NextStepPrediction | null>(null);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const session = useRef<string | undefined>(undefined);
  const conversationVersion = useRef(0);
  const composer = useRef<HTMLTextAreaElement>(null);
  const filePicker = useRef<HTMLInputElement>(null);
  const latestTurn = useRef<HTMLElement>(null);
  const messagesEnd = useRef<HTMLDivElement>(null);
  const previousTurnCount = useRef(0);
  const activeTraceRef = useRef<AgentTrace | null>(null);
  const activePredictionRef = useRef<NextStepPrediction | null>(null);

  // Mirrors backend REVIEW_ROLES / UPLOAD_ROLES; the backend enforces regardless.
  const canReview = can.review(role);
  const canUpload = can.upload(role);

  const refreshPending = useCallback(async () => {
    try {
      const r = await call<PendingChangePage>("pending-changes?status=pending");
      setPending(r.pending);
    } catch { /* tray is secondary; never block the conversation on it */ }
  }, [call]);

  useEffect(() => {
    let active = true;
    call<PendingChangePage>("pending-changes?status=pending")
      .then((result) => { if (active) setPending(result.pending); })
      .catch(() => undefined);
    return () => { active = false; };
  }, [call]);

  // Reopening a recent ask prefills it. Re-sending a mutation on navigation
  // could stage a duplicate proposal, so only an explicit submit sends it.
  const navigationKey = params.toString();
  const qParam = params.get("q");
  const newParam = params.get("new");
  const sessionParam = params.get("session");
  useEffect(() => {
    let active = true;
    const frame = window.requestAnimationFrame(() => {
      if (newParam) {
        conversationVersion.current += 1;
        session.current = undefined;
        setTurns([]); setQ(""); setSending(null); setBusy(false);
        setLoadingHistory(false);
        setActiveTrace(null); setStreamedAnswer("");
        setActivePrediction(null); activePredictionRef.current = null;
        activeTraceRef.current = null;
        setErr(null); setNote(null);
      } else if (sessionParam) {
        const requestVersion = ++conversationVersion.current;
        session.current = undefined;
        previousTurnCount.current = 0;
        setTurns([]); setQ(""); setSending(null); setBusy(false);
        setLoadingHistory(true);
        setActiveTrace(null); setStreamedAnswer("");
        setActivePrediction(null); activePredictionRef.current = null;
        activeTraceRef.current = null;
        setErr(null); setNote(null);
        call<ChatSession>(`chat/sessions/${encodeURIComponent(sessionParam)}`)
          .then((saved) => {
            if (!active || requestVersion !== conversationVersion.current) return;
            session.current = saved.session_id;
            setTurns(saved.turns.map(restoreTurn));
          })
          .catch((error) => {
            if (!active || requestVersion !== conversationVersion.current) return;
            setErr((error as Error).message);
          })
          .finally(() => {
            if (active && requestVersion === conversationVersion.current) {
              setLoadingHistory(false);
            }
          });
      } else if (qParam) {
        setLoadingHistory(false);
        setQ(qParam);
        composer.current?.focus();
      }
    });
    return () => { active = false; window.cancelAnimationFrame(frame); };
  }, [call, navigationKey, newParam, qParam, sessionParam]);

  useEffect(() => {
    const input = composer.current;
    if (!input) return;
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
  }, [q]);

  useEffect(() => {
    const completedTurn = turns.length > previousTurnCount.current;
    previousTurnCount.current = turns.length;
    if (!completedTurn && !sending) return;
    const frame = window.requestAnimationFrame(() => {
      const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      const target = completedTurn ? latestTurn.current : messagesEnd.current;
      target?.scrollIntoView({
        behavior: reduced ? "auto" : "smooth",
        block: completedTurn ? "start" : "end",
      });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [turns.length, sending, streamedAnswer.length, activeTrace?.steps.length]);

  async function ask(question: string) {
    const cleanQuestion = question.trim();
    if (!cleanQuestion || busy || loadingHistory) return;
    const requestVersion = conversationVersion.current;
    const initialTrace: AgentTrace = {
      query: cleanQuestion,
      steps: [{
        id: "request",
        label: "Connecting to agent stream",
        status: "running",
      }],
    };
    activeTraceRef.current = initialTrace;
    setActiveTrace(initialTrace);
    setStreamedAnswer("");
    setActivePrediction(null); activePredictionRef.current = null;
    setBusy(true); setSending(cleanQuestion); setQ(""); setErr(null); setNote(null);
    let completed: ChatStreamComplete | null = null;
    let streamFailure = "";

    const handleEvent = (event: ChatStreamEvent): void | Promise<void> => {
      if (requestVersion !== conversationVersion.current) return;
      if (event.type === "answer_delta") {
        setStreamedAnswer((current) => current + event.delta);
        return new Promise((resolve) => window.requestAnimationFrame(() => resolve()));
      }
      const nextTrace = applyTraceEvent(activeTraceRef.current ?? initialTrace, event);
      activeTraceRef.current = nextTrace;
      setActiveTrace(nextTrace);
      if (event.type === "prediction_complete") {
        const prediction = { intent: event.intent, suggestions: event.suggestions };
        activePredictionRef.current = prediction;
        setActivePrediction(prediction);
      } else if (event.type === "complete") {
        completed = event;
        setStreamedAnswer(event.answer);
        if (event.next_steps) {
          activePredictionRef.current = event.next_steps;
          setActivePrediction(event.next_steps);
        }
      } else if (event.type === "error") {
        streamFailure = event.message;
      }
    };

    const page = capture();
    const request = {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: cleanQuestion, session_id: session.current,
                             batch_id: page.batch_id, page_context: page }),
    };

    try {
      try {
        await stream<ChatStreamEvent>("chat/stream", request, handleEvent);
      } catch (error) {
        if (!(error instanceof ApiError) || ![404, 405].includes(error.status)) throw error;
        handleEvent({
          type: "fallback",
          reason: "Live progress is unavailable on this backend; using the standard response path.",
        });
        const response = await call<ChatResponse>("chat", request);
        const fallbackComplete: ChatStreamComplete = {
          ...response,
          type: "complete",
          provider: "standard endpoint",
          model: "non-streaming response",
          tool_calls: [],
        };
        completed = fallbackComplete;
        handleEvent(fallbackComplete);
      }
      if (requestVersion !== conversationVersion.current) return;
      if (streamFailure) throw new Error(streamFailure);
      if (!completed) throw new Error("The agent stream ended before returning a result.");

      const result = completed as ChatStreamComplete;
      const actionNotice = applyActions(result.page_actions ?? [], page);
      if (actionNotice) setNote(actionNotice);
      session.current = result.session_id;
      const staged = result.sources.some((source) => source.type === "pending_change");
      const finalTrace = activeTraceRef.current ?? initialTrace;
      const nextSteps = result.next_steps ?? activePredictionRef.current ?? undefined;
      setTurns((current) => [...current, {
        q: cleanQuestion,
        a: result.answer,
        sources: result.sources,
        staged,
        trace: finalTrace,
        nextSteps,
        stagedAction: result.staged_action ?? null,
      }]);
      notifyChatHistoryChanged();
      if (staged) await refreshPending();
    } catch (error) {
      if (requestVersion === conversationVersion.current) {
        const message = (error as Error).message;
        let failedTrace = activeTraceRef.current ?? initialTrace;
        if (!failedTrace.steps.some((step) => step.status === "error")) {
          failedTrace = applyTraceEvent(failedTrace, {
            type: "error",
            stage: "stream",
            status: 500,
            error_type: (error as Error).name || "Error",
            message,
          });
          activeTraceRef.current = failedTrace;
        }
        setTurns((current) => [...current, {
          q: cleanQuestion,
          a: "",
          sources: [],
          staged: false,
          trace: failedTrace,
          error: message,
        }]);
        setQ((current) => current || cleanQuestion);
        setErr(message);
      }
    } finally {
      if (requestVersion === conversationVersion.current) {
        setBusy(false); setSending(null); setActiveTrace(null); setStreamedAnswer("");
        setActivePrediction(null); activePredictionRef.current = null;
        activeTraceRef.current = null;
      }
    }
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
      const activity = t.trace.steps.map((step) =>
        `- [${step.status}] ${step.label}${step.result ? ` — ${step.result}` : ""}`
      ).join("\n");
      const answer = t.error ? `_Failed: ${t.error}_` : t.a;
      return `## ${t.q}\n\n${answer}\n\n<details><summary>Agent activity</summary>\n\n`
             + `${activity}\n\n</details>\n\n_Sources: ${src}_`
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
    conversationVersion.current += 1;
    session.current = undefined;
    setTurns([]); setQ(""); setSending(null); setBusy(false);
    setLoadingHistory(false);
    setActiveTrace(null); setStreamedAnswer("");
    setActivePrediction(null); activePredictionRef.current = null;
    activeTraceRef.current = null;
    setErr(null); setNote(null);
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
            // Nulls are sent as nulls, not coerced to 0. The endpoint merges
            // each missing value with the staged proposal and then the item's
            // current level; `?? 0` instead recorded ROP=0/Min=0 for a
            // proposal that only stated a Max, and 3 >= 0 >= 0 passes the
            // ordering check — a silent zero all the way to the export.
            final_max: p.proposed_max,
            final_rop: p.proposed_rop,
            final_min: p.proposed_min,
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

  /** An action card carries the same fields the tray's handlers read, so the
   *  card reuses them rather than posting to the review endpoints itself. The
   *  card's own values are authoritative — the tray row is only a fallback for
   *  fields the card does not carry. */
  function asPendingChange(action: StagedAction): PendingChange | null {
    if (action.pending_id === null) return null;
    const tray = pending.find((p) => p.pending_id === action.pending_id);
    return { ...(tray ?? {}), ...action } as unknown as PendingChange;
  }

  const empty = turns.length === 0 && !sending && !loadingHistory;
  const completedTurns = turns.filter((turn) => !turn.error).length;
  const failedTurns = turns.length - completedTurns;
  const runningStep = activeTrace?.steps.findLast((step) => step.status === "running");

  return (
    <div className={`chat-page ${empty ? "is-empty" : "has-turns"}`}>
      {empty ? (
        <header className="chat-hero">
          <span className="orb" aria-hidden><span /></span>
          <p className="chat-eyebrow">Grounded review assistant</p>
          <h1>
            <span>Hello, {user}.</span>{" "}
            What are we reviewing?
          </h1>
          <p className="chat-intro">
            Ask why an item was flagged, trace its review history, or stage a change
            in your own words. Every answer stays tied to recorded BOM data.
          </p>
          <div className="chat-assurance">
            <span aria-hidden />
            The engine calculates · you approve · WINGS updates after approval
          </div>
        </header>
      ) : (
        <header className="chat-header">
          <div>
            <p className="chat-eyebrow">NYRA assistant</p>
            <h1>Review conversation</h1>
            <p>
              {loadingHistory
                ? "Loading saved conversation…"
                : sending
                ? runningStep?.label ?? "Connecting to the agent…"
                : `${completedTurns} completed ${completedTurns === 1 ? "turn" : "turns"}`
                  + (failedTurns ? ` · ${failedTurns} failed` : "")}
            </p>
          </div>
          <div className="chat-header-actions">
            <button type="button" className="btn" onClick={exportChat}
                    disabled={turns.length === 0}>
              Export
            </button>
            <button type="button" className="btn" onClick={newChat} disabled={busy}>
              New chat
            </button>
          </div>
        </header>
      )}

      {err && <Banner kind="error">{err}</Banner>}
      {note && <Banner kind="info">{note}</Banner>}

      {pending.length > 0 && (
        <details className="card pending-tray" open>
          <summary>
            <span className="pending-icon" aria-hidden>!</span>
            <span className="pending-summary-copy">
              <strong>Staged changes</strong>
              <span>Review before anything enters the approval path.</span>
            </span>
            <span className="pending-count">{pending.length}</span>
          </summary>
          <div className="pending-list">
            {pending.map((p) => (
              <article key={p.pending_id} className="pending-item">
                <div className="pending-item-head">
                  <strong>{p.item_id}</strong>
                  <span>Proposal #{p.pending_id}</span>
                </div>
                <div className="pending-values tnum">
                  {p.proposed_max !== null && <span>Max <strong>{p.proposed_max}</strong></span>}
                  {p.proposed_rop !== null && <span>ROP <strong>{p.proposed_rop}</strong></span>}
                  {p.proposed_min !== null && <span>Min <strong>{p.proposed_min}</strong></span>}
                </div>
                <blockquote>“{p.source_utterance}”</blockquote>
                <div className="pending-meta">
                  Staged by {p.created_by || "unknown"} · {formatTimestamp(p.created_at)} ·
                  parsed by {p.parsed_by || "unknown"}
                </div>
                <div className="pending-actions">
                  <button type="button" className="btn btn-primary" disabled={busy || !canReview}
                          onClick={() => confirm(p)}>
                    Confirm change
                  </button>
                  <button type="button" className="btn" disabled={busy || !canReview}
                          onClick={() => discard(p)}>
                    Discard
                  </button>
                  {p.batch_id !== null && (
                    <Link className="btn" href={`/batches/${p.batch_id}/items/${p.item_id}`}>
                      Open item
                    </Link>
                  )}
                </div>
                {!canReview && (
                  <p className="pending-role-note">Your role cannot confirm changes.</p>
                )}
              </article>
            ))}
          </div>
        </details>
      )}

      {!empty && (
        <section className="chat-transcript" aria-label="Conversation" aria-live="polite"
                 aria-relevant="additions" aria-busy={Boolean(sending || loadingHistory)}>
          {loadingHistory && (
            <div className="chat-history-loading" role="status">
              <span className="typing-dots" aria-hidden><i /><i /><i /></span>
              Loading the complete conversation…
            </div>
          )}
          {turns.map((turn, index) => (
            <article key={index} ref={index === turns.length - 1 ? latestTurn : undefined}
                     className="chat-turn turn-in">
              <div className="chat-row chat-row-user">
                <div className="chat-bubble chat-bubble-user">{turn.q}</div>
                <span className="chat-avatar chat-avatar-user" aria-hidden>
                  {user.slice(0, 1).toUpperCase()}
                </span>
              </div>
              <div className="chat-row chat-row-assistant">
                <span className="chat-avatar chat-avatar-assistant" aria-hidden>N</span>
                <div className="chat-response">
                  <div className="chat-speaker">NYRA</div>
                  {turn.a && <ChatAnswer>{turn.a}</ChatAnswer>}
                  {canReview && turn.stagedAction && (
                    <ActionCard action={turn.stagedAction} busy={busy}
                                onConfirm={(action) => {
                                  const p = asPendingChange(action);
                                  if (p) void confirm(p);
                                }}
                                onDiscard={(action) => {
                                  const p = asPendingChange(action);
                                  if (p) void discard(p);
                                }} />
                  )}
                  {index === turns.length - 1 && turn.nextSteps && (
                    <NextStepSuggestions prediction={turn.nextSteps} disabled={busy}
                                         onSelect={(prompt) => void ask(prompt)} />
                  )}
                  <AgentToolCalls trace={turn.trace} />
                  {turn.error && (
                    <div className="chat-run-error" role="alert">
                      <strong>The agent could not complete this request.</strong>
                      <span>{turn.error}</span>
                    </div>
                  )}
                  <AgentActivity trace={turn.trace} />
                  {turn.staged && (
                    <div className="staged-notice">
                      <span aria-hidden>!</span>
                      Staged only — confirm the proposal above to record a decision.
                    </div>
                  )}
                  {turn.sources.length > 0 && (
                    <div className="chat-sources" aria-label="Answer sources">
                      <span className="chat-sources-label">Sources</span>
                      {turn.sources.map((source, sourceIndex) => {
                        const href = sourceHref(source);
                        const contents = <><SourceIcon />{sourceLabel(source)}</>;
                        return href ? (
                          <Link key={sourceIndex} className="source-pill" href={href}>
                            {contents}
                          </Link>
                        ) : (
                          <span key={sourceIndex} className="source-pill">{contents}</span>
                        );
                      })}
                    </div>
                  )}
                </div>
              </div>
            </article>
          ))}

          {sending && (
            <article className="chat-turn turn-in">
              <div className="chat-row chat-row-user">
                <div className="chat-bubble chat-bubble-user is-pending">{sending}</div>
                <span className="chat-avatar chat-avatar-user" aria-hidden>
                  {user.slice(0, 1).toUpperCase()}
                </span>
              </div>
              <div className="chat-row chat-row-assistant">
                <span className="chat-avatar chat-avatar-assistant" aria-hidden>N</span>
                <div className="chat-response chat-thinking" role="status">
                  <span className="chat-speaker">NYRA</span>
                  {activeTrace && <AgentToolCalls trace={activeTrace} />}
                  {activeTrace && <AgentActivity trace={activeTrace} live />}
                  {streamedAnswer ? (
                    <div className="streaming-answer">
                      <ChatAnswer>{streamedAnswer}</ChatAnswer>
                      <span className="stream-caret" aria-hidden />
                    </div>
                  ) : !activeTrace ? (
                    <span className="typing-dots" aria-hidden><i /><i /><i /></span>
                  ) : null}
                  {activePrediction && (
                    <NextStepSuggestions prediction={activePrediction} disabled
                                         onSelect={() => undefined} />
                  )}
                  <span className="sr-only">NYRA is showing live execution progress.</span>
                </div>
              </div>
            </article>
          )}
          <div ref={messagesEnd} className="messages-end" aria-hidden />
        </section>
      )}

      <form className={`composer ${empty ? "composer-empty" : "composer-sticky"}`}
            onSubmit={(event) => { event.preventDefault(); void ask(q); }}>
        <label htmlFor="ask" className="sr-only">Ask about a recommendation</label>
        <textarea
          id="ask"
          ref={composer}
          rows={1}
          className="composer-input"
          value={q}
          aria-describedby="composer-help"
          onChange={(event) => setQ(event.target.value)}
          placeholder="Ask about an item, or state a change in your own words…"
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              void ask(q);
            }
          }}
        />
        <div className="composer-footer">
          <span id="composer-help" className="composer-hint">
            {loadingHistory
              ? "Loading saved conversation…"
              : sending
              ? runningStep?.label ?? "Connecting to the agent…"
              : canReview
                ? "Proposals are staged, never applied · Enter to send"
                : `Read-only as ${role} · Enter to send`}
          </span>
          <div className="composer-actions">
            {canUpload && (
              <>
                <input ref={filePicker} type="file" accept=".csv,text/csv" className="hidden"
                       onChange={(event) => {
                         const file = event.target.files?.[0];
                         event.target.value = "";
                         if (file) void attach(file);
                       }} />
                <button type="button" className="btn composer-attach" disabled={busy}
                        title="Upload a BOM extract (.csv) as a new batch"
                        onClick={() => filePicker.current?.click()}>
                  <AttachIcon />
                  <span>Attach CSV</span>
                </button>
              </>
            )}
            <button className="btn btn-primary composer-send"
                    disabled={busy || loadingHistory || !q.trim()}>
              <span>{sending ? "Working" : "Send"}</span>
              <SendIcon />
            </button>
          </div>
        </div>
      </form>

      {empty && (
        <section className="chat-suggestions" aria-label="Suggested prompts">
          <div className="suggestions-heading">
            <span>Start with a workflow</span>
            <span>Uses the latest scored batch</span>
          </div>
          <SuggestionCards onSend={(prompt) => void ask(prompt)} onFill={fill}
                           canReview={canReview} disabled={busy || loadingHistory} />
        </section>
      )}
    </div>
  );
}
