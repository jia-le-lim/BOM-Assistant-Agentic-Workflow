"use client";

import type { AssistVerdict, Recommendation, Status, TriageTier } from "@/lib/types";

/* ---------------- status & risk chips (icon + label, never colour alone) ---- */

const STATUS_META: Record<Status, { label: string; icon: string; color: string }> = {
  auto_cleared:    { label: "Auto-cleared",    icon: "✓", color: "var(--good)" },
  pending_review:  { label: "Pending review",  icon: "◷", color: "var(--warning)" },
  awaiting_senior: { label: "Awaiting senior", icon: "⇧", color: "var(--serious)" },
  reviewed:        { label: "Reviewed",        icon: "●", color: "var(--seq)" },
};

/** Every chip here is a coloured glyph beside a neutral label. One component,
 *  so "never colour alone" cannot be forgotten in one of four near-identical
 *  copies, and a change to the shape lands on all of them at once.
 *
 *  `compact` drops the visible label for dense tables. The glyph differs per
 *  state and the label stays in the tooltip AND in screen-reader text, so the
 *  compact form still never carries meaning in colour alone. */
function Chip({ icon, color, label, compact }: {
  icon: string; color: string; label: string; compact?: boolean;
}) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs whitespace-nowrap"
          title={compact ? label : undefined}>
      <span aria-hidden style={{ color }}>{icon}</span>
      <span className={compact ? "sr-only" : undefined}
            style={compact ? undefined : { color: "var(--text-secondary)" }}>{label}</span>
    </span>
  );
}

export function StatusChip({ status, compact }: { status: Status; compact?: boolean }) {
  return <Chip {...STATUS_META[status]} compact={compact} />;
}

const RISK_META = {
  Low:    { icon: "○", color: "var(--good)" },
  Medium: { icon: "◐", color: "var(--warning)" },
  High:   { icon: "▲", color: "var(--critical)" },
} as const;

export function RiskChip({ level, compact }: {
  level: Recommendation["risk_level"]; compact?: boolean;
}) {
  return <Chip {...RISK_META[level]} label={`${level} risk`} compact={compact} />;
}

export function ActionChip({ action }: { action: Recommendation["action"] }) {
  const icon = action === "Increase" ? "↑" : action === "Decrease" ? "↓" : "=";
  return <Chip icon={icon} color="var(--text-muted)" label={action} />;
}

const TRIAGE_META: Record<TriageTier, { label: string; icon: string; color: string }> = {
  clear_candidate: { label: "Clear candidate", icon: "✓", color: "var(--good)" },
  review: { label: "Review", icon: "◐", color: "var(--warning)" },
  escalate: { label: "Escalate", icon: "▲", color: "var(--critical)" },
};

export function TriageChip({ tier }: { tier: TriageTier }) {
  return <Chip {...TRIAGE_META[tier]} />;
}

/* ---------------- PRD v3 statistical signals -------------------------------- */

const CONSUMABLE_META: Record<string, { label: string; color: string }> = {
  constant: { label: "Constant",  color: "var(--seq)" },
  sporadic: { label: "Sporadic",  color: "var(--warning)" },
  dying:    { label: "Dying",     color: "var(--serious)" },
  none:     { label: "Dormant",   color: "var(--text-muted)" },
};

export function ConsumableChip({ value }: { value: string }) {
  const m = CONSUMABLE_META[value] ?? { label: value || "—", color: "var(--text-muted)" };
  return (
    <span className="text-[11px] px-1.5 py-0.5 rounded whitespace-nowrap"
          style={{ background: "var(--seq-soft)", color: m.color }}>
      {m.label}
    </span>
  );
}

const AGREEMENT_META: Record<string, { label: string; icon: string; color: string }> = {
  match:   { label: "Matches engineer", icon: "≈", color: "var(--good)" },
  diverge: { label: "Diverges",         icon: "≠", color: "var(--critical)" },
};

/** A brand-new month has no factory_recommended_* to grade against, so the
 *  engine falls back to this part's last engineer decision. Say which, or the
 *  chip claims an authority it does not have. */
const PRIOR_LABEL: Record<string, string> = {
  match:   "Matches last review",
  diverge: "Diverges from last review",
};

/** Engine vs whichever benchmark the ladder picked (PRD v3 §6). */
export function AgreementChip({ value, source, compact }: {
  value: string; source?: string; compact?: boolean;
}) {
  const m = AGREEMENT_META[value];
  if (!m) return null;
  const label = (source === "prior_review" && PRIOR_LABEL[value]) || m.label;
  return <Chip icon={m.icon} color={m.color} label={label} compact={compact} />;
}

/* What each assist verdict tells the reviewer, in the order attention should
 * go. The chain decides these deterministically (backend/app/assist/rules.py);
 * the pages only label them -- and they label them the same way, which is why
 * this sits here rather than in one of the two. */
export const ASSIST_VERDICT: Record<AssistVerdict, { label: string; hint: string }> = {
  flag_for_review: { label: "Needs review", hint: "The engine's history on this part argues against taking it as read" },
  needs_context: { label: "No prior cycle", hint: "Never reviewed before — nothing to check the engine against" },
  bulk_accept_candidate: { label: "Bulk accept", hint: "Matched the last cycles and the engine has not moved off the accepted value" },
};

const ASSIST_META: Record<AssistVerdict, { icon: string; color: string }> = {
  flag_for_review: { icon: "◐", color: "var(--warning)" },
  needs_context: { icon: "?", color: "var(--text-muted)" },
  bulk_accept_candidate: { icon: "✓", color: "var(--good)" },
};

export function AssistChip({ verdict, compact }: {
  verdict: AssistVerdict; compact?: boolean;
}) {
  return <Chip {...ASSIST_META[verdict]} label={ASSIST_VERDICT[verdict].label}
                compact={compact} />;
}

export function ReasonCodes({ codes, max = 3 }: { codes: string; max?: number }) {
  const list = codes.split(",").filter(Boolean);
  const shown = list.slice(0, max);
  return (
    <span className="flex flex-wrap gap-1">
      {shown.map((c) => (
        <span key={c} className="text-[11px] px-1.5 py-0.5 rounded"
              style={{ background: "var(--seq-soft)", color: "var(--text-primary)" }}>
          {c}
        </span>
      ))}
      {list.length > max && (
        <span className="text-[11px] px-1 py-0.5" style={{ color: "var(--text-muted)" }}>
          +{list.length - max}
        </span>
      )}
    </span>
  );
}

/* ---------------- stat tile (a headline number is not a chart) ------------- */

export function StatTile({ label, value, sub, accent }: {
  label: string; value: string | number; sub?: string; accent?: string;
}) {
  return (
    <div className="card p-4">
      <div className="text-xs mb-1.5" style={{ color: "var(--text-secondary)" }}>{label}</div>
      <div className="text-3xl leading-none" style={{ color: accent ?? "var(--text-primary)" }}>
        {typeof value === "number" ? value.toLocaleString() : value}
      </div>
      {sub && <div className="text-xs mt-1.5" style={{ color: "var(--text-muted)" }}>{sub}</div>}
    </div>
  );
}

export function Banner({ kind, children }: {
  kind: "error" | "info" | "success" | "warning"; children: React.ReactNode;
}) {
  const color = kind === "error" ? "var(--critical)"
              : kind === "success" ? "var(--success-text)"
              : kind === "warning" ? "var(--warning)" : "var(--seq)";
  const icon = kind === "error" ? "✕" : kind === "success" ? "✓"
             : kind === "warning" ? "⚠" : "ℹ";
  return (
    <div className="card p-3 text-sm flex gap-2 items-start"
         style={{ borderColor: color }}>
      <span aria-hidden style={{ color }}>{icon}</span>
      <div style={{ color: "var(--text-secondary)" }}>{children}</div>
    </div>
  );
}

/* ---------------- loading surfaces ----------------------------------------
 * A wait needs a shape that matches it. A short fetch with a known layout gets
 * a skeleton of that layout, so the page never collapses and never jumps when
 * the data lands. A single action gets a spinner inside its own button. Long
 * work with no measurable percentage gets an indeterminate bar -- never a fake
 * one. Long work with real phases streams them (see RunStream).
 * ------------------------------------------------------------------------ */

/** One placeholder block. `w`/`h` are any CSS length. */
export function Skeleton({ w = "100%", h = "0.72rem", className = "" }: {
  w?: string; h?: string; className?: string;
}) {
  return <span className={`skeleton block ${className}`} style={{ width: w, height: h }} aria-hidden />;
}

/** Placeholder rows sized like real table rows, so the table keeps its height. */
export function TableSkeleton({ rows = 6, cols = 5, label = "Loading rows" }: {
  rows?: number; cols?: number; label?: string;
}) {
  return (
    <div role="status" aria-busy="true" aria-label={label}>
      <span className="sr-only">{label}…</span>
      {Array.from({ length: rows }, (_, r) => (
        <div key={r} className="skeleton-row">
          {Array.from({ length: cols }, (_, c) => <Skeleton key={c} />)}
        </div>
      ))}
    </div>
  );
}

/** Placeholder for a card whose contents are not a table. */
export function CardSkeleton({ lines = 3, title = true, label = "Loading" }: {
  lines?: number; title?: boolean; label?: string;
}) {
  return (
    <div className="card p-5 skeleton-stack" role="status" aria-busy="true" aria-label={label}>
      <span className="sr-only">{label}…</span>
      {title && <Skeleton w="34%" h="0.85rem" />}
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} w={i === lines - 1 ? "62%" : "100%"} />
      ))}
    </div>
  );
}

/** Indeterminate only. We know it is running; we do not know how far. */
export function Progress({ label }: { label?: string }) {
  return (
    <div role="status" aria-busy="true" aria-live="polite">
      {label && (
        <div className="text-xs mb-1.5" style={{ color: "var(--text-secondary)" }}>{label}</div>
      )}
      <div className="progress-track"><i /></div>
    </div>
  );
}

/** Spinner glyph for inside a button. The label stays; only the glyph is new. */
export function Spin() {
  return <span className="spin" aria-hidden />;
}

/**
 * The button label during a run. Keeping the verb visible ("Running engine…"
 * rather than a generic "Working…") is what tells the engineer which of the
 * several long actions on this page is the one they are waiting for.
 */
export function BusyLabel({ busy, idle, running }: {
  busy: boolean; idle: React.ReactNode; running: string;
}) {
  return busy ? <><Spin />{running}</> : <>{idle}</>;
}

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="text-sm py-8 flex items-center gap-2" role="status" aria-busy="true"
         style={{ color: "var(--text-muted)" }}>
      <Spin /> {label}
    </div>
  );
}
