"use client";

/* Entry points into the review workflow, shown on an empty conversation.
 *
 * Two kinds, deliberately: a card either SENDS a question that needs no
 * argument, or FILLS the composer with a stem the engineer completes. Sending
 * "why item ?" on their behalf would spend a turn to be told the id is
 * missing. */

type Icon = "queue" | "explain" | "history" | "propose";

export interface Suggestion {
  id: Icon;
  title: string;
  detail: string;
  prompt: string;
  /** true -> put it in the composer for completion, don't send it */
  fills?: boolean;
  /** staging a proposal is a review action (the backend enforces this too) */
  needsReview?: boolean;
}

export const SUGGESTIONS: Suggestion[] = [
  {
    id: "queue",
    title: "Triage the queue",
    detail: "Highest-exposure items still waiting on a decision.",
    prompt: "top exposure items",
  },
  {
    id: "explain",
    title: "Explain a decision",
    detail: "What the engine proposed for one item, and the reason codes behind it.",
    prompt: "why item ",
    fills: true,
  },
  {
    id: "history",
    title: "Check item history",
    detail: "Past decisions for a part across every batch, not just this month.",
    prompt: "history ",
    fills: true,
  },
  {
    id: "propose",
    title: "Propose a change",
    detail: "State the value yourself. It is staged for confirmation, never applied.",
    prompt: "set item ",
    fills: true,
    needsReview: true,
  },
];

/* Lucide-style strokes. SVG, not emoji: an emoji is a font-dependent picture
   with a screen-reader name nobody chose. */
function Glyph({ name }: { name: Icon }) {
  const common = {
    width: 18, height: 18, viewBox: "0 0 24 24", fill: "none",
    stroke: "currentColor", strokeWidth: 1.6,
    strokeLinecap: "round" as const, strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };
  switch (name) {
    case "queue":     // ranked bars -- exposure, descending
      return <svg {...common}><path d="M4 20V10M10 20V4M16 20v-7M22 20H2" /></svg>;
    case "explain":
      return <svg {...common}><circle cx="12" cy="12" r="9" /><path d="M9.2 9.3a2.9 2.9 0 0 1 5.6 1c0 1.9-2.8 2.4-2.8 4" /><path d="M12 17.5h.01" /></svg>;
    case "history":
      return <svg {...common}><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3.2 2" /></svg>;
    case "propose":
      return <svg {...common}><path d="M4 20h4l10.5-10.5a2.1 2.1 0 0 0-3-3L5 17v3Z" /><path d="M14.5 6.5 17.5 9.5" /></svg>;
  }
}

export function SuggestionCards({ onSend, onFill, canReview, disabled }: {
  onSend: (prompt: string) => void;
  onFill: (stem: string) => void;
  canReview: boolean;
  disabled?: boolean;
}) {
  return (
    <ul className="grid gap-2.5 grid-cols-1 sm:grid-cols-2 stagger-in list-none p-0 m-0">
      {SUGGESTIONS.map((s, i) => {
        const denied = Boolean(s.needsReview && !canReview);
        const why = denied ? "Your role cannot stage changes." : s.detail;
        return (
          <li key={s.id} className="flex" style={{ ["--i" as string]: i }}>
            <button
              type="button"
              className="card card-action suggestion-card w-full h-full text-left"
              disabled={denied || disabled}
              aria-label={`${s.title}. ${why}`}
              onClick={() => (s.fills ? onFill(s.prompt) : onSend(s.prompt))}
            >
              <span className="suggestion-icon">
                <Glyph name={s.id} />
              </span>
              <span className="suggestion-copy">
                <span className="block text-sm font-medium">{s.title}</span>
                <span className="block text-xs mt-1 leading-snug"
                      style={{ color: "var(--text-secondary)" }}>
                  {why}
                </span>
              </span>
              <span className="suggestion-arrow" aria-hidden>→</span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
