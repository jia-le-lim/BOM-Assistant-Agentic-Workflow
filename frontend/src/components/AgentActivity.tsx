export type AgentStepStatus = "running" | "done" | "warning" | "error";

export interface AgentTraceStep {
  id: string;
  label: string;
  detail?: string;
  result?: string;
  status: AgentStepStatus;
  technical?: boolean;
}

export interface AgentTrace {
  query: string;
  provider?: string;
  model?: string;
  batchId?: number | null;
  fallback?: boolean;
  steps: AgentTraceStep[];
}

export function AgentActivity({ trace, live = false }: {
  trace: AgentTrace;
  live?: boolean;
}) {
  const hasError = trace.steps.some((step) => step.status === "error");
  const running = trace.steps.findLast((step) => step.status === "running");
  const title = live
    ? running?.label ?? "Agent is working"
    : hasError
      ? "Agent run failed"
      : trace.fallback
        ? "Fallback used"
        : "Agent activity";
  const context = [trace.provider, trace.model,
    trace.batchId ? `batch ${trace.batchId}` : null].filter(Boolean).join(" · ");

  return (
    <details className={`agent-activity${live ? " is-live" : ""}`
                      + `${trace.fallback ? " is-fallback" : ""}`
                      + `${hasError ? " has-error" : ""}`}
             open={live || undefined}>
      <summary>
        <span className={`agent-activity-state${hasError ? " is-error" : ""}`}
              aria-hidden />
        <span className="agent-activity-summary">
          <strong>{title}</strong>
          <span>{context || `${trace.steps.length} execution steps`}</span>
        </span>
        <span className="agent-activity-toggle" aria-hidden>⌄</span>
      </summary>
      <div className="agent-activity-body">
        <div className="agent-query">
          <span>Submitted query</span>
          <code>{trace.query}</code>
        </div>
        <ol className="agent-steps">
          {trace.steps.map((step) => (
            <li key={step.id} className={`agent-step is-${step.status}`}>
              <span className="agent-step-marker" aria-hidden />
              <div>
                <strong>{step.label}</strong>
                {step.detail && (
                  step.technical
                    ? <code className="agent-step-detail">{step.detail}</code>
                    : <p>{step.detail}</p>
                )}
                {step.result && <p className="agent-step-result">{step.result}</p>}
              </div>
            </li>
          ))}
        </ol>
      </div>
    </details>
  );
}
