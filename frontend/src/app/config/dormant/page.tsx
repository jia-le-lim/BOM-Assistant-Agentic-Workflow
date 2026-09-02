"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { fmtUsd, useApi } from "@/lib/api";
import { can, useSession } from "@/lib/session";
import type { DormantRule, DormantRuleCoverage, DormantRulePage } from "@/lib/types";
import { Banner, Spinner } from "@/components/ui";

/* Dormant stocking rules. The engine sizes a part with no consumption in any
 * window to zero; over eight review cycles engineers overrode that on 1,344 of
 * 8,343 dormant rows. This page is where that decision lives, so it is a rule
 * with an owner and a date rather than 1,344 repeated manual edits. */

const POLICIES = [
  ["hold_current", "Hold current", "Keep whatever the part is stocked at today. Declines when the row has no current level — a missing number is a gap in the extract, not a decision to stock nothing."],
  ["fixed_qty", "Fixed quantity", "A named quantity for everything this rule matches."],
  ["zero", "Zero", "The engine's own answer, stated explicitly, so a category can be switched back off without deleting the rule."],
] as const;

const CRITICALITY = [
  ["", "Any"], ["h", "High"], ["m", "Medium"], ["l", "Low"],
] as const;

export default function DormantRulesPage() {
  const { call } = useApi();
  const { role } = useSession();
  const [page, setPage] = useState<DormantRulePage | null>(null);
  const [coverage, setCoverage] = useState<DormantRuleCoverage | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [scope, setScope] = useState<DormantRule["scope"]>("category");
  const [matchKey, setMatchKey] = useState("");
  const [criticality, setCriticality] = useState("");
  const [policy, setPolicy] = useState<DormantRule["policy"]>("hold_current");
  const [qty, setQty] = useState("");
  const [priority, setPriority] = useState("500");
  const [batchId, setBatchId] = useState("");

  const load = useCallback(async () => {
    try { setPage(await call<DormantRulePage>("config/dormant-rules")); setErr(null); }
    catch (e) { setErr((e as Error).message); }
  }, [call]);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => { void load(); });
    return () => window.cancelAnimationFrame(frame);
  }, [load]);

  async function checkCoverage() {
    if (!batchId) return;
    setBusy(true); setErr(null); setNote(null);
    try {
      setCoverage(await call<DormantRuleCoverage>(
        `config/dormant-rules/coverage?batch_id=${Number(batchId)}`));
    } catch (e) { setErr((e as Error).message); setCoverage(null); }
    finally { setBusy(false); }
  }

  async function propose() {
    setBusy(true); setErr(null); setNote(null);
    try {
      await call("config/dormant-rules", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          scope, match_key: scope === "default" ? "" : matchKey.trim(),
          criticality, policy,
          fixed_qty: policy === "fixed_qty" ? Number(qty) : null,
          priority: Number(priority),
        }),
      });
      setNote("Proposed. It sizes nothing until a different person with senior " +
              "rights confirms it, and it applies from the next engine run.");
      setMatchKey(""); setQty(""); await load();
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  async function confirm(ruleId: number) {
    setBusy(true); setErr(null); setNote(null);
    try {
      await call(`config/dormant-rules/${ruleId}/confirm`, { method: "POST" });
      setNote("Confirmed. Re-run the engine on a batch to apply it — rules are " +
              "read at score time, so an open batch is unchanged until then.");
      await load();
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  async function remove(ruleId: number) {
    setBusy(true); setErr(null); setNote(null);
    try {
      await call(`config/dormant-rules/${ruleId}`, { method: "DELETE" });
      setNote("Deleted. The engine falls back to its own sizing for anything " +
              "that rule used to match.");
      await load();
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  if (!page && !err) return <Spinner label="Loading dormant rules…" />;

  return (
    <div className="page flex flex-col gap-6">
      <div className="page-head">
        <Link href="/config" className="text-xs" style={{ color: "var(--text-muted)" }}>
          ← Rules &amp; criticality
        </Link>
        <h1 className="text-xl font-semibold mt-1">Dormant stocking rules</h1>
        <p className="text-sm mt-1" style={{ color: "var(--text-secondary)" }}>
          What a part with no recorded consumption keeps on the shelf. The engine
          would size these to zero; these rules override it.
        </p>
      </div>

      {err && <Banner kind="error">{err}</Banner>}
      {note && <Banner kind="success">{note}</Banner>}

      <Banner kind="info">
        Rules are read when the engine <strong>scores</strong> a batch, not when you
        review it. Editing one here changes the next <code>Run engine</code>; a batch
        already on screen keeps the numbers its reviewer saw.
      </Banner>

      <div className="card p-5">
        <h2 className="text-sm font-semibold mb-1">Coverage &amp; stock impact</h2>
        <p className="text-xs mb-3" style={{ color: "var(--text-muted)" }}>
          These rules move inventory. Match rate alone is not the decision — the book
          value against what the engine itself proposed is the other half of it.
        </p>
        <div className="flex flex-wrap gap-3 items-end">
          <label className="flex flex-col gap-1 text-xs">
            <span style={{ color: "var(--text-secondary)" }}>Scored batch</span>
            <input className="field w-28 tnum" type="number" value={batchId}
                   placeholder="13" onChange={(e) => setBatchId(e.target.value)} />
          </label>
          <button className="btn" onClick={checkCoverage}
                  disabled={busy || !batchId || !can.review(role)}>Check</button>
        </div>
        {coverage && (
          <p className="text-sm mt-3">
            {coverage.matched.toLocaleString()} of {coverage.dormant_rows.toLocaleString()}{" "}
            dormant rows matched ({coverage.pct}%) by {coverage.confirmed_rules} confirmed{" "}
            {coverage.confirmed_rules === 1 ? "rule" : "rules"}. Proposed book{" "}
            {fmtUsd(coverage.proposed_book_usd)} versus {fmtUsd(coverage.engine_book_usd)}{" "}
            from the engine —{" "}
            <strong style={{ color: coverage.delta_usd > 0 ? "var(--warning)" : undefined }}>
              {coverage.delta_usd >= 0 ? "+" : ""}{fmtUsd(coverage.delta_usd)}
            </strong>.
          </p>
        )}
      </div>

      <div className="card p-5">
        <h2 className="text-sm font-semibold mb-1">Rules</h2>
        <p className="text-xs mb-3" style={{ color: "var(--text-muted)" }}>
          Lower priority wins; within a priority an item rule beats a category rule
          beats the default, so a per-part exception needs no renumbering. Same
          two-person rule as criticality: propose, then a <strong>different</strong>{" "}
          senior confirms.
          {page && ` ${page.confirmed} confirmed · ${page.pending} pending.`}
        </p>

        {page && page.rules.length > 0 && (
          <div className="scroll-x mb-4">
            <table className="w-full text-sm min-w-[720px]">
              <thead>
                <tr>
                  <th className="text-right">Pri</th>
                  <th className="text-left">Scope</th>
                  <th className="text-left">Matches</th>
                  <th className="text-left">Criticality</th>
                  <th className="text-left">Policy</th>
                  <th className="text-right">Qty</th>
                  <th className="text-left">Status</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {page.rules.map((r) => (
                  <tr key={r.rule_id}>
                    <td className="text-right tnum">{r.priority}</td>
                    <td>{r.scope}</td>
                    <td className="font-mono text-xs">{r.match_key || "—"}</td>
                    <td>{CRITICALITY.find(([v]) => v === r.criticality)?.[1] ?? r.criticality}</td>
                    <td>{POLICIES.find(([v]) => v === r.policy)?.[1] ?? r.policy}</td>
                    <td className="text-right tnum">{r.fixed_qty ?? "—"}</td>
                    <td className="text-xs" style={{ color: "var(--text-secondary)" }}>
                      {r.confirmed
                        ? <span><span aria-hidden style={{ color: "var(--success-text)" }}>✓ </span>
                            active{r.confirmed_by ? ` (${r.confirmed_by})` : ""}</span>
                        : <span><span aria-hidden style={{ color: "var(--warning)" }}>◷ </span>
                            pending{r.set_by ? ` (${r.set_by})` : ""}</span>}
                    </td>
                    <td className="text-right">
                      <div className="flex gap-1.5 justify-end">
                        {!r.confirmed && (
                          <button className="btn text-xs" disabled={busy || !can.approve(role)}
                                  onClick={() => confirm(r.rule_id)}>Confirm</button>
                        )}
                        <button className="btn text-xs" disabled={busy || !can.approve(role)}
                                onClick={() => remove(r.rule_id)}>Delete</button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="flex flex-wrap gap-3 items-end">
          <label className="flex flex-col gap-1 text-xs">
            <span style={{ color: "var(--text-secondary)" }}>Scope</span>
            <select className="field" value={scope} disabled={!can.review(role)}
                    onChange={(e) => setScope(e.target.value as DormantRule["scope"])}>
              <option value="category">category</option>
              <option value="item">item</option>
              <option value="default">default</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span style={{ color: "var(--text-secondary)" }}>
              {scope === "item" ? "Item id" : scope === "category" ? "Category" : "—"}
            </span>
            <input className="field w-40 font-mono" value={matchKey}
                   disabled={!can.review(role) || scope === "default"}
                   placeholder={scope === "item" ? "500699364" : "filter"}
                   onChange={(e) => setMatchKey(e.target.value)} />
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span style={{ color: "var(--text-secondary)" }}>Criticality</span>
            <select className="field" value={criticality} disabled={!can.review(role)}
                    onChange={(e) => setCriticality(e.target.value)}>
              {CRITICALITY.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span style={{ color: "var(--text-secondary)" }}>Policy</span>
            <select className="field" value={policy} disabled={!can.review(role)}
                    onChange={(e) => setPolicy(e.target.value as DormantRule["policy"])}>
              {POLICIES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span style={{ color: "var(--text-secondary)" }}>Quantity</span>
            <input className="field w-20 tnum" type="number" min={0} max={10000}
                   value={qty} disabled={!can.review(role) || policy !== "fixed_qty"}
                   onChange={(e) => setQty(e.target.value)} />
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span style={{ color: "var(--text-secondary)" }}>Priority</span>
            <input className="field w-20 tnum" type="number" min={1} max={9999}
                   value={priority} disabled={!can.review(role)}
                   onChange={(e) => setPriority(e.target.value)} />
          </label>
          <button className="btn" onClick={propose}
                  disabled={busy || !can.review(role)
                            || (scope !== "default" && !matchKey.trim())
                            || (policy === "fixed_qty" && qty === "")}>
            Propose
          </button>
        </div>
        <p className="text-xs mt-3" style={{ color: "var(--text-muted)" }}>
          {POLICIES.find(([v]) => v === policy)?.[2]}
        </p>
      </div>
    </div>
  );
}
