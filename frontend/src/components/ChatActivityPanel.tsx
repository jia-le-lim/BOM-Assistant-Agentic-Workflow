"use client";

import { useEffect, useRef } from "react";
import type { AgentTrace, AgentTraceStep } from "./AgentActivity";

export function ActivityIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <rect x="3" y="4" width="18" height="16" rx="3" /><path d="M15 4v16M7 9h4M7 13h4" />
    </svg>
  );
}

function stepStatus(step: AgentTraceStep, live: boolean) {
  if (step.status === "running") return live ? "Running" : "Stopped";
  if (step.status === "error") return "Failed";
  if (step.status === "warning") return step.kind === "tool" ? "No results" : "Notice";
  return "Done";
}

export function ChatActivityPanel({ traces, activeTrace, selectedTurn, onSelectTurn, open, loading }: {
  traces: AgentTrace[];
  activeTrace: AgentTrace | null;
  selectedTurn: number | null;
  onSelectTurn: (index: number | null) => void;
  open: boolean;
  loading: boolean;
}) {
  const scroll = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const trace = selectedTurn === null ? activeTrace ?? traces.at(-1) : traces[selectedTurn];
  const live = selectedTurn === null && Boolean(activeTrace);
  const toolCount = trace?.steps.filter((step) => step.kind === "tool").length ?? 0;
  const hasError = trace?.steps.some((step) => step.status === "error");
  const status = loading ? "Loading" : live ? "Working" : !trace ? "Ready"
    : trace.responseStatus === "clarification" ? "Needs input"
    : trace.responseStatus === "no_results" ? "No results"
    : trace.responseStatus === "partial" ? "Partial response"
    : hasError ? "Failed" : trace.fallback ? "Fallback used" : "Complete";

  useEffect(() => {
    if (live && follow.current && scroll.current) {
      scroll.current.scrollTop = scroll.current.scrollHeight;
    }
  }, [live, trace]);

  return (
    <aside id="chat-activity" className={`chat-activity-panel${open ? " is-open" : ""}`}
           aria-labelledby="chat-activity-title" data-assistant-private>
      <header className="chat-activity-header">
        <h2 id="chat-activity-title"><ActivityIcon /> Activity</h2>
        <span className={`chat-activity-status${live ? " is-live" : ""}${hasError ? " is-error" : ""}`}
              role="status"><i aria-hidden />{status}</span>
      </header>
      <p className="chat-activity-caption">Reasoning &amp; tool calls</p>

      {traces.length > 0 && (
        <div className="chat-activity-select">
          <label className="sr-only" htmlFor="activity-turn">Activity for message</label>
          <select id="activity-turn" value={selectedTurn ?? "latest"}
                  onChange={(event) => {
                    follow.current = true;
                    onSelectTurn(event.target.value === "latest" ? null : Number(event.target.value));
                    if (scroll.current) scroll.current.scrollTop = 0;
                  }}>
            <option value="latest">{activeTrace ? "Current request" : "Latest response"}</option>
            {traces.map((item, index) => (
              <option key={index} value={index}>{index + 1}. {item.query.slice(0, 90)}</option>
            ))}
          </select>
        </div>
      )}

      <div className="chat-activity-scroll" ref={scroll} onScroll={(event) => {
        const element = event.currentTarget;
        follow.current = element.scrollHeight - element.scrollTop - element.clientHeight < 48;
      }}>
        {trace ? (
          <>
            <div className="chat-activity-query">
              <span>{live ? "Current request" : "Request"}</span>
              <p>{trace.query}</p>
            </div>
            <ol className="activity-timeline" aria-label="Execution steps">
              {trace.steps.map((step) => (
                <li key={step.id} className={`activity-event is-${step.status === "running" && !live ? "stopped" : step.status}`}>
                  <span className="activity-event-dot" aria-hidden />
                  <div className="activity-event-content">
                    <div className="activity-event-heading">
                      <strong>{step.kind === "tool" ? "Tool call" : step.label}</strong>
                      <span>{stepStatus(step, live)}</span>
                    </div>
                    {step.kind === "tool" && <code className="activity-tool-name">{step.toolName ?? step.label}</code>}
                    {step.result && <p>{step.result}</p>}
                    {step.detail && !step.technical && <p className="activity-event-detail">{step.detail}</p>}
                    {(step.kind === "tool" || (step.technical && step.detail)) && (
                      <details className="activity-event-data">
                        <summary>{step.kind === "tool" ? "View input" : "View details"}</summary>
                        <pre>{step.kind === "tool" ? JSON.stringify(step.toolArgs ?? {}, null, 2) : step.detail}</pre>
                      </details>
                    )}
                  </div>
                </li>
              ))}
            </ol>
          </>
        ) : (
          <div className="chat-activity-empty">
            <span className="activity-empty-icon"><ActivityIcon /></span>
            <strong>{loading ? "Loading conversation" : "Follow along here"}</strong>
            <p>{loading ? "Saved tool calls will appear shortly." : "As NYRA works, its progress and tool calls will appear in this timeline."}</p>
          </div>
        )}
      </div>

      <footer className="chat-activity-footer">
        <span>{trace ? `${trace.steps.length} steps · ${toolCount} tool call${toolCount === 1 ? "" : "s"}` : "Waiting for your first message"}</span>
        {trace?.model && <span title={[trace.provider, trace.model].filter(Boolean).join(" · ")}>{trace.model}</span>}
      </footer>
    </aside>
  );
}
