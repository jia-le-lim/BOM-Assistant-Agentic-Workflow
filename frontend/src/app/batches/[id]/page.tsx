"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { fmtCompact, fmtUsd, useApi } from "@/lib/api";
import { can, useSession } from "@/lib/session";
import type {
  AssistPage, AssistRunSummary, AssistVerdict,
  BatchSummary, BulkReviewResult, Recommendation, RecommendationPage, RuleConfig, RunSummary,
  SimilarityRunSummary, Status, TriagePage,
} from "@/lib/types";
import {
  AgreementChip, Banner, BusyLabel, ConsumableChip, Progress, ReasonCodes,
  RiskChip, StatTile, StatusChip, TableSkeleton,
} from "@/components/ui";
import { WorkflowPipeline } from "@/components/WorkflowPipeline";
import { AgentTriage, PriorityCallout, TriageLanes } from "@/components/TriageLanes";

const PAGE = 25;

/* What each verdict tells the reviewer, in the order attention should go.
 * The chain decides these deterministically (backend/app/assist/rules.py); the
 * page only groups by them. */
const VERDICT: Record<AssistVerdict, { label: string; hint: string }> = {
  flag_for_review: { label: "Needs review", hint: "The engine's history on this part argues against taking it as read" },
  needs_context: { label: "No prior cycle", hint: "Never reviewed before — nothing to check the engine against" },
  bulk_accept_candidate: { label: "Bulk accept", hint: "Matched the last cycles and the engine has not moved off the accepted value" },
};

/** Composite key: an item can sit in more than one stockroom. */
const keyOf = (r: { item_id: string; stockroom_id: string }) =>
  `${r.item_id}::${r.stockroom_id}`;

export default function BatchPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const batchId = Number(id);
  const { call, raw } = useApi();
  const { role } = useSession();
  const canReview = can.review(role);
  const router = useRouter();
  const searchParams = useSearchParams();

  const [summary, setSummary] = useState<BatchSummary | null>(null);
  const [page, setPage] = useState<RecommendationPage | null>(null);
  const [triage, setTriage] = useState<TriagePage | null>(null);
  const [assist, setAssist] = useState<AssistPage | null>(null);
  const [ruleConfig, setRuleConfig] = useState<RuleConfig | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  // The name of the running action, not a bare boolean. With one shared flag
  // every button on this page read "Working…" at once, so a similarity run and
  // an engine re-run were indistinguishable while you waited for one of them.
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const busy = busyAction !== null;
  // A filter change refetches the queue. The old rows stay on screen, dimmed,
  // rather than being replaced by a hole -- what you were reading stays the
  // answer until a better one arrives.
  const [refreshing, setRefreshing] = useState(false);
  const [similarityRefresh, setSimilarityRefresh] = useState(false);

  // Initialize filter state from URL query parameters
  const [status, setStatus] = useState<Status | "">(
    (searchParams.get("status") as Status) || "pending_review"
  );
  const [risk, setRisk] = useState(searchParams.get("risk_level") || "");
  const [action, setAction] = useState(searchParams.get("action") || "");
  const [consumable, setConsumable] = useState(searchParams.get("consumable") || "");
  const [agreement, setAgreement] = useState(searchParams.get("agreement") || "");
  const [minExp, setMinExp] = useState(searchParams.get("min_exposure") || "");
  const [q, setQ] = useState(searchParams.get("q") || "");
  // What is in the box vs. what the queue is filtered by. They differ only while
  // you are still typing -- committing per keystroke would push a history entry
  // and refetch the whole batch for every character.
  const [qDraft, setQDraft] = useState(q);
  const [offset, setOffset] = useState(Number(searchParams.get("offset")) || 0);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  // Helper to update URL with current filters
  const updateUrl = useCallback((
    newStatus?: Status | "",
    newRisk?: string,
    newAction?: string,
    newConsumable?: string,
    newAgreement?: string,
    newMinExp?: string,
    newOffset?: number,
    newQ?: string,
  ) => {
    const params = new URLSearchParams();
    const s = newStatus ?? status;
    const r = newRisk ?? risk;
    const a = newAction ?? action;
    const c = newConsumable ?? consumable;
    const ag = newAgreement ?? agreement;
    const m = newMinExp ?? minExp;
    const o = newOffset ?? offset;
    const query = newQ ?? q;

    if (s) params.set("status", s);
    if (r) params.set("risk_level", r);
    if (a) params.set("action", a);
    if (c) params.set("consumable", c);
    if (ag) params.set("agreement", ag);
    if (m) params.set("min_exposure", m);
    if (query) params.set("q", query);
    if (o > 0) params.set("offset", String(o));

    router.push(`/batches/${batchId}?${params.toString()}`);
  }, [batchId, router, status, risk, action, consumable, agreement, minExp, offset, q]);

  const loadSummary = useCallback(async () => {
    try { setSummary(await call<BatchSummary>(`batches/${batchId}/summary`)); }
    catch (e) { setErr((e as Error).message); }
  }, [call, batchId]);

  const loadPage = useCallback(async () => {
    const params = new URLSearchParams({ batch_id: String(batchId), limit: String(PAGE), offset: String(offset) });
    if (status) params.set("status", status);
    if (risk) params.set("risk_level", risk);
    if (action) params.set("action", action);
    if (consumable) params.set("consumable", consumable);
    if (agreement) params.set("agreement", agreement);
    if (minExp) params.set("min_exposure", minExp);
    if (q.trim()) params.set("q", q.trim());
    setRefreshing(true);
    try {
      setPage(await call<RecommendationPage>(`recommendations?${params}`));
      setErr(null);
    } catch (e) { setErr((e as Error).message); setPage(null); }
    finally { setRefreshing(false); }
  }, [call, batchId, status, risk, action, consumable, agreement, minExp, offset, q]);

  const loadTriage = useCallback(async () => {
    if (!canReview) { setTriage(null); return; }
    try { setTriage(await call<TriagePage>(`triage/${batchId}`)); }
    catch (e) { setErr((e as Error).message); }
  }, [call, batchId, canReview]);

  const loadAssist = useCallback(async () => {
    try { setAssist(await call<AssistPage>(`assist/${batchId}`)); }
    catch { setAssist(null); }
  }, [call, batchId]);

  const loadRuleConfig = useCallback(async () => {
    try { setRuleConfig(await call<RuleConfig>("config/rules")); }
    catch (e) { setErr((e as Error).message); }
  }, [call]);

  // Create wrapper functions for filter setters that update both state and URL
  const handleStatusChange = useCallback((value: string) => {
    const v = value as Status | "";
    setStatus(v);
    setOffset(0);
    setSelected(new Set());
    updateUrl(v, risk, action, consumable, agreement, minExp, 0);
  }, [updateUrl, risk, action, consumable, agreement, minExp]);

  const handleRiskChange = useCallback((v: string) => {
    setRisk(v);
    setOffset(0);
    setSelected(new Set());
    updateUrl(status, v, action, consumable, agreement, minExp, 0);
  }, [updateUrl, status, action, consumable, agreement, minExp]);

  const handleActionChange = useCallback((v: string) => {
    setAction(v);
    setOffset(0);
    setSelected(new Set());
    updateUrl(status, risk, v, consumable, agreement, minExp, 0);
  }, [updateUrl, status, risk, consumable, agreement, minExp]);

  const handleConsumableChange = useCallback((v: string) => {
    setConsumable(v);
    setOffset(0);
    setSelected(new Set());
    updateUrl(status, risk, action, v, agreement, minExp, 0);
  }, [updateUrl, status, risk, action, agreement, minExp]);

  const handleAgreementChange = useCallback((v: string) => {
    setAgreement(v);
    setOffset(0);
    setSelected(new Set());
    updateUrl(status, risk, action, consumable, v, minExp, 0);
  }, [updateUrl, status, risk, action, consumable, minExp]);

  const handleMinExpChange = useCallback((v: string) => {
    setMinExp(v);
    setOffset(0);
    setSelected(new Set());
    updateUrl(status, risk, action, consumable, agreement, v, 0);
  }, [updateUrl, status, risk, action, consumable, agreement]);

  const handleOffsetChange = useCallback((v: number) => {
    setOffset(v);
    updateUrl(status, risk, action, consumable, agreement, minExp, v);
  }, [updateUrl, status, risk, action, consumable, agreement, minExp]);

  // Commit the search box 300ms after the last keystroke.
  useEffect(() => {
    if (qDraft === q) return;
    const t = window.setTimeout(() => {
      setQ(qDraft);
      setOffset(0);
      setSelected(new Set());
      updateUrl(status, risk, action, consumable, agreement, minExp, 0, qDraft);
    }, 300);
    return () => window.clearTimeout(t);
  }, [qDraft, q, updateUrl, status, risk, action, consumable, agreement, minExp]);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => { void loadSummary(); });
    return () => window.cancelAnimationFrame(frame);
  }, [loadSummary]);
  useEffect(() => {
    const frame = window.requestAnimationFrame(() => { void loadPage(); });
    return () => window.cancelAnimationFrame(frame);
  }, [loadPage]);
  useEffect(() => {
    const frame = window.requestAnimationFrame(() => { void loadTriage(); });
    return () => window.cancelAnimationFrame(frame);
  }, [loadTriage]);
  useEffect(() => {
    const frame = window.requestAnimationFrame(() => { void loadRuleConfig(); });
    return () => window.cancelAnimationFrame(frame);
  }, [loadRuleConfig]);
  useEffect(() => {
    const frame = window.requestAnimationFrame(() => { void loadAssist(); });
    return () => window.cancelAnimationFrame(frame);
  }, [loadAssist]);

  const isScored = summary?.batch.status === "scored";
  const statuses = (summary?.statuses ?? {}) as Record<Status, number>;
  const needsHuman = (statuses.pending_review ?? 0) + (statuses.awaiting_senior ?? 0);

  const refresh = useCallback(async () => {
    await Promise.all([loadSummary(), loadPage(), loadTriage(), loadAssist()]);
  }, [loadSummary, loadPage, loadTriage, loadAssist]);

  const verdicts = useMemo(() => {
    const out = new Map<string, AssistPage["items"][number]>();
    for (const a of assist?.items ?? []) out.set(keyOf(a), a);
    return out;
  }, [assist]);

  async function runEngine() {
    setBusyAction("engine"); setErr(null); setNote(null);
    try {
      const s = await call<RunSummary>(`run-recommendation?batch_id=${batchId}`, { method: "POST" });
      setNote(`Scored ${s.rows_scored.toLocaleString()} rows with ${s.rule_version} — ` +
              `${s.review_required_Y.toLocaleString()} need review, ` +
              `${s.review_required_N.toLocaleString()} auto-cleared.`);
      await refresh();
    } catch (e) { setErr((e as Error).message); }
    finally { setBusyAction(null); }
  }

  async function runSimilarity() {
    setBusyAction("similarity"); setErr(null); setNote(null);
    try {
      const s = await call<SimilarityRunSummary>("similarity/run", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ batch_id: batchId, refresh: similarityRefresh }),
      });
      setNote(s.neighbour_pool === 0
        ? "No reviewed history yet — every row is flagged as having no reliable "
          + "analogue. Run again once this month's reviews are recorded."
        : `Matched ${s.scored.toLocaleString()} rows against `
          + `${s.neighbour_pool.toLocaleString()} reviewed peers — `
          + `${s.outliers.toLocaleString()} unusual, `
          + `${s.diverging.toLocaleString()} diverging from peer median. `
          + `${s.categorised.toLocaleString()} of ${s.scored.toLocaleString()} `
          + `had a part category.`);
      setSimilarityRefresh(false);
      await refresh();
    } catch (e) { setErr((e as Error).message); }
    finally { setBusyAction(null); }
  }

  async function runAssist() {
    setBusyAction("assist"); setErr(null); setNote(null);
    try {
      const s = await call<AssistRunSummary>(`assist/run?batch_id=${batchId}`,
                                             { method: "POST" });
      setNote(s.rows_assisted === 0
        ? "No active or dying rows in this batch — dormant rows are sized by the "
          + "dormant rules instead."
        : `Assisted ${s.rows_assisted.toLocaleString()} rows — `
          + `${s.counts.flag_for_review} need review, `
          + `${s.counts.bulk_accept_candidate} bulk-accept candidates, `
          + `${s.counts.needs_context} with no prior cycle.`);
      await refresh();
    } catch (e) { setErr((e as Error).message); }
    finally { setBusyAction(null); }
  }

  function summarise(r: BulkReviewResult): string {
    const bits = [`${r.reviewed.toLocaleString()} accepted`];
    if (r.awaiting_senior) bits.push(`${r.awaiting_senior} sent for senior approval`);
    if (r.skipped) bits.push(`${r.skipped} already decided`);
    if (r.failed.length) bits.push(`${r.failed.length} failed`);
    return bits.join(", ");
  }

  async function acceptLane(cons: string) {
    setBusyAction(`lane:${cons}`); setErr(null); setNote(null);
    try {
      const r = await call<BulkReviewResult>("review/bulk", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          batch_id: batchId, decision: "accept",
          justification: "bulk: safe agreements in lane",
          filters: { consumable: cons, min_confidence: 0.8, exclude_high_risk: true },
        }),
      });
      setNote(`${cons} lane — ${summarise(r)}.`);
      setSelected(new Set());
      await refresh();
    } catch (e) { setErr((e as Error).message); }
    finally { setBusyAction(null); }
  }

  async function acceptGuardedTriage() {
    setBusyAction("guarded"); setErr(null); setNote(null);
    try {
      const r = await call<BulkReviewResult>("review/bulk", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          batch_id: batchId, decision: "accept",
          justification: "bulk: human-confirmed guarded triage candidates",
          filters: { triage_preselect: true },
        }),
      });
      setNote(`Guarded triage selection — ${summarise(r)}.`);
      setSelected(new Set());
      await refresh();
    } catch (e) { setErr((e as Error).message); }
    finally { setBusyAction(null); }
  }

  async function bulkSelected(decision: "accept" | "reject") {
    if (!selected.size) return;
    setBusyAction(`bulk:${decision}`); setErr(null); setNote(null);
    try {
      const items = [...selected].map((k) => {
        const [item_id, stockroom_id] = k.split("::");
        return { item_id, stockroom_id };
      });
      const r = await call<BulkReviewResult>("review/bulk", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          batch_id: batchId, decision,
          justification: `bulk: ${decision} from queue`, items,
        }),
      });
      setNote(`${decision === "accept" ? "Accepted" : "Rejected"} selection — ${summarise(r)}.`);
      setSelected(new Set());
      await refresh();
    } catch (e) { setErr((e as Error).message); }
    finally { setBusyAction(null); }
  }

  async function quickReview(r: Recommendation, decision: "accept" | "reject") {
    setBusyAction(`${decision}:${keyOf(r)}`); setErr(null); setNote(null);
    try {
      const res = await call<{ status: string; requires_senior_approval: boolean }>(
        `review/${r.item_id}?batch_id=${batchId}&stockroom_id=${encodeURIComponent(r.stockroom_id)}`,
        { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ decision, justification: "quick review" }) },
      );
      setNote(res.requires_senior_approval
        ? `${r.item_id} recorded — awaiting senior approval.`
        : `${r.item_id} ${decision}ed.`);
      await refresh();
    } catch (e) { setErr((e as Error).message); }
    finally { setBusyAction(null); }
  }

  async function download(format: "csv" | "xlsx") {
    // The workbook is assembled server-side over the whole batch, so this is a
    // seconds-long wait on a button that used to give no sign it was pressed.
    setBusyAction(`export:${format}`); setErr(null);
    try {
      const path = format === "xlsx" ? "export/wings.xlsx" : "export/wings";
      const res = await raw(`${path}?batch_id=${batchId}`);
      if (!res.ok) { setErr(`Export failed: ${(await res.json()).detail}`); return; }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = `wings_update_batch${batchId}.${format}`; a.click();
      URL.revokeObjectURL(url);
      setNote(`Export ready — ${res.headers.get("x-rows-updated") ?? res.headers.get("x-rows-exported") ?? ""} rows.`);
    } finally { setBusyAction(null); }
  }

  const preselectOn = Boolean(ruleConfig?.config.triage_guarded_assist_enabled);

  const preselectKeys = useMemo(() => (page?.items ?? [])
    .filter((r) => r.status === "pending_review"
                   && verdicts.get(keyOf(r))?.verdict === "bulk_accept_candidate")
    .map(keyOf), [page, verdicts]);

  // Pre-tick once per rendered page, never again: re-seeding on every render
  // would put the ticks back the moment someone pressed Clear, which is the one
  // escape hatch that makes pre-ticking safe at all.
  const seeded = useRef<string>("");
  useEffect(() => {
    if (!preselectOn || !canReview || !preselectKeys.length) return;
    const signature = preselectKeys.join("|");
    if (seeded.current === signature) return;
    seeded.current = signature;
    setSelected((prev) => new Set([...prev, ...preselectKeys]));
  }, [preselectOn, canReview, preselectKeys]);

  const pageKeys = useMemo(() => (page?.items ?? []).map(keyOf), [page]);
  const allSelected = pageKeys.length > 0 && pageKeys.every((k) => selected.has(k));

  function toggle(k: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(k)) next.delete(k); else next.add(k);
      return next;
    });
  }
  function toggleAll() {
    setSelected((prev) => {
      const next = new Set(prev);
      if (allSelected) pageKeys.forEach((k) => next.delete(k));
      else pageKeys.forEach((k) => next.add(k));
      return next;
    });
  }

  return (
    <div className="page-wide flex flex-col gap-6">
      <div className="page-head flex flex-wrap items-start justify-between gap-3">
        <div>
          <Link href="/" className="text-xs" style={{ color: "var(--text-muted)" }}>← Batches</Link>
          <h1 className="text-xl font-semibold mt-1">
            Batch #{batchId} {summary?.batch.label && `— ${summary.batch.label}`}
          </h1>
          <p className="text-sm mt-1" style={{ color: "var(--text-secondary)" }}>
            {summary
              ? `${summary.batch.row_count?.toLocaleString()} rows ingested · ` +
                `${summary.batch.quarantined_count} quarantined · ` +
                `${isScored ? `scored with ${summary.batch.scored_rule_version}` : "not yet scored"}`
              : "…"}
          </p>
        </div>
        <div className="flex gap-2">
          <button className="btn btn-primary" onClick={runEngine} disabled={busy || !can.upload(role)}>
            <BusyLabel busy={busyAction === "engine"} running="Scoring rows…"
                       idle={isScored ? "Re-run engine" : "Run engine"} />
          </button>
          <button className="btn btn-primary" onClick={() => download("xlsx")}
                  disabled={!isScored || !can.export(role)}
                  title="The monthly workbook with approved values already in their cells">
            <BusyLabel busy={busyAction === "export:xlsx"} running="Building…"
                       idle="Export workbook" />
          </button>
          <button className="btn" onClick={() => download("csv")}
                  disabled={!isScored || !can.export(role)} title="Just the changed rows, as CSV">
            <BusyLabel busy={busyAction === "export:csv"} running="Building…" idle="Export CSV" />
          </button>
        </div>
      </div>

      {err && <Banner kind="error">{err}</Banner>}
      {note && <Banner kind="success">{note}</Banner>}

      {!isScored && !err && (
        <Banner kind="info">
          This batch has not been scored yet. Run the engine to generate recommendations.
        </Banner>
      )}

      {summary && isScored && (
        <>
          <div className="grid gap-4 grid-cols-2 lg:grid-cols-4">
            <StatTile label="Needs a human" value={needsHuman}
                      sub="pending + awaiting senior" accent="var(--warning)" />
            <StatTile label="Safe to bulk-accept" value={summary.bulk_acceptable}
                      sub="high-confidence agreements"
                      accent={summary.bulk_acceptable ? "var(--success-text)" : undefined} />
            <StatTile label="Exposure at stake" value={fmtCompact(summary.exposure_total_usd)}
                      sub={`${fmtCompact(summary.exposure_pending_usd)} still unreviewed`} />
            <StatTile label="Ready for WINGS" value={summary.export_ready_rows}
                      sub="approved & changed"
                      accent={summary.export_ready_rows ? "var(--success-text)" : undefined} />
          </div>
        </>
      )}

      <div className="work-split">
        <div className="flex flex-col gap-6">
        <div className="card p-5">
          <div className="queue-toolbar flex flex-wrap items-end gap-3 mb-4">
            <h2 className="text-sm font-semibold mr-auto">Review queue</h2>
            <label className="flex flex-col gap-1 text-xs">
              <span style={{ color: "var(--text-secondary)" }}>Search part</span>
              <input className="field w-44" type="search" value={qDraft}
                     placeholder="Item or stockroom"
                     aria-label="Search the review queue by item or stockroom"
                     onChange={(e) => setQDraft(e.target.value)} />
            </label>
            <Filter label="Status" value={status} set={handleStatusChange}
                    opts={[["", "All"], ["pending_review", "Pending"], ["awaiting_senior", "Awaiting senior"],
                           ["reviewed", "Reviewed"], ["auto_cleared", "Auto-cleared"]]} />
            <Filter label="Demand" value={consumable} set={handleConsumableChange}
                    opts={[["", "Any"], ["constant", "Constant"], ["sporadic", "Sporadic"],
                           ["dying", "Dying"], ["none", "Dormant"]]} />
            <Filter label="Agreement" value={agreement} set={handleAgreementChange}
                    opts={[["", "Any"], ["match", "Matches"], ["diverge", "Diverges"], ["none", "No benchmark"]]} />
            <Filter label="Risk" value={risk} set={handleRiskChange}
                    opts={[["", "Any"], ["High", "High"], ["Medium", "Medium"], ["Low", "Low"]]} />
            <Filter label="Action" value={action} set={handleActionChange}
                    opts={[["", "Any"], ["Increase", "Increase"], ["Maintain", "Maintain"], ["Decrease", "Decrease"]]} />
            <label className="flex flex-col gap-1 text-xs">
              <span style={{ color: "var(--text-secondary)" }}>Min exposure $</span>
              <input className="field w-28" type="number" value={minExp} placeholder="0"
                     onChange={(e) => handleMinExpChange(e.target.value)} />
            </label>
          </div>

          <p className="text-xs mb-3" style={{ color: "var(--text-muted)" }}>
            Sorted by exposure, highest first. Tick rows to accept or reject in bulk, or use the
            inline actions. Overrides still open the item detail.
          </p>

          {selected.size > 0 && (
            <div className="flex items-center gap-3 mb-3 p-2 rounded"
                 style={{ background: "var(--seq-soft)" }}>
              <span className="text-sm">
                <strong>{selected.size}</strong> selected
                {preselectOn && preselectKeys.length > 0
                  && ` (${preselectKeys.length} pre-ticked by assist — check before accepting)`}
              </span>
              <button className="btn btn-primary text-xs" disabled={busy || !canReview}
                      onClick={() => bulkSelected("accept")}>
                <BusyLabel busy={busyAction === "bulk:accept"} running="Accepting…"
                           idle="Accept selected" />
              </button>
              <button className="btn text-xs" disabled={busy || !canReview}
                      onClick={() => bulkSelected("reject")}>
                <BusyLabel busy={busyAction === "bulk:reject"} running="Rejecting…"
                           idle="Reject selected" />
              </button>
              <button className="btn text-xs" onClick={() => setSelected(new Set())}>Clear</button>
            </div>
          )}

          {refreshing && page !== null && <Progress />}

          {page === null ? (
            <TableSkeleton rows={8} cols={7} label="Loading the review queue" />
          ) : page.items.length === 0 ? (
            <p className="text-sm py-6" style={{ color: "var(--text-muted)" }}>
              No rows match these filters.
              {q && status ? " That part may sit under a different status — try Status: All." : ""}
            </p>
          ) : (
            <div className={refreshing ? "is-refreshing" : undefined}>
              <div className="scroll-x">
                <table className="w-full text-sm min-w-[1000px]">
                  <thead>
                    <tr>
                      <th className="w-8">
                        <input type="checkbox" checked={allSelected} onChange={toggleAll}
                               aria-label="Select all on page" />
                      </th>
                      <th>Item</th><th>Demand</th><th className="text-right">Exposure</th>
                      <th>Status</th><th>Risk</th><th>Agreement</th><th>Assist</th>
                      <th className="text-right">Max</th><th>Why</th><th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {page.items.map((r: Recommendation) => {
                      const k = keyOf(r);
                      const pending = r.status === "pending_review";
                      const a = verdicts.get(k);
                      return (
                        <tr key={k}>
                          <td>
                            <input type="checkbox" checked={selected.has(k)}
                                   disabled={!pending}
                                   onChange={() => toggle(k)}
                                   aria-label={`Select ${r.item_id}`} />
                          </td>
                          <td className="font-mono text-xs">{r.item_id}</td>
                          <td><ConsumableChip value={r.consumable} /></td>
                          <td className="text-right tnum">{fmtUsd(r.exposure_usd)}</td>
                          <td><StatusChip status={r.status} /></td>
                          <td><RiskChip level={r.risk_level} /></td>
                          <td><AgreementChip value={r.agreement} source={r.agreement_source} /></td>
                          <td className="text-xs" title={a ? VERDICT[a.verdict].hint : undefined}
                              style={{ color: a?.verdict === "flag_for_review"
                                ? "var(--warning)" : "var(--text-secondary)" }}>
                            {a ? VERDICT[a.verdict].label : "—"}
                            {a?.narrative && (
                              <span className="block mt-0.5" style={{ color: "var(--text-muted)" }}>
                                {a.narrative}
                              </span>
                            )}
                          </td>
                          <td className="text-right tnum">{r.new_max}</td>
                          <td className="max-w-[240px]"><ReasonCodes codes={r.reason_code} /></td>
                          <td>
                            <div className="flex gap-1.5 justify-end">
                              {pending && canReview && (
                                <>
                                  <button className="btn text-xs" disabled={busy}
                                          onClick={() => quickReview(r, "accept")}>
                                    <BusyLabel busy={busyAction === `accept:${k}`}
                                               running="Accepting…" idle="Accept" />
                                  </button>
                                  <button className="btn text-xs" disabled={busy}
                                          onClick={() => quickReview(r, "reject")}>
                                    <BusyLabel busy={busyAction === `reject:${k}`}
                                               running="Rejecting…" idle="Reject" />
                                  </button>
                                </>
                              )}
                              <Link className="btn text-xs"
                                    href={`/batches/${batchId}/items/${r.item_id}`}>Open</Link>
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <div className="flex items-center justify-between mt-4 text-xs">
                <span style={{ color: "var(--text-muted)" }}>
                  {offset + 1}–{Math.min(offset + PAGE, page.total)} of {page.total.toLocaleString()}
                </span>
                <div className="flex gap-2">
                  <button className="btn text-xs" disabled={offset === 0}
                          onClick={() => handleOffsetChange(Math.max(0, offset - PAGE))}>Previous</button>
                  <button className="btn text-xs" disabled={offset + PAGE >= page.total}
                          onClick={() => handleOffsetChange(offset + PAGE)}>Next</button>
                </div>
              </div>
            </div>
          )}
        </div>
              {canReview && triage && (
                <AgentTriage batchId={batchId} triage={triage} busy={busy}
                             guardedAssist={Boolean(ruleConfig?.config.triage_guarded_assist_enabled)}
                             onGuardedAccept={acceptGuardedTriage} />
              )}
        </div>

        {summary && isScored && (
          <div className="work-rail">
              <PriorityCallout summary={summary} />

              {canReview && (
                <div className="card p-5">
                  <div className="flex flex-wrap items-end gap-3">
                    <div className="mr-auto">
                      <h2 className="text-sm font-semibold">Run peer similarity</h2>
                      <p className="text-xs mt-1" style={{ color: "var(--text-muted)" }}>
                        Matches each scored row against previously reviewed parts. Advisory
                        evidence only — it never changes Min/ROP/Max or a risk level.
                      </p>
                    </div>
                    <label className="flex items-center gap-2 text-xs pb-2">
                      <input type="checkbox" checked={similarityRefresh}
                             onChange={(e) => setSimilarityRefresh(e.target.checked)} />
                      Rebuild existing results
                    </label>
                    <button className="btn btn-primary" onClick={runSimilarity}
                            disabled={busy}>
                      <BusyLabel busy={busyAction === "similarity"} running="Matching peers…"
                                 idle="Run similarity" />
                    </button>
                  </div>
                </div>
              )}
              {canReview && (
                <div className="card p-5">
                  <div className="flex flex-wrap items-end gap-3">
                    <div className="mr-auto">
                      <h2 className="text-sm font-semibold">Run review assist</h2>
                      <p className="text-xs mt-1" style={{ color: "var(--text-muted)" }}>
                        Gives every active or dying row a verdict from its own
                        match/diverge history. The verdict is decided by rules, not by
                        a model — the model only writes the sentence explaining it.
                        Dormant rows are sized by the{" "}
                        <Link href="/config/dormant" style={{ textDecoration: "underline" }}>
                          dormant rules
                        </Link>{" "}instead.
                        {assist && assist.items.length > 0
                          && ` ${assist.counts.flag_for_review} flagged · `
                             + `${assist.counts.bulk_accept_candidate} bulk-accept · `
                             + `${assist.counts.needs_context} no prior cycle.`}
                        {preselectOn
                          ? " Bulk-accept rows arrive pre-ticked; nothing is submitted until you press Accept."
                          : " Pre-ticking is off — turn on guarded assistance in Rules & criticality to enable it."}
                      </p>
                    </div>
                    <button className="btn btn-primary" onClick={runAssist} disabled={busy}>
                      <BusyLabel busy={busyAction === "assist"} running="Assisting rows…"
                                 idle="Run assist" />
                    </button>
                  </div>
                </div>
              )}
                <TriageLanes summary={summary} busy={busy} canReview={canReview}
                             onAcceptLane={acceptLane} />
                <WorkflowPipeline counts={statuses} exported={summary.export_ready_rows} />
          </div>
        )}
      </div>
    </div>
  );
}

function Filter({ label, value, set, opts }: {
  label: string; value: string; set: (v: string) => void; opts: [string, string][];
}) {
  return (
    <label className="flex flex-col gap-1 text-xs">
      <span style={{ color: "var(--text-secondary)" }}>{label}</span>
      <select className="field" value={value} onChange={(e) => set(e.target.value)}>
        {opts.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </label>
  );
}
