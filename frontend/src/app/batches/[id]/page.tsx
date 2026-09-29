"use client";

import { ReminderContext } from "@/components/ReminderContext";

import Link from "next/link";
import { use, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { fmtCompact, fmtUsd, useApi } from "@/lib/api";
import { can, useSession } from "@/lib/session";
import type {
  AssistPage, AssistRunSummary,
  BatchSummary, BulkReviewResult, Recommendation, RecommendationPage, RuleConfig, RunSummary,
  Status,
} from "@/lib/types";
import {
  AgreementChip, ASSIST_VERDICT, Banner, BusyLabel, ConsumableChip, Progress,
  ReasonCodes, RiskChip, StatTile, StatusChip, TableSkeleton,
} from "@/components/ui";
import { WorkflowPipeline } from "@/components/WorkflowPipeline";
import { PriorityCallout, TriageLanes } from "@/components/TriageLanes";
import { ReviewSplitView } from "@/components/ReviewSplitView";
import type { ReviewDraft } from "@/components/ItemReview";
import { useReviewView } from "@/lib/review-view";
import { transitionReview } from "@/lib/review-motion";

const PAGE = 25;

/** Composite key: an item can sit in more than one stockroom. */
const keyOf = (r: { item_id: string; stockroom_id: string }) =>
  `${r.item_id}::${r.stockroom_id}`;

export default function BatchPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const batchId = Number(id);
  const { call, raw } = useApi();
  const { role, user } = useSession();
  const [view, setView] = useReviewView();
  const [activeItem, setActiveItem] = useState<string | null>(null);
  const [detailBusy, setDetailBusy] = useState(false);
  const [detailRevision, setDetailRevision] = useState(0);
  const [drafts] = useState(() => new Map<string, ReviewDraft>());
  const [overriding, setOverriding] = useState<Set<string>>(new Set());
  const router = useRouter();
  const searchParams = useSearchParams();

  const [summary, setSummary] = useState<BatchSummary | null>(null);
  const canReview = can.review(role) && summary?.read_only !== true;
  const [page, setPage] = useState<RecommendationPage | null>(null);
  const [assist, setAssist] = useState<AssistPage | null>(null);
  const [ruleConfig, setRuleConfig] = useState<RuleConfig | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  // The name of the running action, not a bare boolean. With one shared flag
  // every button on this page read "Working…" at once, so a similarity run and
  // an engine re-run were indistinguishable while you waited for one of them.
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const busy = busyAction !== null || detailBusy;
  // A filter change refetches the queue. The old rows stay on screen, dimmed,
  // rather than being replaced by a hole -- what you were reading stays the
  // answer until a better one arrives.
  const [refreshing, setRefreshing] = useState(false);
  const [similarityRefresh, setSimilarityRefresh] = useState(false);

  // One record, not eight useStates plus an eight-positional-argument updater.
  // The keys ARE the query-string names, so the URL and the /recommendations
  // request are both `new URLSearchParams(filters)` with the blanks dropped --
  // there is no third list to keep in step when a filter is added.
  const [filters, setFilters] = useState<Record<string, string>>(() => ({
    status: searchParams.get("status") ?? "pending_review",
    risk_level: searchParams.get("risk_level") ?? "",
    consumable: searchParams.get("consumable") ?? "",
    agreement: searchParams.get("agreement") ?? "",
    min_exposure: searchParams.get("min_exposure") ?? "",
    q: searchParams.get("q") ?? "",
    offset: searchParams.get("offset") ?? "",
  }));
  // What is in the box vs. what the queue is filtered by. They differ only while
  // you are still typing -- committing per keystroke would push a history entry
  // and refetch the whole batch for every character.
  const [qDraft, setQDraft] = useState(filters.q);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  // Numbers typed over the engine's proposal, keyed by row. Only rows someone
  // actually retyped land here, so an untouched row still submits as a plain
  // accept and the queue keeps showing the engine's figure everywhere else.
  const [edits, setEdits] = useState<Record<string, { max: string; rop: string }>>({});
  // The one cell currently open for typing, as "<row key>:max" | "<row key>:rop".
  // Only one box is ever open: clicking another number closes this one, and
  // leaving the box (blur, Enter, Escape) puts the number back to plain text.
  const [editing, setEditing] = useState<string | null>(null);

  const onDraftChange = useCallback((key: string, override: boolean) => {
    setOverriding((prev) => {
      if (prev.has(key) === override) return prev;
      const next = new Set(prev);
      if (override) next.add(key); else next.delete(key);
      return next;
    });
    if (override) setSelected((prev) => {
      if (!prev.has(key)) return prev;
      const next = new Set(prev); next.delete(key); return next;
    });
  }, []);

  const blockedKeys = useMemo(() => {
    const keys = new Set(overriding);
    for (const row of page?.items ?? []) {
      const key = keyOf(row), edit = edits[key];
      if (edit && (edit.max !== String(row.new_max) || edit.rop !== String(row.new_rop))) keys.add(key);
    }
    return keys;
  }, [overriding, page, edits]);

  /** Record a typed-over Max or ROP, seeding the pair from the engine values.
   *  Typing also unticks the row: bulk accept records the engine's numbers, so
   *  a ticked-and-edited row would quietly submit the figure just replaced. */
  function setEdit(r: Recommendation, field: "max" | "rop", v: string) {
    const k = keyOf(r);
    setEdits((prev) => {
      const base = prev[k] ?? { max: String(r.new_max), rop: String(r.new_rop) };
      return { ...prev, [k]: { ...base, [field]: v } };
    });
    setSelected((prev) => {
      if (!prev.has(k)) return prev;
      const next = new Set(prev); next.delete(k); return next;
    });
  }

  /** Escape drops a typed number back to what the engine proposed. */
  function resetEdit(r: Recommendation, field: "max" | "rop") {
    setEdit(r, field, String(field === "max" ? r.new_max : r.new_rop));
  }

  const offset = Number(filters.offset) || 0;
  const query = useMemo(() => new URLSearchParams(
    Object.entries(filters).filter(([, v]) => v)).toString(), [filters]);

  // Changing any filter but the page resets to the first page and drops the
  // selection. Ticks surviving a filter change is how someone bulk-accepts rows
  // they can no longer see.
  const setFilter = useCallback((patch: Record<string, string>) => {
    setFilters((prev) => ({ ...prev, offset: "", ...patch }));
    if (!("offset" in patch)) setSelected(new Set());
  }, []);

  // push, not replace: a filter change is a place you can go Back from, and
  // that is how the original eight-argument updater behaved. scroll:false keeps
  // the toolbar under your cursor instead of jumping to the top of the page.
  // The mount run is skipped -- the URL already says this, and pushing it again
  // would put a duplicate entry in the history on every page load.
  const urlSynced = useRef(false);
  useEffect(() => {
    if (!urlSynced.current) { urlSynced.current = true; return; }
    router.push(query ? `/batches/${batchId}?${query}` : `/batches/${batchId}`,
                { scroll: false });
  }, [router, batchId, query]);

  const loadSummary = useCallback(async () => {
    try { setSummary(await call<BatchSummary>(`batches/${batchId}/summary`)); }
    catch (e) { setErr((e as Error).message); }
  }, [call, batchId]);

  const loadPage = useCallback(async () => {
    const params = new URLSearchParams(query);
    params.set("batch_id", String(batchId));
    params.set("limit", String(PAGE));
    params.set("offset", String(offset));
    setRefreshing(true);
    try {
      setPage(await call<RecommendationPage>(`recommendations?${params}`));
      setDetailRevision((value) => value + 1);
      // A new page of rows is a new set of proposals; half-typed numbers from
      // the rows that just left the screen must not follow them.
      setEdits({});
      setEditing(null);
      setErr(null);
    } catch (e) { setErr((e as Error).message); setPage(null); }
    finally { setRefreshing(false); }
  }, [call, batchId, query, offset]);

  const loadAssist = useCallback(async () => {
    try { setAssist(await call<AssistPage>(`assist/${batchId}`)); }
    catch { setAssist(null); }
  }, [call, batchId]);

  const loadRuleConfig = useCallback(async () => {
    try { setRuleConfig(await call<RuleConfig>("config/rules")); }
    catch (e) { setErr((e as Error).message); }
  }, [call]);

  // Commit the search box 300ms after the last keystroke.
  useEffect(() => {
    if (qDraft === filters.q) return;
    const t = window.setTimeout(() => setFilter({ q: qDraft }), 300);
    return () => window.clearTimeout(t);
  }, [qDraft, filters.q, setFilter]);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => { void loadSummary(); });
    return () => window.cancelAnimationFrame(frame);
  }, [loadSummary]);
  useEffect(() => {
    const frame = window.requestAnimationFrame(() => { void loadPage(); });
    return () => window.cancelAnimationFrame(frame);
  }, [loadPage]);
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
    await Promise.all([loadSummary(), loadPage(), loadAssist()]);
  }, [loadSummary, loadPage, loadAssist]);

  const refreshAfterDetail = useCallback(async () => {
    setNote("Item decision saved. The queue has been updated.");
    setSelected(new Set());
    await refresh();
  }, [refresh]);

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

  async function runAssist() {
    setBusyAction("assist"); setErr(null); setNote(null);
    try {
      const s = await call<AssistRunSummary>(
        `assist/run?batch_id=${batchId}&refresh_peers=${similarityRefresh}`,
        { method: "POST" });
      // The peer line only appears when this run built peers -- on a re-run
      // over existing ones the backend returns null and there is nothing new
      // to report.
      const peers = s.similarity === null ? ""
        : s.similarity.neighbour_pool === 0
          ? " No reviewed history yet, so every row is flagged as having no"
            + " reliable analogue."
          : ` Matched ${s.similarity.scored.toLocaleString()} rows against `
            + `${s.similarity.neighbour_pool.toLocaleString()} reviewed peers `
            + `(${s.similarity.outliers.toLocaleString()} unusual, `
            + `${s.similarity.diverging.toLocaleString()} diverging from peer `
            + `median).`;
      setNote((s.rows_assisted === 0
        ? "No active or dying rows in this batch — dormant rows are sized by the "
          + "dormant rules instead."
        : `Assisted ${s.rows_assisted.toLocaleString()} rows — `
          + `${s.counts.flag_for_review} need review, `
          + `${s.counts.bulk_accept_candidate} bulk-accept candidates, `
          + `${s.counts.needs_context} with no prior cycle.`) + peers);
      setSimilarityRefresh(false);
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
    const k = keyOf(r);
    const ed = edits[k];
    // Accepting a row whose numbers were typed over is an override: the
    // engineer's figures go in as final and the API sends the row for senior
    // approval. Min is not a column here, so the engine's Min rides along --
    // clamped under the new ROP, because the API rejects anything but
    // Max >= ROP >= Min and a Min left above it would bounce the submit.
    const override = decision === "accept" && ed !== undefined
      && (ed.max !== String(r.new_max) || ed.rop !== String(r.new_rop));
    const body = override
      ? { decision: "override", justification: "queue override",
          final_max: Number(ed.max), final_rop: Number(ed.rop),
          final_min: Math.min(r.new_min, Number(ed.rop)) }
      : { decision, justification: "quick review" };
    setBusyAction(`${decision}:${k}`); setErr(null); setNote(null);
    try {
      const res = await call<{ status: string; requires_senior_approval: boolean }>(
        `review/${r.item_id}?batch_id=${batchId}&stockroom_id=${encodeURIComponent(r.stockroom_id)}`,
        { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body) },
      );
      setNote(res.requires_senior_approval
        ? `${r.item_id} recorded${override ? " as an override" : ""} — awaiting senior approval.`
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

  const pageKeys = useMemo(() => (page?.items ?? [])
    .filter((row) => row.status === "pending_review" && !blockedKeys.has(keyOf(row)))
    .map(keyOf), [page, blockedKeys]);
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
          <button className="btn btn-primary" onClick={runEngine} disabled={busy || !can.upload(role) || summary?.read_only}>
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
      {summary?.read_only && <Banner kind="info">Viewing another user&apos;s workspace. You have read-only access.</Banner>}
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

      <ReminderContext batchId={batchId} />

      <div className={`work-split review-layout-region${view === "split" ? " review-work-split" : ""}`}>
        <div className="flex flex-col gap-6">
        <div className="card p-5">
          <fieldset disabled={busy} className="queue-toolbar flex flex-wrap items-end gap-3 mb-4">
            <h2 className="text-sm font-semibold mr-auto" data-review-morph="control:heading">Review queue</h2>
            <div className="review-view-toggle" data-review-morph="control:layout" role="group" aria-label="Review layout">
              <button type="button" aria-pressed={view === "rows"} disabled={busy}
                onClick={() => transitionReview("layout", () => setView("rows"), view !== "rows")}>
                <svg width="15" height="15" viewBox="0 0 20 20" fill="none" stroke="currentColor" aria-hidden><rect x="2" y="3" width="16" height="14" rx="2" /><path d="M2 8h16M2 12h16M7 3v14" /></svg>
                Rows
              </button>
              <button type="button" aria-pressed={view === "split"} disabled={busy}
                onClick={() => transitionReview("layout", () => setView("split"), view !== "split")}>
                <svg width="15" height="15" viewBox="0 0 20 20" fill="none" stroke="currentColor" aria-hidden><rect x="2" y="3" width="16" height="14" rx="2" /><path d="M8 3v14M4 7h2M4 10h2M4 13h2" /></svg>
                Split view
              </button>
            </div>
            <label className="flex flex-col gap-1 text-xs" data-review-morph="control:search">
              <span style={{ color: "var(--text-secondary)" }}>Search part</span>
              <input className="field w-44" type="search" value={qDraft}
                     placeholder="Item or stockroom"
                     aria-label="Search the review queue by item or stockroom"
                     onChange={(e) => setQDraft(e.target.value)} />
            </label>
            <Filter label="Status" value={filters.status} set={(v) => setFilter({ status: v })}
                    opts={[["", "All"], ["pending_review", "Pending"], ["awaiting_senior", "Awaiting senior"],
                           ["reviewed", "Reviewed"], ["auto_cleared", "Auto-cleared"]]} />
            {/* The engine can still classify a row "sporadic"; it is simply not
                a lane anyone filters by here, and Any still reaches those rows. */}
            <Filter label="Demand" value={filters.consumable} set={(v) => setFilter({ consumable: v })}
                    opts={[["", "Any"], ["constant", "Constant"],
                           ["dying", "Dying"], ["none", "Dormant"]]} />
            <Filter label="Agreement" value={filters.agreement} set={(v) => setFilter({ agreement: v })}
                    opts={[["", "Any"], ["match", "Matches"], ["diverge", "Diverges"], ["none", "No benchmark"]]} />
            <Filter label="Risk" value={filters.risk_level} set={(v) => setFilter({ risk_level: v })}
                    opts={[["", "Any"], ["High", "High"], ["Medium", "Medium"], ["Low", "Low"]]} />
            <label className="flex flex-col gap-1 text-xs" data-review-morph="control:exposure">
              <span style={{ color: "var(--text-secondary)" }}>Min exposure $</span>
              <input className="field w-28" type="number" value={filters.min_exposure}
                     placeholder="0"
                     onChange={(e) => setFilter({ min_exposure: e.target.value })} />
            </label>
          </fieldset>

          {view === "rows" ? <p className="text-xs mb-3" style={{ color: "var(--text-muted)" }}>
            Sorted by exposure, highest first. Tick rows to accept or reject in bulk, or use
            the ✓ / ✕ on a row. Not happy with a proposed Max or ROP? Type your own over it —
            that row&apos;s ✓ then records an override and goes for senior approval. Click a row
            to open the item for the full form, with Min and a justification.
          </p> : <p className="text-xs mb-3" style={{ color: "var(--text-muted)" }}>
            Sorted by exposure, highest first. Choose an item to review its details here. Your layout preference is saved in this browser.
          </p>}

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

          {page === null && err ? (
            <button className="btn" onClick={() => void loadPage()}>Retry queue</button>
          ) : page === null ? (
            <TableSkeleton rows={8} cols={7} label="Loading the review queue" />
          ) : page.items.length === 0 ? (
            <p className="text-sm py-6" style={{ color: "var(--text-muted)" }}>
              No rows match these filters.
              {filters.q && filters.status
                ? " That part may sit under a different status — try Status: All." : ""}
            </p>
          ) : (
            <div className={refreshing ? "is-refreshing" : undefined}>
              {view === "split" ? <ReviewSplitView key={`${batchId}:${user}:${role}`} batchId={batchId}
                rows={page.items} activeKey={activeItem} onActivate={setActiveItem}
                verdicts={verdicts} selected={selected} onToggle={toggle}
                canReview={canReview} disabled={busy || refreshing} drafts={drafts}
                revision={detailRevision}
                blockedKeys={blockedKeys} onDraftChange={onDraftChange}
                onReviewed={refreshAfterDetail} onBusyChange={setDetailBusy} /> : <div className="scroll-x">
                <table className="w-full text-sm min-w-[1280px]">
                  <thead>
                    <tr>
                      <th className="w-8">
                        <input type="checkbox" checked={allSelected} onChange={toggleAll}
                               aria-label="Select all on page" />
                      </th>
                      <th>Item</th><th>Description</th><th>Category</th><th>Demand</th>
                      <th className="text-right">Exposure</th>
                      <th className="w-6" title="Status">St</th>
                      <th className="w-6" title="Risk level">Rk</th>
                      <th className="w-6" title="Agreement with the engineer">Ag</th>
                      <th>Assist</th>
                      <th className="text-right">Max now → new</th>
                      <th className="text-right">ROP now → new</th>
                      <th>Why</th><th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {page.items.map((r: Recommendation) => {
                      const k = keyOf(r);
                      const pending = r.status === "pending_review";
                      const a = verdicts.get(k);
                      const href = `/batches/${batchId}/items/${encodeURIComponent(r.item_id)}?stockroom_id=${encodeURIComponent(r.stockroom_id)}`;
                      const ed = edits[k];
                      const eMax = ed?.max ?? String(r.new_max);
                      const eRop = ed?.rop ?? String(r.new_rop);
                      const edited = eMax !== String(r.new_max) || eRop !== String(r.new_rop);
                      // A pair the API would reject never leaves the browser --
                      // a 422 behind a one-glyph button is not a readable error.
                      const badEdit = edited && !(eMax !== "" && eRop !== ""
                        && Number.isInteger(Number(eMax)) && Number.isInteger(Number(eRop))
                        && Number(eMax) >= Number(eRop) && Number(eRop) >= 0);
                      return (
                        <tr key={k} className="row-link" data-review-morph={`row:${k}`} onClick={() => router.push(href)}>
                          <td onClick={(e) => e.stopPropagation()}>
                            <input type="checkbox" checked={selected.has(k)}
                                   disabled={!pending || edited || blockedKeys.has(k)}
                                   onChange={() => toggle(k)}
                                   title={edited
                                     ? "Bulk accept records the engine's numbers — use the "
                                       + "✓ on this row to record yours."
                                     : undefined}
                                   aria-label={`Select ${r.item_id}`} />
                          </td>
                          <td>
                            {/* A real link, not just the row handler: row click is
                                mouse-only, and this row has to be reachable by
                                keyboard and announced as a destination. */}
                            <Link href={href} className="font-mono text-xs review-morph-text" data-review-morph={`title:${k}`}
                                  onClick={(e) => e.stopPropagation()}>{r.item_id}</Link>
                          </td>
                          <td className="max-w-[240px] text-xs truncate"
                              title={r.item_desc || undefined}>
                            <span className="review-morph-text" data-review-morph={`description:${k}`}>{r.item_desc || "—"}</span>
                          </td>
                          <td className="text-[11px]" style={{ color: "var(--text-secondary)" }}>
                            {r.part_category || "—"}
                          </td>
                          <td><ConsumableChip value={r.consumable} /></td>
                          <td className="text-right tnum">{fmtUsd(r.exposure_usd)}</td>
                          <td><StatusChip status={r.status} compact /></td>
                          <td><RiskChip level={r.risk_level} compact /></td>
                          <td><AgreementChip value={r.agreement} source={r.agreement_source}
                                             compact /></td>
                          <td className="text-xs" title={a ? ASSIST_VERDICT[a.verdict].hint : undefined}
                              style={{ color: a?.verdict === "flag_for_review"
                                ? "var(--warning)" : "var(--text-secondary)" }}>
                            {a ? ASSIST_VERDICT[a.verdict].label : "—"}
                            {a?.narrative && (
                              <span className="block mt-0.5" style={{ color: "var(--text-muted)" }}>
                                {a.narrative}
                              </span>
                            )}
                          </td>
                          <td className="text-right" onClick={(e) => e.stopPropagation()}>
                            <Swing now={r.current_max} next={r.new_max} bench={r.bench_max}
                                   edit={pending && canReview
                                     ? { value: eMax, bad: badEdit,
                                         label: `New Max for ${r.item_id}`,
                                         open: editing === `${k}:max`,
                                         onOpen: () => setEditing(`${k}:max`),
                                         onClose: (cancel) => {
                                           if (cancel) resetEdit(r, "max");
                                           setEditing(null);
                                         },
                                         onChange: (v) => setEdit(r, "max", v) }
                                     : undefined} />
                          </td>
                          <td className="text-right" onClick={(e) => e.stopPropagation()}>
                            <Swing now={r.current_rop} next={r.new_rop} bench={r.bench_rop}
                                   edit={pending && canReview
                                     ? { value: eRop, bad: badEdit,
                                         label: `New ROP for ${r.item_id}`,
                                         open: editing === `${k}:rop`,
                                         onOpen: () => setEditing(`${k}:rop`),
                                         onClose: (cancel) => {
                                           if (cancel) resetEdit(r, "rop");
                                           setEditing(null);
                                         },
                                         onChange: (v) => setEdit(r, "rop", v) }
                                     : undefined} />
                          </td>
                          <td className="max-w-[240px]"><ReasonCodes codes={r.reason_code} /></td>
                          <td onClick={(e) => e.stopPropagation()}>
                            {pending && canReview && (
                              <div className="flex gap-1.5 justify-end">
                                <IconAction glyph="✓" colour="var(--good)"
                                            label={badEdit
                                              ? `Max must be at least ROP for ${r.item_id}`
                                              : edited ? `Override ${r.item_id} with your numbers`
                                              : `Accept ${r.item_id}`}
                                            busy={busyAction === `accept:${k}`}
                                            disabled={busy || badEdit}
                                            onClick={() => quickReview(r, "accept")} />
                                <IconAction glyph="✕" colour="var(--critical)"
                                            label={`Reject ${r.item_id}`}
                                            busy={busyAction === `reject:${k}`} disabled={busy}
                                            onClick={() => quickReview(r, "reject")} />
                              </div>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>}
              <div className="flex items-center justify-between mt-4 text-xs">
                <span style={{ color: "var(--text-muted)" }}>
                  {offset + 1}–{Math.min(offset + PAGE, page.total)} of {page.total.toLocaleString()}
                </span>
                <div className="flex gap-2">
                  <button className="btn text-xs" disabled={busy || refreshing || offset === 0}
                          onClick={() => setFilter({ offset: String(Math.max(0, offset - PAGE)) })}>Previous</button>
                  <button className="btn text-xs" disabled={busy || refreshing || offset + PAGE >= page.total}
                          onClick={() => setFilter({ offset: String(offset + PAGE) })}>Next</button>
                </div>
              </div>
            </div>
          )}
        </div>
        </div>

        {summary && isScored && (
          <div className="work-rail">
              <PriorityCallout summary={summary} />

              {canReview && (
                <div className="card p-5">
                  <div className="flex flex-wrap items-end gap-3">
                    <div className="mr-auto">
                      <h2 className="text-sm font-semibold">Run review assist</h2>
                      <p className="text-xs mt-1" style={{ color: "var(--text-muted)" }}>
                        Matches every scored row against previously reviewed parts,
                        then gives each active or dying row a verdict from its own
                        match/diverge history. The verdict is decided by rules, not by
                        a model — the model only writes the sentence explaining it, and
                        neither one changes Min/ROP/Max or a risk level.
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
                    <label className="flex items-center gap-2 text-xs pb-2">
                      <input type="checkbox" checked={similarityRefresh}
                             onChange={(e) => setSimilarityRefresh(e.target.checked)} />
                      Rebuild peer matches
                    </label>
                    <button className="btn btn-primary" onClick={runAssist} disabled={busy}>
                      <BusyLabel busy={busyAction === "assist"} running="Assisting rows…"
                                 idle="Run assist" />
                    </button>
                  </div>
                </div>
              )}
                <TriageLanes summary={summary} />
                <WorkflowPipeline counts={statuses} exported={summary.export_ready_rows} />
          </div>
        )}
      </div>
    </div>
  );
}

/** Current Wings setting -> what the engine proposes. The engineer's own
 *  number for this cycle rides in the tooltip: it is what `agreement` was
 *  graded against, and it is absent on a first cycle.
 *
 *  With `edit` the proposed number is clickable: it reads as plain text until
 *  someone clicks it, and only then becomes a box. Typing records nothing --
 *  the row's ✓ is still the only thing that writes, and it writes an override
 *  once the number differs from the engine's. */
function Swing({ now, next, bench, edit }: {
  now?: number | null; next: number; bench?: number | null;
  edit?: {
    value: string; bad: boolean; label: string; open: boolean;
    onOpen: () => void; onClose: (cancel: boolean) => void; onChange: (v: string) => void;
  };
}) {
  const title = bench == null ? undefined
    : `Engineer proposed ${bench.toLocaleString()} this upload`;
  const changed = edit !== undefined && edit.value !== String(next);
  const colour = edit?.bad ? "var(--critical)" : changed ? "var(--warning)" : undefined;
  return (
    <span className="tnum whitespace-nowrap" title={title}>
      <span style={{ color: "var(--text-muted)" }}>
        {now == null ? "—" : now.toLocaleString()} →{" "}
      </span>
      {!edit ? next.toLocaleString()
        : edit.open ? (
          // Enter and Escape both leave; only Escape puts the engine's number
          // back. autoFocus is the point of the click that opened this.
          <input className="cell-num tnum" type="number" min={0}
                 autoFocus value={edit.value} aria-label={edit.label}
                 onChange={(e) => edit.onChange(e.target.value)}
                 onBlur={() => edit.onClose(false)}
                 onKeyDown={(e) => {
                   if (e.key === "Enter") edit.onClose(false);
                   if (e.key === "Escape") edit.onClose(true);
                 }}
                 style={{ borderColor: colour }} />
        ) : (
          <button type="button" className="cell-edit tnum" onClick={edit.onOpen}
                  aria-label={`${edit.label} — click to edit`} style={{ color: colour }}>
            {edit.value === "" ? "—" : Number(edit.value).toLocaleString()}
          </button>
        )}
      {bench != null && bench !== next && <span style={{ color: "var(--warning)" }}> *</span>}
    </span>
  );
}

/** Glyph-only row action. The label is the accessible name, never dropped --
 *  a bare ✓ is unreadable to a screen reader and ambiguous under a tooltip. */
function IconAction({ glyph, colour, label, busy, disabled, onClick }: {
  glyph: string; colour: string; label: string;
  busy: boolean; disabled: boolean; onClick: () => void;
}) {
  return (
    <button className="btn px-2 py-0.5 text-sm leading-none" title={label}
            aria-label={label} disabled={disabled} onClick={onClick}
            style={{ color: busy ? "var(--text-muted)" : colour }}>
      {busy ? "…" : glyph}
    </button>
  );
}

function Filter({ label, value, set, opts }: {
  label: string; value: string; set: (v: string) => void; opts: [string, string][];
}) {
  return (
    <label className="flex flex-col gap-1 text-xs" data-review-morph={`control:${label}`}>
      <span style={{ color: "var(--text-secondary)" }}>{label}</span>
      <select className="field" value={value} onChange={(e) => set(e.target.value)}>
        {opts.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </label>
  );
}
