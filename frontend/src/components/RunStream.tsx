"use client";

/**
 * Live phase list for a long backend run.
 *
 * Triage spends 3 to 7 model calls on a row -- tens of seconds during which the
 * old console showed a disabled button and nothing else, which reads as a hung
 * page. The backend now streams the graph's own phases, so this component shows
 * work that actually happened: announced up front, ticked off as each node
 * finishes. Nothing here is a timer or a guessed percentage.
 */

import { Spin } from "@/components/ui";

export interface RunPhase { node: string; label: string }

export interface RunStreamState {
  title: string;
  phases: RunPhase[];
  done: string[];
  detail?: string;
  error?: string;
  finished: boolean;
}

/** Triage stream events, as emitted by POST /triage/run/stream. */
export type TriageStreamEvent =
  | { type: "start"; batch_id: number; candidates: number; item_id: string | null }
  | { type: "item_start"; item_id: string; stockroom_id: string; phases: RunPhase[] }
  | { type: "phase"; node: string; label: string }
  | { type: "item_done"; item_id: string; stockroom_id: string; triage_tier: string;
      priority_score: number; confidence: number; llm_calls_used: number }
  | { type: "complete"; summary: { triaged: number; llm_calls_used: number } }
  | { type: "error"; message: string };

/** Fold one streamed event into the panel state. */
export function reduceTriageStream(
  state: RunStreamState, event: TriageStreamEvent,
): RunStreamState {
  switch (event.type) {
    case "start":
      return { ...state, detail: event.candidates === 1 ? "1 row" : `${event.candidates} rows` };
    case "item_start":
      return { ...state, phases: event.phases, done: [] };
    case "phase":
      return state.done.includes(event.node)
        ? state
        : { ...state, done: [...state.done, event.node] };
    case "item_done":
      return {
        ...state,
        detail: `${event.triage_tier.replace("_", " ")} · ${Math.round(event.confidence * 100)}%`
                + ` confidence · ${event.llm_calls_used} model call`
                + (event.llm_calls_used === 1 ? "" : "s"),
      };
    case "complete":
      return { ...state, finished: true };
    case "error":
      return { ...state, finished: true, error: event.message };
  }
}

export function RunStream({ state }: { state: RunStreamState }) {
  const live = !state.finished && !state.error;
  // Only the earliest unfinished phase is called running. Under-reporting a
  // parallel pair is honest; claiming a phase finished before it did is not.
  const running = state.phases.find((p) => !state.done.includes(p.node))?.node;

  return (
    <section className="run-stream" aria-live="polite" aria-busy={live}>
      <div className="run-stream-head">
        {live ? <Spin /> : <span aria-hidden style={{ color: state.error ? "var(--critical)" : "var(--good)" }}>
          {state.error ? "✕" : "✓"}</span>}
        <strong>{state.title}</strong>
        {state.detail && <span>{state.detail}</span>}
      </div>

      {state.phases.length === 0 ? (
        <div className="progress-track"><i /></div>
      ) : (
        <ol className="agent-steps">
          {state.phases.map((p) => {
            const status = state.done.includes(p.node) ? "done"
                         : p.node === running && live ? "running"
                         : "";
            return (
              <li key={p.node} className={`agent-step${status ? ` is-${status}` : ""}`}>
                <span className="agent-step-marker" aria-hidden />
                <div><strong>{p.label}</strong></div>
              </li>
            );
          })}
        </ol>
      )}

      {state.error && (
        <p className="text-xs mt-2" style={{ color: "var(--critical)" }}>{state.error}</p>
      )}
    </section>
  );
}

export const emptyRunStream = (title: string): RunStreamState => ({
  title, phases: [], done: [], finished: false,
});
