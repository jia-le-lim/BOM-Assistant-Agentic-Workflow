"use client";

import { useCallback, useEffect, useState } from "react";
import { fmtUsd, useApi } from "@/lib/api";
import { can, useSession } from "@/lib/session";
import type { DormantRule, DormantRuleCoverage, DormantRulePage, DormantRuleDraft } from "@/lib/types";
import { useAssistantForm } from "@/lib/assistant-context";
import { AssistantDraftTable } from "@/components/AssistantDraftTable";
import { Banner, Spinner } from "@/components/ui";
import { SettingsActions, SettingsRow, SettingsWorkspace } from "@/components/SettingsWorkspace";

/* Dormant stocking rules. The engine sizes a part with no consumption in any
 * window to zero; over eight review cycles engineers overrode that on 1,344 of
 * 8,343 dormant rows. This page is where that decision lives, so it is a rule
 * with an owner and a date rather than 1,344 repeated manual edits. */

const POLICIES = [
  ["hold_current", "Hold current", "Keep whatever the part is stocked at today. Declines when the row has no current level — a missing number is a gap in the extract, not a decision to stock nothing."],
  ["fixed_qty", "Fixed quantity", "A named quantity for everything this rule matches."],
  ["zero", "Zero", "The engine's own answer, stated explicitly, so a category can be switched back off without deleting the rule."],
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
  const [policy, setPolicy] = useState<DormantRule["policy"]>("hold_current");
  const [qty, setQty] = useState("");
  const [batchId, setBatchId] = useState("");
  const [drafts, setDrafts] = useState<DormantRuleDraft[]>([]);

  useAssistantForm({ scope, match_key: matchKey, policy, fixed_qty: qty, batch_id: batchId, drafts,
    dirty: !!(matchKey || qty || drafts.length || scope !== "category" || policy !== "hold_current"), busy, ready: !!page }, (actions) => {
    if (!can.review(role) || busy) return;
    for (const action of actions) if (action.section === "dormant_rules") {
      if (action.rows.length === 1 && !drafts.length && !matchKey && !qty) {
        const row = action.rows[0];
        setScope(row.scope); setMatchKey(row.match_key); setPolicy(row.policy);
        setQty(row.fixed_qty === null ? "" : String(row.fixed_qty));
      } else {
        setDrafts((current) => {
          const updated = new Map(current.map((row) => [`${row.scope}:${row.match_key}`, row]));
          for (const row of action.rows) updated.set(`${row.scope}:${row.match_key}`, row);
          return [...updated.values()];
        });
      }
      setNote("NYRA filled a draft. Review the fields and use Propose to submit it.");
    }
  });

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
          policy,
          fixed_qty: policy === "fixed_qty" ? Number(qty) : null,
        }),
      });
      setNote("Proposed for your account. Confirm it with senior or administrator rights " +
              "to apply it on your next engine run.");
      setMatchKey(""); setQty(""); setScope("category"); setPolicy("hold_current"); await load();
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

  if (!page && !err) return <SettingsWorkspace active="dormant"><Spinner label="Loading dormant rules…" /></SettingsWorkspace>;
  const dirty = !!(matchKey || qty || drafts.length || scope !== "category" || policy !== "hold_current");

  return (
    <SettingsWorkspace active="dormant">
      {err && <div role="alert"><Banner kind="error">{err}</Banner></div>}
      {note && <div role="status"><Banner kind="success">{note}</Banner></div>}

      <div className="settings-card" data-assistant-section="Coverage & stock impact">
        <header className="settings-card-heading">
          <h2>Coverage &amp; stock impact</h2>
          <p>See how dormant rules affect a scored batch before the next engine run.</p>
        </header>
        <SettingsRow id="coverage-batch" label="Scored batch" description="Enter a batch ID to check its matching parts and stock value.">
          <input id="coverage-batch" className="field tnum" type="number" min={1} value={batchId}
            aria-describedby="coverage-batch-hint" placeholder="e.g. 13" disabled={busy}
            onChange={(e) => setBatchId(e.target.value)} />
        </SettingsRow>
        <div className="settings-actions">
          <p className="settings-status">Existing batch results stay unchanged.</p>
          <button type="button" className="btn" onClick={checkCoverage}
            disabled={busy || !batchId || !can.review(role)}>Check coverage</button>
        </div>
        {coverage && (
          <p className="settings-helper">
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

      <div className="settings-card" data-assistant-section="Dormant rules" data-assistant-target="dormant_rules">
        <header className="settings-card-heading">
          <h2>Dormant stocking rules</h2>
          <p>Set stock levels for parts with no consumption. Item rules take priority over category rules, then the default.</p>
          <p>Confirm your proposals with senior or administrator rights before your next engine run.
            {page && ` ${page.confirmed} confirmed · ${page.pending} pending.`}</p>
        </header>

        {page && page.rules.length > 0 && (
          <div className="settings-table scroll-x">
            <table className="w-full text-sm min-w-[560px]">
              <thead>
                <tr>
                  <th className="text-left">Scope</th>
                  <th className="text-left">Matches</th>
                  <th className="text-left">Policy</th>
                  <th className="text-right">Qty</th>
                  <th className="text-left">Status</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {page.rules.map((r) => (
                  <tr key={r.rule_id}>
                    <td>{r.scope}</td>
                    <td className="font-mono text-xs">{r.match_key || "—"}</td>
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

        <AssistantDraftTable rows={drafts} onChange={(rows) => setDrafts(rows.map((row) => ({ ...row,
          match_key: row.scope === "default" ? "" : row.match_key,
          fixed_qty: row.policy === "fixed_qty" ? row.fixed_qty : null,
        })))} section="dormant_rules"
          endpoint="config/dormant-rules" canPropose={can.review(role) && !busy} onSaved={load}
          onBusyChange={setBusy}
          valid={(row) => (row.scope === "default" ? !row.match_key : !!row.match_key.trim())
            && drafts.filter((other) => other.scope === row.scope && other.match_key === row.match_key).length === 1
            && (row.policy !== "fixed_qty" || (row.fixed_qty !== null && Number.isInteger(row.fixed_qty)
              && row.fixed_qty >= 0 && row.fixed_qty <= 10000))}
          replacesActive={(row) => !!page?.rules.some((rule) => rule.confirmed && rule.scope === row.scope && rule.match_key === row.match_key)}
          columns={[
            { key: "scope", label: "Scope", options: ["item", "category", "default"] },
            { key: "match_key", label: "Item / category", disabled: (row) => row.scope === "default" },
            { key: "policy", label: "Policy", options: POLICIES.map(([value]) => value) },
            { key: "fixed_qty", label: "Quantity", type: "number", disabled: (row) => row.policy !== "fixed_qty" },
          ]} />

        <div className="settings-rows">
          <SettingsRow id="dormant-scope" label="Scope" description="Apply this rule to an item, a category, or all dormant parts." changed={scope !== "category"}>
            <select id="dormant-scope" className="field" value={scope} disabled={busy || !can.review(role)}
              aria-describedby="dormant-scope-hint" onChange={(e) => setScope(e.target.value as DormantRule["scope"])}>
              <option value="category">Category</option><option value="item">Item</option><option value="default">Default</option>
            </select>
          </SettingsRow>
          <SettingsRow id="dormant-match" label={scope === "item" ? "Item id" : scope === "category" ? "Category" : "Matches"}
            description={scope === "default" ? "The default applies when no more specific rule matches." : "Use the exact item ID or category name."}
            changed={!!matchKey && scope !== "default"}>
            <input id="dormant-match" className="field font-mono" value={scope === "default" ? "" : matchKey}
              aria-describedby="dormant-match-hint" disabled={busy || !can.review(role) || scope === "default"}
              placeholder={scope === "item" ? "e.g. 500699364" : scope === "category" ? "e.g. filter" : "All dormant parts"}
              onChange={(e) => setMatchKey(e.target.value)} />
          </SettingsRow>
          <SettingsRow id="dormant-policy" label="Policy" description="Choose the stocking level for matching parts." changed={policy !== "hold_current"}>
            <select id="dormant-policy" className="field" value={policy} disabled={busy || !can.review(role)}
              aria-describedby="dormant-policy-hint" onChange={(e) => setPolicy(e.target.value as DormantRule["policy"])}>
              {POLICIES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </SettingsRow>
          <SettingsRow id="dormant-qty" label="Quantity" description="Required for a fixed quantity. Enter 0–10,000 units." changed={!!qty && policy === "fixed_qty"}>
            <input id="dormant-qty" className="field tnum" type="number" min={0} max={10000}
              aria-describedby="dormant-qty-hint" value={policy === "fixed_qty" ? qty : ""}
              placeholder={policy === "fixed_qty" ? "Enter units" : "Determined by policy"}
              disabled={busy || !can.review(role) || policy !== "fixed_qty"} onChange={(e) => setQty(e.target.value)} />
          </SettingsRow>
        </div>
        <p className="settings-helper">{POLICIES.find(([value]) => value === policy)?.[2]}</p>
        <SettingsActions dirty={dirty} disabled={busy} status={dirty ? "Unsaved dormant rule proposal" : "No pending edits"}
          onCancel={() => { setScope("category"); setMatchKey(""); setPolicy("hold_current"); setQty(""); setDrafts([]); setErr(null); setNote(null); }}>
          <button type="button" className="btn btn-primary" onClick={propose}
            disabled={busy || !can.review(role) || (scope !== "default" && !matchKey.trim())
              || (policy === "fixed_qty" && (qty === "" || !Number.isInteger(Number(qty)) || Number(qty) < 0 || Number(qty) > 10000))}>
            Propose
          </button>
        </SettingsActions>
      </div>
    </SettingsWorkspace>
  );
}
