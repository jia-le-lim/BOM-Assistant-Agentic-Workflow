"use client";

import { useCallback, useEffect, useState } from "react";
import { useApi } from "@/lib/api";
import { can, useSession } from "@/lib/session";
import type { PartCategoryPage, RuleConfig, CriticalityDraft, CategoryDraft } from "@/lib/types";
import { useAssistantForm } from "@/lib/assistant-context";
import { AssistantDraftTable } from "@/components/AssistantDraftTable";
import { Banner, Spinner } from "@/components/ui";
import { SETTINGS_SECTIONS, SettingsActions, SettingsRow, SettingsWorkspace, type SettingsSection } from "@/components/SettingsWorkspace";

const EDITABLE = [
  ["long_lead_time_threshold", "Long lead time (days)", "Workload/safety dial: 30 → 47% review & 7.6% miss; 60 → 38% review & 10.7% miss"],
  ["zero_stock_risk_clt", "Zero-stock risk lead time (days)", "Dormant parts at/above this lead time are carved out of auto-clear"],
  ["high_cost_threshold", "High cost ($)", "Override rate roughly doubles above ~$1,500"],
  ["low_cost_threshold", "Low cost ($)", "Rule 1 low-cost recurring usage"],
  ["recent_usage_days", "Recent usage (days)", "How far back to look for recent consumption."],
  ["min_usage_months", "Min usage months", "Minimum months of usage for a recurring-demand signal."],
  ["max_change_pct_review", "Max change before review", "0.5 = +50%"],
  ["value_gate_usd", "Value gate ($)", "Exposure at/above this always reaches a human"],
  ["min_protective_stock", "Protective floor (units)", "Applied when the source algorithm says 0 but risk exists"],
] as const;

// Statistical-engine auto-clear knobs. All default OFF -- s15 calibration on
// Jan'26 showed expanding auto-clear lowered agreement with engineers.
const AUTOCLEAR = [
  ["autoclear_immaterial_usd", "Immaterial $ floor", "Auto-clear changes below this exposure. 0 = off"],
  ["autoclear_noop_abs", "No-op tolerance (units)", "Engine within this many units of current = no change. 0 = off"],
  ["autoclear_noop_rel", "No-op tolerance (fraction)", "…or within this fraction (0.10 = 10%). 0 = off"],
  ["autoclear_high_value_usd", "High-value gate ($)", "A material change at/above this always reaches a human"],
] as const;

export default function ConfigPage() {
  const { call } = useApi();
  const { role } = useSession();
  const [section, setSection] = useState<SettingsSection>("thresholds");
  const [cfg, setCfg] = useState<RuleConfig | null>(null);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [version, setVersion] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [pattern, setPattern] = useState("");
  const [crit, setCrit] = useState("High");
  const [cats, setCats] = useState<PartCategoryPage | null>(null);
  const [catPattern, setCatPattern] = useState("");
  const [catName, setCatName] = useState("");
  const [catPriority, setCatPriority] = useState("500");
  const [reliableEdit, setReliableEdit] = useState("");
  const [triageEnabledEdit, setTriageEnabledEdit] = useState("");
  const [criticalityDrafts, setCriticalityDrafts] = useState<CriticalityDraft[]>([]);
  const [categoryDrafts, setCategoryDrafts] = useState<CategoryDraft[]>([]);

  useEffect(() => {
    const syncSection = () => {
      const id = window.location.hash.slice(1);
      const match = SETTINGS_SECTIONS.find((entry) => entry.id === id);
      if (match) setSection(match.id);
    };
    const frame = window.requestAnimationFrame(syncSection);
    window.addEventListener("hashchange", syncSection);
    return () => { window.cancelAnimationFrame(frame); window.removeEventListener("hashchange", syncSection); };
  }, []);

  function selectSection(id: SettingsSection) {
    setSection(id);
    window.history.replaceState(null, "", `#${id}`);
  }

  useAssistantForm({ edits, rule_version: version, pattern, criticality: crit,
    catPattern, catName, catPriority, reliableEdit, triageEnabledEdit,
    criticalityDrafts, categoryDrafts, busy, ready: !!cfg,
    dirty: !!(Object.keys(edits).length || version || pattern || crit !== "High" || catPattern || catName || catPriority !== "500"
      || reliableEdit || triageEnabledEdit || criticalityDrafts.length || categoryDrafts.length),
  }, (actions) => {
    if (busy || !can.review(role)) return;
    for (const action of actions) {
      if (action.section === "thresholds" && can.configWrite(role)) {
        const numeric: Record<string, string> = {};
        for (const [key, value] of Object.entries(action.updates)) {
          if (key === "autoclear_reliable") setReliableEdit(String(value));
          else if (key === "triage_guarded_assist_enabled") setTriageEnabledEdit(String(value));
          else if ([...EDITABLE, ...AUTOCLEAR].some(([name]) => name === key)) numeric[key] = String(value);
        }
        setEdits((current) => ({ ...current, ...numeric }));
        if (action.rule_version) setVersion(action.rule_version);
      } else if (action.section === "criticality") {
        if (action.rows.length === 1 && !pattern && !criticalityDrafts.length) {
          setPattern(action.rows[0].pattern); setCrit(action.rows[0].criticality);
        } else setCriticalityDrafts((current) => {
          const updated = new Map(current.map((row) => [row.pattern, row]));
          for (const row of action.rows) updated.set(row.pattern, row);
          return [...updated.values()];
        });
      } else if (action.section === "part_categories") {
        if (action.rows.length === 1 && !catPattern && !categoryDrafts.length) {
          setCatPattern(action.rows[0].pattern); setCatName(action.rows[0].category);
          setCatPriority(String(action.rows[0].priority));
        } else setCategoryDrafts((current) => {
          const updated = new Map(current.map((row) => [row.pattern, row]));
          for (const row of action.rows) updated.set(row.pattern, row);
          return [...updated.values()];
        });
      }
    }
    const first = actions[0];
    if (first) {
      if (first.section === "criticality") selectSection("criticality");
      else if (first.section === "part_categories") selectSection("part-categories");
      else if (first.section === "thresholds") {
        const keys = Object.keys(first.updates);
        selectSection(keys.every((key) => key.startsWith("autoclear_")) ? "autoclear"
          : keys.every((key) => key === "triage_guarded_assist_enabled") ? "review-assist" : "thresholds");
      }
    }
    setNote("NYRA filled an editable draft. Review the fields, then use Save or Propose.");
  });

  const load = useCallback(async () => {
    try { setCfg(await call<RuleConfig>("config/rules")); setErr(null); }
    catch (e) { setErr((e as Error).message); }
  }, [call]);

  const loadCategories = useCallback(async () => {
    try { setCats(await call<PartCategoryPage>("config/part-categories")); }
    catch (e) { setErr((e as Error).message); }
  }, [call]);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      void load(); void loadCategories();
    });
    return () => window.cancelAnimationFrame(frame);
  }, [load, loadCategories]);

  async function save() {
    if (!can.configWrite(role) || busy || !version.trim()) return;
    if (Object.values(edits).some((value) => !value.trim() || !Number.isFinite(Number(value)))) {
      setErr("Enter a valid number for each changed setting."); return;
    }
    setBusy(true); setErr(null); setNote(null);
    try {
      const updates: Record<string, number | boolean> = {};
      for (const [k, v] of Object.entries(edits)) {
        if (v !== "" && Number(v) !== Number(cfg?.config[k])) updates[k] = Number(v);
      }
      if (reliableEdit !== "" &&
          (reliableEdit === "true") !== Boolean(cfg?.config.autoclear_reliable)) {
        updates.autoclear_reliable = reliableEdit === "true";
      }
      if (triageEnabledEdit !== "" &&
          (triageEnabledEdit === "true") !== Boolean(cfg?.config.triage_guarded_assist_enabled)) {
        updates.triage_guarded_assist_enabled = triageEnabledEdit === "true";
      }
      if (!Object.keys(updates).length) { setErr("No changes to apply."); return; }
      const r = await call<{ rule_version: string }>("config/rules", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rule_version: version.trim(), updates }),
      });
      setNote(`Saved as ${r.rule_version}. Re-run the engine or triage on a batch to apply it — ` +
              `existing results keep the version they were scored with.`);
      setEdits({}); setReliableEdit(""); setTriageEnabledEdit(""); setVersion(""); await load();
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
      setNote(`Proposed "${pattern}" as ${crit}. Confirm it with senior or administrator ` +
              `rights for your account — the engine reads confirmed rows only.`);
      setPattern(""); setCrit("High"); await load();
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

  async function proposeCategory() {
    setBusy(true); setErr(null); setNote(null);
    try {
      await call("config/part-categories", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pattern: catPattern, category: catName,
                               priority: Number(catPriority) || 500 }),
      });
      setNote(`Proposed "${catPattern}" → ${catName}. It does NOT affect peer ` +
              `retrieval until you confirm it with senior or administrator rights.`);
      setCatPattern(""); setCatName(""); setCatPriority("500"); await loadCategories();
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  async function confirmCategory(p: string) {
    setBusy(true); setErr(null); setNote(null);
    try {
      await call(`config/part-categories/${encodeURIComponent(p)}/confirm`,
                 { method: "POST" });
      setNote(`Confirmed "${p}" — re-run similarity on a batch to apply it.`);
      await loadCategories();
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  if (!cfg) return (
    <SettingsWorkspace active={section} onSelect={selectSection}>
      {err ? <Banner kind="error">{err}</Banner> : <Spinner label="Loading settings…" />}
    </SettingsWorkspace>
  );

  const criticality = (cfg.config.machine_criticality ?? {}) as Record<string, string>;
  const numericChanged = (key: string) => edits[key] !== undefined
    && (edits[key] === "" || Number(edits[key]) !== Number(cfg.config[key]));
  const reliableChanged = reliableEdit !== ""
    && (reliableEdit === "true") !== Boolean(cfg.config.autoclear_reliable);
  const triageChanged = triageEnabledEdit !== ""
    && (triageEnabledEdit === "true") !== Boolean(cfg.config.triage_guarded_assist_enabled);
  const rulesChanged = Object.keys(edits).some(numericChanged) || reliableChanged || triageChanged;
  const rulesDirty = rulesChanged || !!version;
  const criticalityDirty = !!(pattern || crit !== "High" || criticalityDrafts.length);
  const categoriesDirty = !!(catPattern || catName || catPriority !== "500" || categoryDrafts.length);
  const invalidNumber = Object.values(edits).some((value) => !value.trim() || !Number.isFinite(Number(value)));
  const changedSections: SettingsSection[] = [];
  if (EDITABLE.some(([key]) => numericChanged(key))) changedSections.push("thresholds");
  if (AUTOCLEAR.some(([key]) => numericChanged(key)) || reliableChanged) changedSections.push("autoclear");
  if (triageChanged) changedSections.push("review-assist");
  if (criticalityDirty) changedSections.push("criticality");
  if (categoriesDirty) changedSections.push("part-categories");
  const isRuleSection = section === "thresholds" || section === "autoclear" || section === "review-assist";

  function cancelRules() {
    setEdits({}); setReliableEdit(""); setTriageEnabledEdit(""); setVersion("");
    setErr(null); setNote(null);
  }

  function numericRows(rows: typeof EDITABLE | typeof AUTOCLEAR) {
    return rows.map(([key, label, hint]) => (
      <SettingsRow key={key} id={key} label={label} description={hint} changed={numericChanged(key)}>
        <input id={key} className="field tnum" type="number" step="any"
          aria-label={label} aria-describedby={`${key}-hint`}
          disabled={busy || !can.configWrite(role)}
          value={edits[key] ?? String(cfg?.config[key] ?? "")}
          onChange={(e) => setEdits((current) => ({ ...current, [key]: e.target.value }))} />
      </SettingsRow>
    ));
  }

  return (
    <SettingsWorkspace active={section} onSelect={selectSection} changed={changedSections} version={cfg.rule_version}>
      {err && <div role="alert"><Banner kind="error">{err}</Banner></div>}
      {note && <div role="status"><Banner kind="success">{note}</Banner></div>}

      <div className="settings-card">
        <section hidden={section !== "thresholds"} data-assistant-section="Thresholds" data-assistant-target={section === "thresholds" ? "thresholds" : undefined}>
          <header className="settings-card-heading">
            <h2>Thresholds</h2>
            <p>Lead times, costs, and stock levels used across BOM reviews.</p>
          </header>
          <div className="settings-rows">{numericRows(EDITABLE)}</div>
        </section>

        <section hidden={section !== "autoclear"} data-assistant-section="Auto-clear policy" data-assistant-target={section === "autoclear" ? "thresholds" : undefined}>
          <header className="settings-card-heading">
            <h2>Auto-clear policy</h2>
            <p>Choose when a part can skip human review. These settings do not change Min/ROP/Max.</p>
          </header>
          <div className="settings-rows">
            {numericRows(AUTOCLEAR)}
            <SettingsRow id="autoclear-reliable" label="Reliable-stable lever" changed={reliableChanged}
              description="Auto-clear regular, stable parts with moderate exposure. Off by default.">
              <select id="autoclear-reliable" className="field" aria-label="Reliable-stable lever"
                aria-describedby="autoclear-reliable-hint" disabled={busy || !can.configWrite(role)}
                value={reliableEdit || String(Boolean(cfg.config.autoclear_reliable))}
                onChange={(e) => setReliableEdit(e.target.value)}>
                <option value="false">Off</option><option value="true">On</option>
              </select>
            </SettingsRow>
          </div>
          <p className="settings-helper">Auto-clear is calibrated off. Check agreement with engineers before enabling it.</p>
        </section>

        <section hidden={section !== "review-assist"} data-assistant-section="Guarded bulk acceptance" data-assistant-target={section === "review-assist" ? "thresholds" : undefined}>
          <header className="settings-card-heading">
            <h2>Review assist</h2>
            <p>Control how review assist prepares parts for bulk acceptance.</p>
          </header>
          <div className="settings-rows">
            <SettingsRow id="guarded-assist" label="Guarded bulk acceptance" changed={triageChanged}
              description="Preselect eligible candidates. An engineer still confirms every bulk action.">
              <select id="guarded-assist" className="field" aria-label="Guarded bulk acceptance"
                aria-describedby="guarded-assist-hint" disabled={busy || !can.configWrite(role)}
                value={triageEnabledEdit || String(Boolean(cfg.config.triage_guarded_assist_enabled))}
                onChange={(e) => setTriageEnabledEdit(e.target.value)}>
                <option value="false">Off</option><option value="true">On</option>
              </select>
            </SettingsRow>
          </div>
          <p className="settings-helper">Re-run review assist on a batch to rebuild its candidates after saving.</p>
        </section>

        {isRuleSection && <>
          <div className="settings-version">
            <SettingsRow id="rule-version" label="New rule version" changed={!!version}
              description={`Required to save. Active version: ${cfg.rule_version}.`}>
              <input id="rule-version" className="field" aria-label="New rule version (required)"
                aria-describedby="rule-version-hint" value={version} placeholder="e.g. 0.2.1-tcb"
                disabled={busy || !can.configWrite(role)} onChange={(e) => setVersion(e.target.value)} />
            </SettingsRow>
          </div>
          {!can.configWrite(role) && <p className="settings-helper">Administrator access is required to edit these settings.</p>}
          <SettingsActions dirty={rulesDirty} disabled={busy} onCancel={cancelRules}
            status={rulesDirty ? "Unsaved changes in rules" : "All changes saved"}>
            <button type="button" className="btn btn-primary" onClick={save}
              disabled={busy || !can.configWrite(role) || !version.trim() || !rulesChanged || invalidNumber}>
              {busy ? "Saving…" : "Save changes"}
            </button>
          </SettingsActions>
          {changedSections.filter((id) => ["thresholds", "autoclear", "review-assist"].includes(id)).length > 1
            && <p className="settings-helper">Saving includes your changes in Thresholds, Auto-clear policy, and Review assist.</p>}
        </>}

        <section hidden={section !== "criticality"} data-assistant-section="Machine criticality" data-assistant-target="criticality">
          <header className="settings-card-heading">
            <h2>Machine criticality</h2>
            <p>Set the importance of each machine type. Confirm your proposals with senior or administrator rights to activate them.</p>
          </header>
          {Object.keys(criticality).length > 0 ? (
            <div className="settings-list">
              <h3>Confirmed &amp; active</h3>
              <div className="settings-badges">
                {Object.entries(criticality).map(([p, c]) => <span key={p}>✓ {p} → {c}</span>)}
              </div>
            </div>
          ) : <p className="settings-empty">No confirmed machine rules yet. Add a proposal below.</p>}

          <AssistantDraftTable rows={criticalityDrafts} onChange={setCriticalityDrafts} section="criticality"
            endpoint="config/criticality" canPropose={can.review(role) && !busy} onSaved={load}
            onBusyChange={setBusy} valid={(row) => row.pattern.trim().length >= 2}
            replacesActive={(row) => Object.hasOwn(criticality, row.pattern)}
            columns={[{ key: "pattern", label: "Machine type contains" },
              { key: "criticality", label: "Criticality", options: ["High", "Medium", "Low"] }]} />

          <div className="settings-rows">
            <SettingsRow id="machine-pattern" label="Machine type contains" description="Match part of a machine name." changed={!!pattern}>
              <input id="machine-pattern" className="field" value={pattern} placeholder="e.g. KnS TCX3"
                aria-describedby="machine-pattern-hint" onChange={(e) => setPattern(e.target.value)} disabled={busy || !can.review(role)} />
            </SettingsRow>
            <SettingsRow id="machine-criticality" label="Criticality" description="The risk level assigned to matching machines." changed={crit !== "High"}>
              <select id="machine-criticality" className="field" value={crit} onChange={(e) => setCrit(e.target.value)}
                aria-label="Criticality" aria-describedby="machine-criticality-hint" disabled={busy || !can.review(role)}>
                <option>High</option><option>Medium</option><option>Low</option>
              </select>
            </SettingsRow>
          </div>
          <SettingsActions dirty={criticalityDirty} disabled={busy}
            status={criticalityDirty ? "Unsaved criticality proposal" : "No pending edits"}
            onCancel={() => { setPattern(""); setCrit("High"); setCriticalityDrafts([]); setErr(null); setNote(null); }}>
            <button type="button" className="btn" onClick={() => confirm(pattern)}
              disabled={busy || !pattern.trim() || !can.approve(role)}
              title="Confirm your personal proposal with approval rights">Confirm as senior</button>
            <button type="button" className="btn btn-primary" onClick={propose}
              disabled={busy || pattern.trim().length < 2 || !can.review(role)}>Propose</button>
          </SettingsActions>
        </section>

        <section hidden={section !== "part-categories"} data-assistant-section="Part categories" data-assistant-target="part_categories">
          <header className="settings-card-heading">
            <h2>Part categories</h2>
            <p>Group similar parts by description. Lower priorities match first; confirm your proposals with senior or administrator rights.</p>
          </header>
          {cats && cats.rules.length > 0 ? (
            <div className="settings-table scroll-x">
              <p className="settings-helper">{cats.confirmed} confirmed · {cats.pending} pending</p>
              <table className="w-full">
                <thead><tr><th>Priority</th><th>Category</th><th>Pattern</th><th>Status</th><th><span className="sr-only">Actions</span></th></tr></thead>
                <tbody>{cats.rules.map((r) => (
                  <tr key={r.pattern}>
                    <td className="tnum">{r.priority}</td><td>{r.category}</td>
                    <td className="font-mono text-xs">{r.pattern}</td>
                    <td>{r.confirmed ? `✓ Active${r.confirmed_by ? ` (${r.confirmed_by})` : ""}`
                      : `◷ Pending${r.set_by ? ` (${r.set_by})` : ""}`}</td>
                    <td>{!r.confirmed && <button type="button" className="btn" disabled={busy || !can.approve(role)}
                      onClick={() => confirmCategory(r.pattern)}>Confirm</button>}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          ) : <p className="settings-empty">{cats ? "No category rules yet. Add a proposal below." : "Loading category rules…"}</p>}

          <AssistantDraftTable rows={categoryDrafts} onChange={setCategoryDrafts} section="part_categories"
            endpoint="config/part-categories" canPropose={can.review(role) && !busy} onSaved={loadCategories}
            onBusyChange={setBusy}
            valid={(row) => row.pattern.length >= 2 && row.category.length >= 2
              && Number.isInteger(row.priority) && row.priority >= 1 && row.priority <= 9999}
            replacesActive={(row) => !!cats?.rules.some((rule) => rule.confirmed && rule.pattern === row.pattern)}
            columns={[{ key: "pattern", label: "Pattern" }, { key: "category", label: "Category" },
              { key: "priority", label: "Priority", type: "number" }]} />

          <div className="settings-rows">
            <SettingsRow id="category-pattern" label="Pattern (regex on description)" description="Match words in the part description." changed={!!catPattern}>
              <input id="category-pattern" className="field font-mono" value={catPattern} disabled={busy || !can.review(role)}
                aria-describedby="category-pattern-hint" onChange={(e) => setCatPattern(e.target.value)}
                placeholder="e.g. GRIPPER|VACUUM CUP" />
            </SettingsRow>
            <SettingsRow id="category-name" label="Category" description="Only parts in the same known category can be peers." changed={!!catName}>
              <input id="category-name" className="field" value={catName} disabled={busy || !can.review(role)}
                aria-describedby="category-name-hint" onChange={(e) => setCatName(e.target.value)} placeholder="e.g. gripper" />
            </SettingsRow>
            <SettingsRow id="category-priority" label="Priority" description="Use 1–9999. Give specific patterns a lower number." changed={catPriority !== "500"}>
              <input id="category-priority" className="field tnum" type="number" min={1} max={9999}
                aria-describedby="category-priority-hint" value={catPriority} disabled={busy || !can.review(role)}
                onChange={(e) => setCatPriority(e.target.value)} />
            </SettingsRow>
          </div>
          <SettingsActions dirty={categoriesDirty} disabled={busy}
            status={categoriesDirty ? "Unsaved category proposal" : "No pending edits"}
            onCancel={() => { setCatPattern(""); setCatName(""); setCatPriority("500"); setCategoryDrafts([]); setErr(null); setNote(null); }}>
            <button type="button" className="btn btn-primary" onClick={proposeCategory}
              disabled={busy || catPattern.trim().length < 2 || catName.trim().length < 2 || !can.review(role)
                || !Number.isInteger(Number(catPriority)) || Number(catPriority) < 1 || Number(catPriority) > 9999}>Propose</button>
          </SettingsActions>
        </section>
      </div>
    </SettingsWorkspace>
  );
}
