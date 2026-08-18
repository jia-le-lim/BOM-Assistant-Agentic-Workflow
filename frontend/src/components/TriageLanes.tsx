"use client";

/**
 * Triage lanes: the review queue grouped by PRD v3 consumable class.
 *
 * Constant consumers are the engineer's stated priority, so that lane leads.
 * Each lane offers a single bulk action -- accept every high-confidence,
 * non-diverging, non-High-risk row in the class -- which is the lever that
 * turns thousands of individual clicks into a handful of decisions.
 */

import { fmtCompact } from "@/lib/api";
import type { BatchSummary } from "@/lib/types";

const LANES: { key: string; label: string; blurb: string }[] = [
  { key: "constant", label: "Constant consumers", blurb: "steady demand — priority review" },
  { key: "sporadic", label: "Sporadic",           blurb: "recent-only demand" },
  { key: "dying",    label: "Dying",              blurb: "demand has ceased" },
  { key: "none",     label: "Dormant / no data",  blurb: "insurance or flagged" },
];

export function TriageLanes({ summary, busy, canReview, onAcceptLane }: {
  summary: BatchSummary;
  busy: boolean;
  canReview: boolean;
  onAcceptLane: (consumable: string) => void;
}) {
  const lanes = LANES.filter((l) => (summary.consumables[l.key] ?? 0) > 0);
  if (!lanes.length) return null;

  return (
    <div className="card p-5">
      <div className="flex items-baseline justify-between mb-1">
        <h2 className="text-sm font-semibold">Triage by demand pattern</h2>
        <span className="text-xs" style={{ color: "var(--text-muted)" }}>
          {summary.bulk_acceptable.toLocaleString()} rows safe to bulk-accept
        </span>
      </div>
      <p className="text-xs mb-4" style={{ color: "var(--text-muted)" }}>
        &ldquo;Accept safe&rdquo; clears the high-confidence agreements the engine is sure of
        (excludes High-risk and rows that diverge from the engineer&rsquo;s own number).
      </p>
      <div className="flex flex-col gap-2.5">
        {lanes.map((l) => (
          <div key={l.key}
               className="flex items-center justify-between gap-3 py-2"
               style={{ borderTop: "1px solid var(--gridline)" }}>
            <div>
              <div className="text-sm font-medium">{l.label}</div>
              <div className="text-[11px]" style={{ color: "var(--text-muted)" }}>{l.blurb}</div>
            </div>
            <div className="flex items-center gap-3">
              <span className="text-sm tnum" style={{ color: "var(--text-secondary)" }}>
                {(summary.consumables[l.key] ?? 0).toLocaleString()}
              </span>
              <button className="btn text-xs" disabled={busy || !canReview}
                      onClick={() => onAcceptLane(l.key)}
                      title={canReview ? "Accept safe agreements in this lane"
                                       : "Needs engineer, senior or admin role"}>
                Accept safe
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/** Exposure Pareto callout — how few rows carry the money. */
export function PriorityCallout({ summary }: { summary: BatchSummary }) {
  const { items_for_80pct, top100_coverage_pct } = summary.pareto;
  if (!summary.exposure_total_usd) return null;
  return (
    <div className="card p-4" style={{ borderColor: "var(--seq)" }}>
      <div className="text-sm">
        <strong>{items_for_80pct.toLocaleString()}</strong> items carry 80% of the{" "}
        <strong>{fmtCompact(summary.exposure_total_usd)}</strong> exposure — the top 100 alone
        cover <strong>{top100_coverage_pct}%</strong>. Work the queue top-down; bulk-accept the rest.
      </div>
    </div>
  );
}
