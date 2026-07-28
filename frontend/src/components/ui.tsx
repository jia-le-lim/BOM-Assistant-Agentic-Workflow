"use client";

import type { Recommendation, Status } from "@/lib/types";

/* ---------------- status & risk chips (icon + label, never colour alone) ---- */

const STATUS_META: Record<Status, { label: string; icon: string; color: string }> = {
  auto_cleared:    { label: "Auto-cleared",    icon: "✓", color: "var(--good)" },
  pending_review:  { label: "Pending review",  icon: "◷", color: "var(--warning)" },
  awaiting_senior: { label: "Awaiting senior", icon: "⇧", color: "var(--serious)" },
  reviewed:        { label: "Reviewed",        icon: "●", color: "var(--seq)" },
};

export function StatusChip({ status }: { status: Status }) {
  const m = STATUS_META[status];
  return (
    <span className="inline-flex items-center gap-1.5 text-xs whitespace-nowrap">
      <span aria-hidden style={{ color: m.color }}>{m.icon}</span>
      <span style={{ color: "var(--text-secondary)" }}>{m.label}</span>
    </span>
  );
}

const RISK_META = {
  Low:    { icon: "○", color: "var(--good)" },
  Medium: { icon: "◐", color: "var(--warning)" },
  High:   { icon: "▲", color: "var(--critical)" },
} as const;

export function RiskChip({ level }: { level: Recommendation["risk_level"] }) {
  const m = RISK_META[level];
  return (
    <span className="inline-flex items-center gap-1.5 text-xs whitespace-nowrap">
      <span aria-hidden style={{ color: m.color }}>{m.icon}</span>
      <span style={{ color: "var(--text-secondary)" }}>{level}</span>
    </span>
  );
}

export function ActionChip({ action }: { action: Recommendation["action"] }) {
  const icon = action === "Increase" ? "↑" : action === "Decrease" ? "↓" : "=";
  return (
    <span className="inline-flex items-center gap-1.5 text-xs whitespace-nowrap">
      <span aria-hidden style={{ color: "var(--text-muted)" }}>{icon}</span>
      <span style={{ color: "var(--text-secondary)" }}>{action}</span>
    </span>
  );
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
  kind: "error" | "info" | "success"; children: React.ReactNode;
}) {
  const color = kind === "error" ? "var(--critical)"
              : kind === "success" ? "var(--success-text)" : "var(--seq)";
  const icon = kind === "error" ? "✕" : kind === "success" ? "✓" : "ℹ";
  return (
    <div className="card p-3 text-sm flex gap-2 items-start"
         style={{ borderColor: color }}>
      <span aria-hidden style={{ color }}>{icon}</span>
      <div style={{ color: "var(--text-secondary)" }}>{children}</div>
    </div>
  );
}

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return <div className="text-sm py-8" style={{ color: "var(--text-muted)" }}>{label}</div>;
}
