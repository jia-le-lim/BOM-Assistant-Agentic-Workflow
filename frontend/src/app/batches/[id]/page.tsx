"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { fmtCompact, fmtUsd, useApi } from "@/lib/api";
import { can, useSession } from "@/lib/session";
import type {
  AssistPage, AssistRunSummary,
  BatchSummary, BulkReviewResult, Recommendation, RecommendationPage, RuleConfig, RunSummary,
  Status, TriagePage,
} from "@/lib/types";
import {
  AgreementChip, ASSIST_VERDICT, Banner, BusyLabel, ConsumableChip, Progress,
  ReasonCodes, RiskChip, StatTile, StatusChip, TableSkeleton,
} from "@/components/ui";
import { WorkflowPipeline } from "@/components/WorkflowPipeline";
import { AgentTriage, PriorityCallout, TriageLanes } from "@/components/TriageLanes";

const PAGE = 25;

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

  // One record, not eight useStates plus an eight-positional-argument updater.
  // The keys ARE the query-string names, so the URL and the /recommendations
  // request are both `new URLSearchParams(filters)` with the blanks dropped --
  // there is no third list to keep in step when a filter is added.
  const [filters, setFilters] = useState<Record<string, string>>(() => ({
    status: searchParams.get("status") ?? "pending_review",
    risk_level: searchParams.get("risk_level") ?? "",
    action: searchParams.get("action") ?? "",
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
      setErr(null);
    } catch (e) { setErr((e as Error).message); setPage(null); }
    finally { setRefreshing(false); }
  }, [call, batchId, query, offset]);

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
            <Filter label="Status" value={filters.status} set={(v) => setFilter({ status: v })}
                    opts={[["", "All"], ["pending_review", "Pending"], ["awaiting_senior", "Awaiting senior"],
                           ["reviewed", "Reviewed"], ["auto_cleared", "Auto-cleared"]]} />
            <Filter label="Demand" value={filters.consumable} set={(v) => setFilter({ consumable: v })}
                    opts={[["", "Any"], ["constant", "Constant"], ["sporadic", "Sporadic"],
                           ["dying", "Dying"], ["none", "Dormant"]]} />
            <Filter label="Agreement" value={filters.agreement} set={(v) => setFilter({ agreement: v })}
                    opts={[["", "Any"], ["match", "Matches"], ["diverge", "Diverges"], ["none", "No benchmark"]]} />
            <Filter label="Risk" value={filters.risk_level} set={(v) => setFilter({ risk_level: v })}
                    opts={[["", "Any"], ["High", "High"], ["Medium", "Medium"], ["Low", "Low"]]} />
            <Filter label="Action" value={filters.action} set={(v) => setFilter({ action: v })}
                    opts={[["", "Any"], ["Increase", "Increase"], ["Maintain", "Maintain"], ["Decrease", "Decrease"]]} />
            <label className="flex flex-col gap-1 text-xs">
              <span style={{ color: "var(--text-secondary)" }}>Min exposure $</span>
              <input className="field w-28" type="number" value={filters.min_exposure}
                     placeholder="0"
                     onChange={(e) => setFilter({ min_exposure: e.target.value })} />
            </label>
          </div>

          <p className="text-xs mb-3" style={{ color: "var(--text-muted)" }}>
            Sorted by exposure, highest first. Tick rows to accept or reject in bulk, or use
            the ✓ / ✕ on a row. Click a row to open the item — overrides live there.
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
              {filters.q && filters.status
                ? " That part may sit under a different status — try Status: All." : ""}
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
                      <th>Part</th><th>Category</th><th>Demand</th>
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
                      const href = `/batches/${batchId}/items/${r.item_id}`;
                      return (
                        <tr key={k} className="row-link" onClick={() => router.push(href)}>
                          <td onClick={(e) => e.stopPropagation()}>
                            <input type="checkbox" checked={selected.has(k)}
                                   disabled={!pending}
                                   onChange={() => toggle(k)}
                                   aria-label={`Select ${r.item_id}`} />
                          </td>
                          <td className="max-w-[220px]">
                            {/* A real link, not just the row handler: row click is
                                mouse-only, and this row has to be reachable by
                                keyboard and announced as a destination. */}
                            <Link href={href} className="font-mono text-xs"
                                  onClick={(e) => e.stopPropagation()}>{r.item_id}</Link>
                            {r.item_desc && (
                              <span className="block text-[11px] truncate"
                                    style={{ color: "var(--text-muted)" }}
                                    title={r.item_desc}>{r.item_desc}</span>
                            )}
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
                          <td className="text-right">
                            <Swing now={r.current_max} next={r.new_max} bench={r.bench_max} />
                          </td>
                          <td className="text-right">
                            <Swing now={r.current_rop} next={r.new_rop} bench={r.bench_rop} />
                          </td>
                          <td className="max-w-[240px]"><ReasonCodes codes={r.reason_code} /></td>
                          <td onClick={(e) => e.stopPropagation()}>
                            {pending && canReview && (
                              <div className="flex gap-1.5 justify-end">
                                <IconAction glyph="✓" colour="var(--good)"
                                            label={`Accept ${r.item_id}`}
                                            busy={busyAction === `accept:${k}`} disabled={busy}
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
              </div>
              <div className="flex items-center justify-between mt-4 text-xs">
                <span style={{ color: "var(--text-muted)" }}>
                  {offset + 1}–{Math.min(offset + PAGE, page.total)} of {page.total.toLocaleString()}
                </span>
                <div className="flex gap-2">
                  <button className="btn text-xs" disabled={offset === 0}
                          onClick={() => setFilter({ offset: String(Math.max(0, offset - PAGE)) })}>Previous</button>
                  <button className="btn text-xs" disabled={offset + PAGE >= page.total}
                          onClick={() => setFilter({ offset: String(offset + PAGE) })}>Next</button>
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
 *  graded against, and it is absent on a first cycle. */
function Swing({ now, next, bench }: {
  now?: number | null; next: number; bench?: number | null;
}) {
  const title = bench == null ? undefined
    : `Engineer proposed ${bench.toLocaleString()} this upload`;
  return (
    <span className="tnum whitespace-nowrap" title={title}>
      <span style={{ color: "var(--text-muted)" }}>
        {now == null ? "—" : now.toLocaleString()} →{" "}
      </span>
      {next.toLocaleString()}
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
    <label className="flex flex-col gap-1 text-xs">
      <span style={{ color: "var(--text-secondary)" }}>{label}</span>
      <select className="field" value={value} onChange={(e) => set(e.target.value)}>
        {opts.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </label>
  );
}
