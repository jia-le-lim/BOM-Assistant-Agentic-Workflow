"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { ApiError, useApi } from "@/lib/api";
import { useAssistantContext } from "@/lib/assistant-context";
import { can } from "@/lib/session";
import { notifyChatHistoryChanged } from "@/lib/history";
import type {
  Batch, ChatHistoryTurn, ChatResponse, ChatSession, ChatSkill, ChatStreamComplete, ChatStreamEvent,
  ConfirmPendingResult, NextStepPrediction, PendingChange, PendingChangePage,
  StagedAction, UploadSummary,
} from "@/lib/types";
import { Banner } from "@/components/ui";
import { ChatAnswer } from "@/components/ChatAnswer";
import { ChatComposer } from "@/components/ChatComposer";
import { ReminderEditor } from "@/components/ReminderEditor";
import { useImageAttachment } from "@/lib/image-attachment";
import { ActivityIcon, ChatActivityPanel } from "@/components/ChatActivityPanel";
import type { AgentTrace, AgentTraceStep } from "@/components/AgentActivity";
import { NextStepSuggestions } from "@/components/NextStepSuggestions";
import { SuggestionCards } from "@/components/SuggestionCards";
import { ActionCard } from "@/components/ActionCard";
import "./chat.css";

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
  if (event.type === "skill") {
    return upsertTraceStep(trace, {
      id: "skill", label: `/${event.name}`, detail: event.title,
      result: "Loaded skill instructions", status: "done",
    });
  }
  if (event.type === "request") {
    return upsertTraceStep({
      ...trace,
      provider: event.provider,
      model: event.model,
      batchId: event.batch_id,
    }, {
      id: "request",
      label: "Request accepted",
      detail: event.batch_id ? `Using workspace #${event.batch_id}` : "No workspace selected",
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
    return upsertTraceStep({ ...trace, fallback: event.fallback, responseStatus: event.response_status }, {
      id: "grounding",
      label: "Grounding check",
      result: `${event.source_count} authoritative source${event.source_count === 1 ? "" : "s"} · `
            + `${event.tool_count} tool call${event.tool_count === 1 ? "" : "s"}`,
      status: event.fallback ? "warning" : "done",
      detail: event.response_reason,
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
      fallback: event.fallback ?? trace.fallback,
      responseStatus: event.response_status ?? trace.responseStatus,
    };
    if (event.skill) completedTrace = applyTraceEvent(completedTrace, { type: "skill", ...event.skill });
    event.tool_calls.forEach((tool, index) => {
      const id = `tool-${index + 1}`;
      const current = completedTrace.steps.find((step) => step.id === id);
      completedTrace = upsertTraceStep(completedTrace, {
        id,
        label: current?.label ?? `Query ${toolLabel(tool.name)}`,
        detail: current?.detail ?? JSON.stringify(tool.args),
        result: current?.result ?? tool.summary ?? (tool.ok ? "Tool call completed." : "Tool call failed."),
        status: current?.status ?? (tool.status === "empty" ? "warning" : tool.ok ? "done" : "error"),
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
    detail: saved.batch_id ? `Used workspace #${saved.batch_id}` : "No workspace selected",
    result: [saved.provider, saved.model].filter(Boolean).join(" · ") || undefined,
    status: "done",
  }];
  if (saved.skill) {
    steps.push({ id: "skill", label: `/${saved.skill.name}`, detail: saved.skill.title,
      result: "Saved skill run", status: "done" });
  }
  saved.tool_calls.forEach((tool, index) => {
    steps.push({
      id: `tool-${index + 1}`,
      label: `Query ${toolLabel(tool.name)}`,
      detail: JSON.stringify(tool.args ?? {}),
      result: tool.summary ?? (tool.ok ? "Recorded tool call completed." : "Recorded tool call failed."),
      status: tool.status === "empty" ? "warning" : tool.ok ? "done" : "error",
      technical: true,
      kind: "tool",
      toolName: tool.name,
      toolArgs: tool.args ?? {},
    });
  });
  if (saved.response_reason || saved.fallback) {
    steps.push({ id: "outcome", label: "Response outcome", detail: saved.response_reason,
      status: saved.fallback ? "warning" : "done" });
  }
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
      fallback: saved.fallback,
      responseStatus: saved.response_status,
      steps,
    },
  };
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
  const [activityOpen, setActivityOpen] = useState(false);
  const [selectedActivity, setSelectedActivity] = useState<number | null>(null);
  const [workspaceId, setWorkspaceId] = useState<number | null>(null);
  const [reminderCapture, setReminderCapture] = useState<{ file?: File; note?: string } | null>(null);
  const [workspaces, setWorkspaces] = useState<Batch[]>([]);
  const [workspacesLoading, setWorkspacesLoading] = useState(true);
  const [workspacesError, setWorkspacesError] = useState<string | null>(null);
  const [skills, setSkills] = useState<ChatSkill[]>([]);
  const [skillsLoading, setSkillsLoading] = useState(true);
  const [skillsError, setSkillsError] = useState<string | null>(null);
  const session = useRef<string | undefined>(undefined);
  const conversationVersion = useRef(0);
  const composer = useRef<HTMLTextAreaElement>(null);
  const activityToggle = useRef<HTMLButtonElement>(null);
  const latestTurn = useRef<HTMLElement>(null);
  const messagesEnd = useRef<HTMLDivElement>(null);
  const previousTurnCount = useRef(0);
  const activeTraceRef = useRef<AgentTrace | null>(null);
  const activePredictionRef = useRef<NextStepPrediction | null>(null);

  // Mirrors backend REVIEW_ROLES / UPLOAD_ROLES; the backend enforces regardless.
  const canReview = can.review(role);
  const canUpload = can.upload(role);

  const loadSkills = useCallback(async (signal?: AbortSignal) => {
    setSkillsLoading(true); setSkillsError(null);
    try {
      const result = await call<{ skills: ChatSkill[] }>("chat/skills", { signal });
      if (!signal?.aborted) setSkills(result.skills);
    } catch (error) {
      if (!signal?.aborted) setSkillsError((error as Error).message);
    } finally {
      if (!signal?.aborted) setSkillsLoading(false);
    }
  }, [call]);

  useEffect(() => {
    const controller = new AbortController();
    const frame = window.requestAnimationFrame(() => void loadSkills(controller.signal));
    return () => { window.cancelAnimationFrame(frame); controller.abort(); };
  }, [loadSkills]);

  const loadWorkspaces = useCallback(async (signal?: AbortSignal) => {
    setWorkspacesLoading(true); setWorkspacesError(null);
    try {
      const result = await call<Batch[]>("batches", { signal });
      if (!signal?.aborted) setWorkspaces(result);
    } catch (error) {
      if (!signal?.aborted) setWorkspacesError((error as Error).message);
    } finally {
      if (!signal?.aborted) setWorkspacesLoading(false);
    }
  }, [call]);

  useEffect(() => {
    const controller = new AbortController();
    const frame = window.requestAnimationFrame(() => void loadWorkspaces(controller.signal));
    return () => { window.cancelAnimationFrame(frame); controller.abort(); };
  }, [loadWorkspaces]);

  function workspaceLabel(id: number) {
    return workspaces.find((workspace) => workspace.batch_id === id)?.label || `Workspace #${id}`;
  }

  useEffect(() => {
    if (!activityOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && window.matchMedia("(max-width: 900px)").matches) {
        setActivityOpen(false);
        activityToggle.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [activityOpen]);

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
  const imageAttachment = useImageAttachment(user + ":" + navigationKey);
  const qParam = params.get("q");
  const newParam = params.get("new");
  const sessionParam = params.get("session");
  useEffect(() => {
    let active = true;
    const frame = window.requestAnimationFrame(() => {
      if (newParam) {
        setWorkspaceId(null);
        setSelectedActivity(null);
        conversationVersion.current += 1;
        session.current = undefined;
        setTurns([]); setQ(""); setSending(null); setBusy(false);
        setLoadingHistory(false);
        setActiveTrace(null); setStreamedAnswer("");
        setActivePrediction(null); activePredictionRef.current = null;
        activeTraceRef.current = null;
        setErr(null); setNote(null);
      } else if (sessionParam) {
        setWorkspaceId(null);
        setSelectedActivity(null);
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
            setWorkspaceId(saved.turns.at(-1)?.batch_id ?? null);
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
      batchId: workspaceId,
      steps: [{
        id: "request",
        label: "Connecting to agent stream",
        status: "running",
      }],
    };
    setSelectedActivity(null);
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

    const snapshot = capture();
    const page = workspaceId === null ? snapshot : {
      ...snapshot,
      batch_id: workspaceId,
      title: `Ask NYRA · ${workspaceLabel(workspaceId)}`.slice(0, 160),
    };
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
          tool_calls: response.tool_calls ?? [],
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
      // Pin the actual workspace returned by the backend for subsequent turns,
      // including conversations that started with the default latest workspace.
      setWorkspaceId(result.batch_id ?? workspaceId);
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
      void loadWorkspaces();
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
    setWorkspaceId(null);
    setSelectedActivity(null);
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
  const focusedPending = workspaceId === null ? pending : pending.filter((change) => change.batch_id === workspaceId);

  return (
    <div className={`chat-page chat-workspace ${empty ? "is-empty" : "has-turns"}${activityOpen ? " activity-visible" : ""}`}>
      <div className="chat-main">
        <header className="chat-header">
          <div className="chat-header-copy">
            <h1>{empty ? "Ask NYRA" : "Review conversation"}</h1>
            <p>BOM review assistant</p>
          </div>
          <div className="chat-header-actions">
            <button type="button" className="btn" onClick={exportChat}
                    disabled={turns.length === 0 || Boolean(sending)} title="Export conversation as Markdown">
              Export
            </button>
            <button type="button" className="btn" onClick={newChat} disabled={busy}>
              New chat
            </button>
            <button ref={activityToggle} type="button" className="btn chat-activity-toggle" aria-controls="chat-activity"
                    aria-expanded={activityOpen} onClick={() => setActivityOpen((current) => !current)}>
              <ActivityIcon /> Activity
            </button>
          </div>
        </header>

        <section className="chat-transcript" aria-label="Conversation" aria-live="polite"
                 aria-relevant="additions" aria-busy={Boolean(sending || loadingHistory)}>

          {err && <Banner kind="error">{err}</Banner>}
          {note && <Banner kind="info">{note}</Banner>}

          {focusedPending.length > 0 && (
            <details className="card pending-tray" open>
              <summary>
                <span className="pending-icon" aria-hidden>!</span>
                <span className="pending-summary-copy">
                  <strong>Staged changes</strong>
                  <span>Review before anything enters the approval path.</span>
                </span>
                <span className="pending-count">{focusedPending.length}</span>
              </summary>
              <div className="pending-list">
                {focusedPending.map((p) => (
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

          {empty && (
            <div className="chat-welcome">
              <span className="chat-welcome-mark" aria-hidden>N</span>
              <p className="chat-eyebrow">Your review starts here</p>
              <h2>What are we reviewing?</h2>
              <p className="chat-intro">Ask about a part, understand a recommendation, or work through your review queue. Answers are grounded in your BOM data.</p>
              <section className="chat-suggestions" aria-label="Suggested prompts">
                <SuggestionCards onSend={(prompt) => void ask(prompt)} onFill={fill}
                                 canReview={canReview} disabled={busy || loadingHistory} />
              </section>
            </div>
          )}
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
                <div className="chat-bubble chat-bubble-user">
                  {turn.trace.batchId != null && <span className="chat-message-workspace">@{workspaceLabel(turn.trace.batchId)}</span>}
                  {turn.q}
                </div>
              </div>
              <div className="chat-row chat-row-assistant">
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
                  {turn.error && (
                    <div className="chat-run-error" role="alert">
                      <strong>The agent could not complete this request.</strong>
                      <span>{turn.error}</span>
                    </div>
                  )}
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
                  <div className="chat-response-actions">
                    <button type="button" className="chat-view-activity"
                            aria-label={`View activity for message ${index + 1}`}
                            aria-controls="chat-activity"
                            onClick={() => { setSelectedActivity(index); setActivityOpen(true); }}>
                      <ActivityIcon /> View activity
                      <span>Tools: {turn.trace.steps.filter((step) => step.kind === "tool").length}</span>
                    </button>
                  </div>
                </div>
              </div>
            </article>
          ))}

          {sending && (
            <article className="chat-turn turn-in">
              <div className="chat-row chat-row-user">
                <div className="chat-bubble chat-bubble-user is-pending">
                  {activeTrace?.batchId != null && <span className="chat-message-workspace">@{workspaceLabel(activeTrace.batchId)}</span>}
                  {sending}
                </div>
              </div>
              <div className="chat-row chat-row-assistant">
                <div className="chat-response chat-thinking" role="status">
                  <span className="chat-speaker">NYRA</span>
                  {streamedAnswer ? (
                    <div className="streaming-answer">
                      <ChatAnswer>{streamedAnswer}</ChatAnswer>
                      <span className="stream-caret" aria-hidden />
                    </div>
                  ) : (
                    <span className="chat-working"><span className="typing-dots" aria-hidden><i /><i /><i /></span> Working on your request</span>
                  )}
                  {activePrediction && (
                    <NextStepSuggestions prediction={activePrediction} disabled
                                         onSelect={() => undefined} />
                  )}
                  <span className="sr-only">NYRA is responding. Execution progress is in the Activity panel.</span>
                </div>
              </div>
            </article>
          )}
          <div ref={messagesEnd} className="messages-end" aria-hidden />
        </section>

        <ChatComposer key={navigationKey} value={q} onChange={setQ} onSend={(question) => void ask(question)}
                      inputRef={composer} busy={busy} sending={Boolean(sending)} loadingHistory={loadingHistory}
                      canUpload={canUpload} onUpload={(file) => void attach(file)}
                      onCapture={canReview ? (file, note) => setReminderCapture({ file, note }) : undefined}
                      imageAttachment={canReview ? imageAttachment : undefined}
                      workspaceId={workspaceId} onWorkspaceChange={setWorkspaceId} workspaces={workspaces}
                      workspacesLoading={workspacesLoading} workspacesError={workspacesError}
                      onRetryWorkspaces={() => void loadWorkspaces()}
                      skills={skills} skillsLoading={skillsLoading} skillsError={skillsError}
                      onRetrySkills={() => void loadSkills()} />

        <p className="chat-composer-note">{canReview ? "Changes are staged for your approval." : `Read-only access as ${role}.`}</p>
      </div>
      {reminderCapture && <ReminderEditor initialFile={reminderCapture.file} initialNote={reminderCapture.note}
        batchId={workspaces.find((workspace) => workspace.batch_id === workspaceId)?.uploaded_by === user ? workspaceId : null}
        onClose={() => setReminderCapture(null)}
        onSaved={() => { setReminderCapture(null); imageAttachment.clear(); setQ(""); setNote("Reminder saved. Find it in Engineer reminders."); }} />}
      <ChatActivityPanel traces={turns.map((turn) => turn.trace)} activeTrace={activeTrace}
                         selectedTurn={selectedActivity} onSelectTurn={setSelectedActivity}
                         open={activityOpen} loading={loadingHistory} />
    </div>
  );
}
