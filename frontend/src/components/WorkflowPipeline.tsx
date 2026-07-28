"use client";

/**
 * The workflow funnel: how a batch's rows distribute across the approval chain.
 *
 * Form: ordinal stepped bar (discrete ordered stages), not a categorical chart --
 * the stages ARE an order, so a single-hue ordinal ramp carries it. Ramp validated
 * light+dark; light end clears 2:1 against the surface.
 * Every stage carries a visible label + count, so colour never carries meaning alone.
 */

import type { Status } from "@/lib/types";

const STAGES: { key: Status; label: string; ramp: string; note: string }[] = [
  { key: "auto_cleared",    label: "Auto-cleared",    ramp: "var(--stage-1)", note: "no change, no risk flag — never exported" },
  { key: "pending_review",  label: "Pending review",  ramp: "var(--stage-2)", note: "awaiting an engineer" },
  { key: "awaiting_senior", label: "Awaiting senior", ramp: "var(--stage-3)", note: "override or High risk — needs a second person" },
  { key: "reviewed",        label: "Reviewed",        ramp: "var(--stage-4)", note: "final — eligible for export" },
];

export function WorkflowPipeline({ counts, exported }: {
  counts: Record<Status, number>;
  exported?: number;
}) {
  const rows = [
    ...STAGES.map((s) => ({ ...s, value: counts[s.key] ?? 0 })),
    ...(exported === undefined ? [] : [{
      key: "exported" as const, label: "In WINGS export", ramp: "var(--stage-5)",
      note: "values differ from current and fully approved", value: exported,
    }]),
  ];
  const max = Math.max(...rows.map((r) => r.value), 1);
  const total = STAGES.reduce((a, s) => a + (counts[s.key] ?? 0), 0);

  return (
    <div className="card p-5">
      <h2 className="text-sm font-semibold mb-1">Approval pipeline</h2>
      <p className="text-xs mb-4" style={{ color: "var(--text-muted)" }}>
        {total.toLocaleString()} scored rows. Nothing reaches WINGS without human approval.
      </p>
      <div className="flex flex-col gap-3">
        {rows.map((r) => {
          const pct = total ? (r.value / total) * 100 : 0;
          return (
            <div key={r.key}>
              <div className="flex items-baseline justify-between gap-3 mb-1">
                <span className="text-sm">{r.label}</span>
                <span className="text-sm tnum" style={{ color: "var(--text-secondary)" }}>
                  {r.value.toLocaleString()}
                  <span className="text-xs ml-1.5" style={{ color: "var(--text-muted)" }}>
                    {pct.toFixed(1)}%
                  </span>
                </span>
              </div>
              <div className="h-2 rounded-full" style={{ background: "var(--gridline)" }}>
                <div
                  className="h-2 rounded-full"
                  style={{ width: `${(r.value / max) * 100}%`, background: r.ramp }}
                  title={`${r.label}: ${r.value.toLocaleString()}`}
                />
              </div>
              <div className="text-[11px] mt-1" style={{ color: "var(--text-muted)" }}>{r.note}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** Horizontal magnitude bars — single hue, sequential (comparison of one measure). */
export function ReasonCodeBars({ codes }: { codes: Record<string, number> }) {
  const entries = Object.entries(codes).sort((a, b) => b[1] - a[1]);
  if (!entries.length) return null;
  const max = Math.max(...entries.map((e) => e[1]));
  return (
    <div className="card p-5">
      <h2 className="text-sm font-semibold mb-1">Why rows were flagged</h2>
      <p className="text-xs mb-4" style={{ color: "var(--text-muted)" }}>
        Reason codes emitted by rule engine — every row carries at least one.
      </p>
      <div className="flex flex-col gap-2.5">
        {entries.map(([code, n]) => (
          <div key={code}>
            <div className="flex items-baseline justify-between gap-3 mb-1">
              <span className="text-xs font-mono">{code}</span>
              <span className="text-xs tnum" style={{ color: "var(--text-secondary)" }}>
                {n.toLocaleString()}
              </span>
            </div>
            <div className="h-1.5 rounded-full" style={{ background: "var(--gridline)" }}>
              <div className="h-1.5 rounded-full"
                   style={{ width: `${(n / max) * 100}%`, background: "var(--seq)" }}
                   title={`${code}: ${n.toLocaleString()}`} />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
