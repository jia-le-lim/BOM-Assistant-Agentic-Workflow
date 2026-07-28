"use client";

import { useCallback, useEffect, useState } from "react";
import { useApi } from "@/lib/api";
import { can, useSession } from "@/lib/session";
import type { RuleConfig } from "@/lib/types";
import { Banner, Spinner } from "@/components/ui";

const EDITABLE = [
  ["long_lead_time_threshold", "Long lead time (days)", "Workload/safety dial: 30 → 47% review & 7.6% miss; 60 → 38% review & 10.7% miss"],
  ["zero_stock_risk_clt", "Zero-stock risk lead time (days)", "Dormant parts at/above this lead time are carved out of auto-clear"],
  ["high_cost_threshold", "High cost ($)", "Override rate roughly doubles above ~$1,500"],
  ["low_cost_threshold", "Low cost ($)", "Rule 1 low-cost recurring usage"],
  ["recent_usage_days", "Recent usage (days)", ""],
  ["min_usage_months", "Min usage months", ""],
  ["max_change_pct_review", "Max change before review", "0.5 = +50%"],
  ["value_gate_usd", "Value gate ($)", "Exposure at/above this always reaches a human"],
  ["min_protective_stock", "Protective floor (units)", "Applied when the source algorithm says 0 but risk exists"],
] as const;

export default function ConfigPage() {
  const { call } = useApi();
  const { role } = useSession();
  const [cfg, setCfg] = useState<RuleConfig | null>(null);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [version, setVersion] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [pattern, setPattern] = useState("");
  const [crit, setCrit] = useState("High");

  const load = useCallback(async () => {
    try { setCfg(await call<RuleConfig>("config/rules")); setErr(null); }
    catch (e) { setErr((e as Error).message); }
  }, [call]);

  useEffect(() => { load(); }, [load]);

  async function save() {
    setBusy(true); setErr(null); setNote(null);
    try {
      const updates: Record<string, number> = {};
      for (const [k, v] of Object.entries(edits)) {
        if (v !== "" && Number(v) !== Number(cfg?.config[k])) updates[k] = Number(v);
      }
      if (!Object.keys(updates).length) { setErr("No changes to apply."); return; }
      const r = await call<{ rule_version: string }>("config/rules", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rule_version: version, updates }),
      });
      setNote(`Saved as ${r.rule_version}. Re-run the engine on a batch to apply it — ` +
              `existing results keep the version they were scored with.`);
      setEdits({}); setVersion(""); await load();
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  async function propose() {
    setBusy(true); setErr(null); setNote(null);
    try {
      await call("config/criticality", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pattern, criticality: crit }),
      });
      setNote(`Proposed "${pattern}" as ${crit}. It is NOT active until a different ` +
              `person with senior rights confirms it — the engine reads confirmed rows only.`);
      setPattern(""); await load();
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  async function confirm(p: string) {
    setBusy(true); setErr(null); setNote(null);
    try {
      await call(`config/criticality/${encodeURIComponent(p)}/confirm`, { method: "POST" });
      setNote(`Confirmed "${p}" — the engine will use it on the next run.`);
      await load();
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  if (!cfg) return err ? <Banner kind="error">{err}</Banner> : <Spinner />;

  const criticality = (cfg.config.machine_criticality ?? {}) as Record<string, string>;

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold">Rules &amp; criticality</h1>
        <p className="text-sm mt-1" style={{ color: "var(--text-secondary)" }}>
          Thresholds are versioned config, never hard-coded. Active version:{" "}
          <code>{cfg.rule_version}</code>
        </p>
      </div>

      {err && <Banner kind="error">{err}</Banner>}
      {note && <Banner kind="success">{note}</Banner>}

      <div className="card p-5">
        <h2 className="text-sm font-semibold mb-3">Thresholds</h2>
        <div className="scroll-x">
          <table className="w-full text-sm min-w-[640px]">
            <thead>
              <tr><th>Setting</th><th className="text-right">Active</th>
                  <th className="text-right w-32">New value</th><th>Why it matters</th></tr>
            </thead>
            <tbody>
              {EDITABLE.map(([key, label, hint]) => (
                <tr key={key}>
                  <td>{label}</td>
                  <td className="text-right tnum">{String(cfg.config[key] ?? "—")}</td>
                  <td className="text-right">
                    <input className="field w-28 tnum text-right" type="number"
                           disabled={!can.configWrite(role)}
                           value={edits[key] ?? ""}
                           placeholder={String(cfg.config[key] ?? "")}
                           onChange={(e) => setEdits({ ...edits, [key]: e.target.value })} />
                  </td>
                  <td className="text-xs" style={{ color: "var(--text-muted)" }}>{hint}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="flex flex-wrap gap-3 items-end mt-4">
          <label className="flex flex-col gap-1 text-xs">
            <span style={{ color: "var(--text-secondary)" }}>New rule version (required)</span>
            <input className="field" value={version} placeholder="e.g. 0.2.1-tcb"
                   disabled={!can.configWrite(role)}
                   onChange={(e) => setVersion(e.target.value)} />
          </label>
          <button className="btn btn-primary" onClick={save}
                  disabled={busy || !can.configWrite(role) || !version}>
            Save as new version
          </button>
        </div>
        <p className="text-xs mt-2" style={{ color: "var(--text-muted)" }}>
          {can.configWrite(role)
            ? "The version must change whenever config changes — that is what makes a past result reproducible."
            : `Role ${role} cannot edit thresholds. Switch to admin.`}
        </p>
      </div>

      <div className="card p-5">
        <h2 className="text-sm font-semibold mb-1">Machine criticality</h2>
        <p className="text-xs mb-4" style={{ color: "var(--text-muted)" }}>
          Not derivable from the data — engineers own it. Anyone with review rights may
          propose; a <strong>different</strong> person with senior rights must confirm.
          The engine reads confirmed entries only, so an assistant can capture intent
          but never silently change what the engine does.
        </p>

        {Object.keys(criticality).length > 0 && (
          <div className="mb-4">
            <div className="text-xs mb-2" style={{ color: "var(--text-secondary)" }}>
              Confirmed &amp; active
            </div>
            <div className="flex flex-wrap gap-2">
              {Object.entries(criticality).map(([p, c]) => (
                <span key={p} className="text-xs px-2 py-1 rounded"
                      style={{ background: "var(--seq-soft)" }}>
                  <span aria-hidden style={{ color: "var(--success-text)" }}>✓ </span>
                  {p} → {c}
                </span>
              ))}
            </div>
          </div>
        )}

        <div className="flex flex-wrap gap-3 items-end">
          <label className="flex flex-col gap-1 text-xs">
            <span style={{ color: "var(--text-secondary)" }}>machine_type contains</span>
            <input className="field" value={pattern} placeholder="e.g. KnS TCX3"
                   onChange={(e) => setPattern(e.target.value)}
                   disabled={!can.review(role)} />
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span style={{ color: "var(--text-secondary)" }}>Criticality</span>
            <select className="field" value={crit} onChange={(e) => setCrit(e.target.value)}
                    disabled={!can.review(role)}>
              <option>High</option><option>Medium</option><option>Low</option>
            </select>
          </label>
          <button className="btn" onClick={propose}
                  disabled={busy || !pattern || !can.review(role)}>Propose</button>
          <button className="btn" onClick={() => confirm(pattern)}
                  disabled={busy || !pattern || !can.approve(role)}
                  title={can.approve(role) ? "Confirm this pattern" : "Needs senior or admin"}>
            Confirm as senior
          </button>
        </div>
      </div>
    </div>
  );
}
