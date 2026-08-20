"use client";

import { use, useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError, fmtUsd, useApi } from "@/lib/api";
import { can, useSession } from "@/lib/session";
import type {
  ItemDetail, JustificationTemplate, JustificationTemplatePage, Review,
  SimilarityResult, TriageResult,
} from "@/lib/types";
import {
  ActionChip, AgreementChip, Banner, BusyLabel, CardSkeleton, ConsumableChip,
  ReasonCodes, RiskChip, Skeleton, StatusChip, TriageChip,
} from "@/components/ui";
import {
  emptyRunStream, reduceTriageStream, RunStream,
  type RunStreamState, type TriageStreamEvent,
} from "@/components/RunStream";

const CONTEXT_LABELS: Record<string, string> = {
  item_desc: "Description", machine_type: "Machine type", aging_status: "Aging status",
  unitprice: "Unit price", contractual_lead_time: "Lead time (days)",
  max_qty: "Current max", rop_qty: "Current ROP", min_qty: "Current min",
  avail_qty: "On hand", last_365_day_cnsmptn_qty: "365-day consumption",
  recom_max: "recom max", atm_recommended_max: "ATM max", sfm_brr_max: "SFM/BRR max",
  replenishment_policy: "Replenishment policy",
};

export default function ItemPage({ params }: {
  params: Promise<{ id: string; itemId: string }>;
}) {
  const { id, itemId } = use(params);
  const batchId = Number(id);
  const { call, stream } = useApi();
  const { role, user } = useSession();
  const router = useRouter();

  const [d, setD] = useState<ItemDetail | null>(null);
  const [history, setHistory] = useState<Review[]>([]);
  const [triage, setTriage] = useState<TriageResult | null>(null);
  const [similar, setSimilar] = useState<SimilarityResult | null>(null);
  const [justificationTemplates, setJustificationTemplates] = useState<JustificationTemplate[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  // Which action is running, not merely that one is. One shared boolean made
  // every button on the page say "Working…" at once, so the engineer could not
  // tell which of them they were waiting for.
  const [pending, setPending] = useState<"decision" | "triage" | "approve" | null>(null);
  const busy = pending !== null;
  const [runStream, setRunStream] = useState<RunStreamState | null>(null);

  const [decision, setDecision] = useState<"accept" | "override" | "reject">("accept");
  const [fMax, setFMax] = useState(""); const [fRop, setFRop] = useState(""); const [fMin, setFMin] = useState("");
  const [comment, setComment] = useState(""); const [justification, setJustification] = useState("");

  const load = useCallback(async () => {
    try {
      const detail = await call<ItemDetail>(`recommendations/${itemId}?batch_id=${batchId}`);
      const triagePath = `triage/${batchId}/${itemId}?stockroom_id=${encodeURIComponent(
        detail.recommendation.stockroom_id)}`;
      const similarPath = `similarity/${batchId}/${itemId}?stockroom_id=${encodeURIComponent(
        detail.recommendation.stockroom_id)}`;
      const [h, templatePage, triageResult, similarResult] = await Promise.all([
        call<{ reviews: Review[] }>(`history/${itemId}`),
        call<JustificationTemplatePage>("review/justification-templates"),
        can.review(role)
          ? call<TriageResult>(triagePath).catch((e) => {
              if (e instanceof ApiError && e.status === 404) return null;
              throw e;
            })
          : Promise.resolve(null),
        can.review(role)
          ? call<SimilarityResult>(similarPath).catch((e) => {
              if (e instanceof ApiError && e.status === 404) return null;
              throw e;
            })
          : Promise.resolve(null),
      ]);
      setD(detail);
      setFMax(String(detail.recommendation.new_max));
      setFRop(String(detail.recommendation.new_rop));
      setFMin(String(detail.recommendation.new_min));
      setHistory(h.reviews);
      setJustificationTemplates(templatePage.templates);
      setTriage(triageResult);
      setSimilar(similarResult);
      setErr(null);
    } catch (e) { setErr((e as Error).message); }
  }, [call, batchId, itemId, role]);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => { void load(); });
    return () => window.cancelAnimationFrame(frame);
  }, [load]);

  async function submit() {
    setPending("decision"); setErr(null); setNote(null);
    try {
      const body: Record<string, unknown> = { decision, comment, justification };
      if (decision === "override") {
        body.final_max = Number(fMax); body.final_rop = Number(fRop); body.final_min = Number(fMin);
      }
      const r = await call<{ status: string; requires_senior_approval: boolean }>(
        `review/${itemId}?batch_id=${batchId}`,
        { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) },
      );
      setNote(r.requires_senior_approval
        ? `Recorded as ${decision} — now awaiting senior approval before it can be exported.`
        : `Recorded as ${decision}. Status: ${r.status}.`);
      await load();
    } catch (e) { setErr((e as Error).message); }
    finally { setPending(null); }
  }

  /**
   * Triage is the only step that spends model calls — 3 to 7 per item — so it
   * runs here, on demand, for the row actually being read. Never for the batch.
   */
  async function runTriage() {
    setPending("triage"); setErr(null); setNote(null);
    // Held in a local as well as in state: the stream callback fires faster
    // than React commits, so folding onto the state variable would drop events.
    let live = emptyRunStream("Triaging this row");
    setRunStream(live);
    try {
      await stream<TriageStreamEvent>("triage/run/stream", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          batch_id: batchId, item_id: itemId,
          stockroom_id: d?.recommendation.stockroom_id, refresh: true,
        }),
      }, (event) => {
        live = reduceTriageStream(live, event);
        setRunStream(live);
        if (event.type === "complete") {
          const { triaged, llm_calls_used: calls } = event.summary;
          setNote(triaged
            ? `Triaged this item using ${calls} model call${calls === 1 ? "" : "s"}.`
            : "Nothing to triage for this item.");
        }
      });
      if (live.error) setErr(live.error);
      await load();
      setRunStream(null);          // the triage card below is now the result
    } catch (e) { setErr((e as Error).message); setRunStream(null); }
    finally { setPending(null); }
  }

  async function approve() {
    setPending("approve"); setErr(null); setNote(null);
    try {
      await call(`review/${itemId}/approve?batch_id=${batchId}`, { method: "POST" });
      setNote("Senior approval recorded — this row is now eligible for the WINGS export.");
      await load();
    } catch (e) { setErr((e as Error).message); }
    finally { setPending(null); }
  }

  if (err && !d) return <Banner kind="error">{err}</Banner>;
  if (!d) return <ItemSkeleton />;

  const r = d.recommendation;
  const cur = {
    max: Number(d.context.max_qty ?? 0),
    rop: Number(d.context.rop_qty ?? 0),
    min: Number(d.context.min_qty ?? 0),
  };
  const latest = d.latest_review;
  const benchRaw = d.context.factory_recommended_new_max;
  const benchMax = benchRaw === null || benchRaw === undefined || benchRaw === ""
    ? null : Number(benchRaw);
  const canApprove = d.status === "awaiting_senior" && can.approve(role)
                     && latest?.reviewer !== user;
  const selectedTemplate = justificationTemplates.find(
    (template) => template.justification === justification);

  return (
    <div className="flex flex-col gap-5">
      <div className="page-head">
        <button onClick={() => router.back()} className="text-xs" style={{ color: "var(--text-muted)", background: "none", border: "none", cursor: "pointer", padding: 0 }}>
          ← Back to items
        </button>
        <div className="flex flex-wrap items-center gap-3 mt-1">
          <h1 className="text-xl font-semibold font-mono">{r.item_id}</h1>
          <StatusChip status={d.status} />
          <RiskChip level={r.risk_level} />
          <ActionChip action={r.action} />
          <ConsumableChip value={r.consumable} />
          <AgreementChip value={r.agreement} />
          <span className="text-sm tnum" style={{ color: "var(--text-secondary)" }}>
            {fmtUsd(r.exposure_usd)} exposure
          </span>
        </div>
        <p className="text-sm mt-1" style={{ color: "var(--text-secondary)" }}>
          {String(d.context.item_desc ?? "")}
        </p>
      </div>

      {err && <Banner kind="error">{err}</Banner>}
      {note && <Banner kind="success">{note}</Banner>}

      <div className="grid gap-5 lg:grid-cols-[1.1fr_1fr]">
        <div className="flex flex-col gap-5">
          <div className="card p-5">
            <h2 className="text-sm font-semibold mb-3">Engine recommendation</h2>
            <div className="scroll-x">
              <table className="w-full text-sm">
                <thead>
                  <tr><th></th><th className="text-right">Max</th>
                      <th className="text-right">ROP</th><th className="text-right">Min</th></tr>
                </thead>
                <tbody>
                  <tr>
                    <td style={{ color: "var(--text-secondary)" }}>Current (WINGS)</td>
                    <td className="text-right tnum">{cur.max}</td>
                    <td className="text-right tnum">{cur.rop}</td>
                    <td className="text-right tnum">{cur.min}</td>
                  </tr>
                  <tr>
                    <td style={{ color: "var(--text-secondary)" }}>Engine proposes</td>
                    <td className="text-right tnum font-semibold">{r.new_max}</td>
                    <td className="text-right tnum font-semibold">{r.new_rop}</td>
                    <td className="text-right tnum font-semibold">{r.new_min}</td>
                  </tr>
                  {benchMax !== null && (
                    <tr>
                      <td style={{ color: "var(--text-secondary)" }}>Engineer benchmark</td>
                      <td className="text-right tnum">{benchMax}</td>
                      <td className="text-right tnum">{Number(d.context.factory_recommended_new_rop)}</td>
                      <td className="text-right tnum">{Number(d.context.factory_recommended_new_min)}</td>
                    </tr>
                  )}
                  {similar && similar.analogue_max_median !== null && (
                    <tr>
                      <td style={{ color: "var(--text-secondary)" }}>Historical analogues</td>
                      <td className="text-right tnum">{similar.analogue_max_median}</td>
                      <td className="text-right tnum">{similar.analogue_rop_median}</td>
                      <td className="text-right tnum">{similar.analogue_min_median}</td>
                    </tr>
                  )}
                  {latest && (
                    <tr>
                      <td style={{ color: "var(--text-secondary)" }}>Engineer final</td>
                      <td className="text-right tnum">{latest.final_max}</td>
                      <td className="text-right tnum">{latest.final_rop}</td>
                      <td className="text-right tnum">{latest.final_min}</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>

            {similar && similar.analogue_max_median !== null && (
              <p className="text-xs mt-2" style={{ color: "var(--text-muted)" }}>
                Typical Max range {similar.analogue_max_p25}–{similar.analogue_max_p75}
                {" · "}{similar.neighbour_count} peers
                {" · "}confidence {similar.confidence.toFixed(2)}
                {" · "}advisory only, never applied
              </p>
            )}

            <div className="mt-4">
              <div className="text-xs mb-1.5" style={{ color: "var(--text-secondary)" }}>
                Why (confidence {r.confidence.toFixed(2)}, {r.rule_version})
              </div>
              <p className="text-sm mb-2">{r.explanation}</p>
              <ReasonCodes codes={r.reason_code} max={12} />
            </div>
          </div>

          {can.review(role) && !triage && (
            <div className="card p-5">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="mr-auto">
                  <h2 className="text-sm font-semibold">Advisory triage</h2>
                  <p className="text-xs mt-1" style={{ color: "var(--text-muted)" }}>
                    Not run for this item yet. Triage is the only step that calls the
                    model — 3 to 7 calls — so it runs per item, when you ask for it.
                  </p>
                </div>
                <button className="btn btn-primary" onClick={runTriage} disabled={busy}>
                  <BusyLabel busy={pending === "triage"} running="Triaging…"
                             idle="Run triage for this item" />
                </button>
              </div>
            </div>
          )}

          {runStream && <RunStream state={runStream} />}

          {triage && (
            <div className="card p-5">
              <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
                <h2 className="text-sm font-semibold">Advisory triage</h2>
                <div className="flex items-center gap-3">
                  <TriageChip tier={triage.triage_tier} />
                  <span className="text-xs tnum" style={{ color: "var(--text-secondary)" }}>
                    priority {triage.priority_score.toFixed(0)} · confidence {Math.round(triage.confidence * 100)}%
                  </span>
                  {can.review(role) && (
                    <button className="btn text-xs" onClick={runTriage} disabled={busy}
                            title="Re-run triage for this item only">
                      <BusyLabel busy={pending === "triage"} running="Re-running…" idle="Re-run" />
                    </button>
                  )}
                </div>
              </div>
              <p className="text-sm" style={{ lineHeight: 1.5 }}>{triage.rationale}</p>
              {triage.focus_question && (
                <p className="text-sm mt-3 p-3 rounded" style={{ background: "var(--seq-soft)" }}>
                  <strong>Decide:</strong> {triage.focus_question}
                </p>
              )}
              {/* The rationale and focus question above ARE the conclusion. The
                  specialist lines are the working behind it — available, but not
                  competing with it for attention. */}
              {(() => {
                const evidence = ([
                  ["History", triage.history_narrative],
                  ["Demand", triage.demand_narrative],
                  ["Procurement", triage.procurement_narrative],
                ] as const).filter(([, n]) => n);
                if (!evidence.length) return null;
                return (
                  <details className="mt-4">
                    <summary className="text-xs" style={{ color: "var(--text-muted)", cursor: "pointer" }}>
                      Specialist evidence ({evidence.length})
                    </summary>
                    <div className="flex flex-col gap-3 mt-3">
                      {evidence.map(([label, narrative]) => (
                        <div key={label} style={{ borderLeft: "2px solid var(--seq)", paddingLeft: 10 }}>
                          <div className="text-xs font-medium mb-1">{label}</div>
                          <p className="text-xs" style={{ color: "var(--text-secondary)",
                                                          whiteSpace: "pre-wrap" }}>
                            {narrative}
                          </p>
                        </div>
                      ))}
                    </div>
                  </details>
                );
              })()}
              <details className="text-xs mt-4">
                <summary style={{ color: "var(--text-muted)", cursor: "pointer" }}>
                  {triage.sources.length} grounded sources · {triage.provider}:{triage.model}
                </summary>
                <pre className="mt-2 p-3 rounded scroll-x" style={{ background: "var(--seq-soft)" }}>
                  {JSON.stringify(triage.sources, null, 2)}
                </pre>
              </details>
            </div>
          )}

          <div className="card p-5">
            <h2 className="text-sm font-semibold mb-3">Decision</h2>
            {!can.review(role) ? (
              <p className="text-sm" style={{ color: "var(--text-muted)" }}>
                Role <code>{role}</code> has read-only access. Switch to engineer, senior
                or admin to record a decision.
              </p>
            ) : (
              <div className="flex flex-col gap-3">
                <div className="flex gap-2 flex-wrap">
                  {(["accept", "override", "reject"] as const).map((dv) => (
                    <button key={dv}
                            className={`btn text-xs ${decision === dv ? "btn-primary" : ""}`}
                            onClick={() => setDecision(dv)}>
                      {dv === "accept" ? "Accept engine values"
                        : dv === "override" ? "Override with my values"
                        : "Reject (keep current)"}
                    </button>
                  ))}
                </div>

                {decision === "override" && (
                  <div className="flex gap-3 flex-wrap">
                    {([["Max", fMax, setFMax], ["ROP", fRop, setFRop], ["Min", fMin, setFMin]] as const)
                      .map(([lbl, val, set]) => (
                        <label key={lbl} className="flex flex-col gap-1 text-xs">
                          <span style={{ color: "var(--text-secondary)" }}>{lbl}</span>
                          <input className="field w-24 tnum" type="number" min={0} value={val}
                                 onChange={(e) => set(e.target.value)} />
                        </label>
                      ))}
                    <p className="text-xs self-end pb-1.5" style={{ color: "var(--text-muted)" }}>
                      Must satisfy Max ≥ ROP ≥ Min.
                    </p>
                  </div>
                )}

                <label className="flex flex-col gap-1 text-xs">
                  <span style={{ color: "var(--text-secondary)" }}>Comment</span>
                  <input className="field" value={comment} onChange={(e) => setComment(e.target.value)}
                         placeholder="What you saw" />
                </label>
                <fieldset className="justification-picker">
                  <legend>Justification template</legend>
                  <p>Choose the reason for this row. Hover or focus an option for its definition.</p>
                  <div className="justification-options">
                    {justificationTemplates.map((template, index) => {
                      const definitionId = `justification-definition-${index}`;
                      const selected = justification === template.justification;
                      return (
                        <button key={template.justification} type="button"
                                className={`justification-option${selected ? " is-selected" : ""}`}
                                aria-label={template.justification}
                                aria-describedby={definitionId}
                                aria-pressed={selected}
                                disabled={busy}
                                onClick={() => setJustification(template.justification)}>
                          <span>{template.justification}</span>
                          <span className="justification-info" aria-hidden>i</span>
                          <span id={definitionId} className="justification-tooltip" role="tooltip">
                            {template.definition}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                  {selectedTemplate && (
                    <div className="justification-selected" aria-live="polite">
                      <strong>{selectedTemplate.justification}</strong>
                      <span>{selectedTemplate.definition}</span>
                      <button type="button" onClick={() => setJustification("")}
                              disabled={busy}>Clear</button>
                    </div>
                  )}
                </fieldset>

                {(decision === "override" || r.risk_level === "High") && (
                  <Banner kind="info">
                    {decision === "override" ? "Overrides" : "High-risk items"} require a
                    second person with senior rights to approve before export.
                  </Banner>
                )}

                <div className="flex gap-2">
                  <button className="btn btn-primary" onClick={submit} disabled={busy}>
                    <BusyLabel busy={pending === "decision"} running="Saving…"
                               idle="Record decision" />
                  </button>
                  {d.status === "awaiting_senior" && (
                    <button className="btn" onClick={approve} disabled={busy || !canApprove}
                            title={!can.approve(role) ? "Needs senior or admin role"
                                   : latest?.reviewer === user ? "You cannot approve your own review"
                                   : "Approve this review"}>
                      <BusyLabel busy={pending === "approve"} running="Approving…"
                                 idle="Approve as senior" />
                    </button>
                  )}
                </div>
                {d.status === "awaiting_senior" && !canApprove && (
                  <p className="text-xs" style={{ color: "var(--text-muted)" }}>
                    {latest?.reviewer === user
                      ? `You (${user}) recorded this review — a different person must approve it.`
                      : `Role ${role} cannot approve. Switch to senior or admin.`}
                  </p>
                )}
              </div>
            )}
          </div>
        </div>

        <div className="flex flex-col gap-5">
          <div className="card p-5">
            <h2 className="text-sm font-semibold mb-3">Input context</h2>
            <p className="text-xs mb-3" style={{ color: "var(--text-muted)" }}>
              What the engine saw. Output and prior-decision columns are withheld from
              the engine by design (no target leakage).
            </p>
            <div className="scroll-x">
              <table className="w-full text-sm">
                <tbody>
                  {Object.entries(CONTEXT_LABELS).map(([k, lbl]) => (
                    <tr key={k}>
                      <td style={{ color: "var(--text-secondary)" }}>{lbl}</td>
                      <td className="text-right tnum">{String(d.context[k] ?? "—")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {similar && (
            <div className="card p-5">
              <h2 className="text-sm font-semibold mb-1">Similar parts historically</h2>
              <p className="text-xs mb-3" style={{ color: "var(--text-muted)" }}>
                Different items with comparable characteristics — not this item&apos;s
                own history. Advisory evidence; it never sets Min/ROP/Max.
              </p>

              {similar.part_category ? (
                <p className="text-xs mb-3 p-2 rounded" style={{ background: "var(--seq-soft)" }}>
                  Peers restricted to <strong>{similar.part_category}</strong> — a part
                  of a different known category is never shown here.
                </p>
              ) : (
                <Banner kind="info">
                  No part category matched this description, so peers were not
                  restricted by category. Add a rule on the Config page to sharpen this.
                </Banner>
              )}

              {similar.advisory_codes.includes("NO_RELIABLE_ANALOGUE") && (
                <Banner kind="warning">
                  This part is unusual against {similar.pool_size.toLocaleString()} reviewed
                  {" "}peers — {similar.neighbour_count} close match
                  {similar.neighbour_count === 1 ? "" : "es"} found. Manual review;
                  no analogue range is shown.
                </Banner>
              )}
              {similar.advisory_codes.includes("ANALOGUE_DIVERGENCE") && (
                <Banner kind="info">
                  The engine proposes Max {r.new_max}, but comparable parts settled
                  around Max {similar.analogue_max_median}. Worth a second look.
                </Banner>
              )}

              {similar.neighbours.length === 0 ? (
                <p className="text-sm py-2" style={{ color: "var(--text-muted)" }}>
                  No sufficiently similar reviewed parts found.
                </p>
              ) : (
                <div className="scroll-x mt-3">
                  <table className="w-full text-sm">
                    <thead>
                      <tr>
                        <th className="text-left">Similar part</th>
                        <th className="text-left">Why similar</th>
                        <th className="text-right">Final</th>
                        <th className="text-left">Historical reason</th>
                      </tr>
                    </thead>
                    <tbody>
                      {similar.neighbours.slice(0, 5).map((n) => (
                        <tr key={n.neighbour_rank}>
                          <td className="font-mono text-xs">{n.neighbour_item_id}</td>
                          <td className="text-xs" style={{ color: "var(--text-secondary)" }}>
                            {n.similarity_reasons || "—"}
                          </td>
                          <td className="text-right tnum">
                            {n.neighbour_final_max === null ? "—" : `Max ${n.neighbour_final_max}`}
                          </td>
                          <td className="text-xs" style={{ color: "var(--text-secondary)" }}>
                            {n.neighbour_justification || n.neighbour_comment || "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              <details className="text-xs mt-4">
                <summary style={{ color: "var(--text-muted)", cursor: "pointer" }}>
                  {similar.similarity_model_version} · outlier score{" "}
                  {similar.outlier_score.toFixed(2)} · nearest distance{" "}
                  {similar.nearest_distance === null ? "—" : similar.nearest_distance.toFixed(3)}
                </summary>
                <pre className="mt-2 p-3 rounded scroll-x" style={{ background: "var(--seq-soft)" }}>
                  {JSON.stringify(similar.neighbours, null, 2)}
                </pre>
              </details>
            </div>
          )}

          <div className="card p-5">
            <h2 className="text-sm font-semibold mb-1">This item previously</h2>
            <p className="text-xs mb-3" style={{ color: "var(--text-muted)" }}>
              The memory layer — every decision on this item, across all batches.
            </p>
            {history.length === 0 ? (
              <p className="text-sm py-2" style={{ color: "var(--text-muted)" }}>
                No prior reviews recorded for this item.
              </p>
            ) : (
              <div className="flex flex-col gap-3">
                {history.map((h) => (
                  <div key={h.review_id} className="text-xs"
                       style={{ borderLeft: "2px solid var(--seq)", paddingLeft: 10 }}>
                    <div className="flex flex-wrap gap-x-2">
                      <span className="font-medium">{h.decision}</span>
                      <span style={{ color: "var(--text-secondary)" }}>by {h.reviewer} ({h.role})</span>
                      <span style={{ color: "var(--text-muted)" }}>{h.reviewed_at}</span>
                    </div>
                    <div className="tnum mt-0.5" style={{ color: "var(--text-secondary)" }}>
                      {h.current_max}/{h.current_rop}/{h.current_min} → {h.final_max}/{h.final_rop}/{h.final_min}
                      {" · "}{h.rule_version}
                    </div>
                    {h.senior_approved_by && (
                      <div className="mt-0.5" style={{ color: "var(--success-text)" }}>
                        ✓ approved by {h.senior_approved_by} at {h.senior_approved_at}
                      </div>
                    )}
                    {(h.comment || h.justification) && (
                      <div className="mt-0.5" style={{ color: "var(--text-muted)" }}>
                        {[h.justification, h.comment].filter(Boolean).join(" — ")}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * The item page fires five requests before it can show anything. A bare
 * "Loading…" for that long reads as a stall, so the wait shows the shape of the
 * page that is coming -- same columns, same card count, so nothing jumps when
 * the data lands.
 */
function ItemSkeleton() {
  return (
    <div className="flex flex-col gap-5" role="status" aria-busy="true">
      <span className="sr-only">Loading item…</span>
      <div className="skeleton-stack">
        <Skeleton w="220px" h="1.35rem" />
        <Skeleton w="52%" h="0.8rem" />
      </div>
      <div className="grid gap-5 lg:grid-cols-[1.1fr_1fr]">
        <div className="flex flex-col gap-5">
          <CardSkeleton lines={5} label="Loading engine recommendation" />
          <CardSkeleton lines={3} label="Loading advisory triage" />
          <CardSkeleton lines={4} label="Loading decision form" />
        </div>
        <div className="flex flex-col gap-5">
          <CardSkeleton lines={6} label="Loading input context" />
          <CardSkeleton lines={4} label="Loading peer evidence" />
        </div>
      </div>
    </div>
  );
}
