"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { fmtUsd, useApi } from "@/lib/api";
import { can, useSession } from "@/lib/session";
import type {
  ItemDetail, JustificationTemplate, JustificationTemplatePage, Review,
} from "@/lib/types";
import { ActionChip, AgreementChip, Banner, ConsumableChip, ReasonCodes, RiskChip, Spinner, StatusChip } from "@/components/ui";

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
  const { call } = useApi();
  const { role, user } = useSession();
  const router = useRouter();

  const [d, setD] = useState<ItemDetail | null>(null);
  const [history, setHistory] = useState<Review[]>([]);
  const [justificationTemplates, setJustificationTemplates] = useState<JustificationTemplate[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [decision, setDecision] = useState<"accept" | "override" | "reject">("accept");
  const [fMax, setFMax] = useState(""); const [fRop, setFRop] = useState(""); const [fMin, setFMin] = useState("");
  const [comment, setComment] = useState(""); const [justification, setJustification] = useState("");

  const load = useCallback(async () => {
    try {
      const [detail, h, templatePage] = await Promise.all([
        call<ItemDetail>(`recommendations/${itemId}?batch_id=${batchId}`),
        call<{ reviews: Review[] }>(`history/${itemId}`),
        call<JustificationTemplatePage>("review/justification-templates"),
      ]);
      setD(detail);
      setFMax(String(detail.recommendation.new_max));
      setFRop(String(detail.recommendation.new_rop));
      setFMin(String(detail.recommendation.new_min));
      setHistory(h.reviews);
      setJustificationTemplates(templatePage.templates);
      setErr(null);
    } catch (e) { setErr((e as Error).message); }
  }, [call, batchId, itemId]);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => { void load(); });
    return () => window.cancelAnimationFrame(frame);
  }, [load]);

  async function submit() {
    setBusy(true); setErr(null); setNote(null);
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
    finally { setBusy(false); }
  }

  async function approve() {
    setBusy(true); setErr(null); setNote(null);
    try {
      await call(`review/${itemId}/approve?batch_id=${batchId}`, { method: "POST" });
      setNote("Senior approval recorded — this row is now eligible for the WINGS export.");
      await load();
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  if (err && !d) return <Banner kind="error">{err}</Banner>;
  if (!d) return <Spinner />;

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
      <div>
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

            <div className="mt-4">
              <div className="text-xs mb-1.5" style={{ color: "var(--text-secondary)" }}>
                Why (confidence {r.confidence.toFixed(2)}, {r.rule_version})
              </div>
              <p className="text-sm mb-2">{r.explanation}</p>
              <ReasonCodes codes={r.reason_code} max={12} />
            </div>
          </div>

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
                    {busy ? "Saving…" : "Record decision"}
                  </button>
                  {d.status === "awaiting_senior" && (
                    <button className="btn" onClick={approve} disabled={busy || !canApprove}
                            title={!can.approve(role) ? "Needs senior or admin role"
                                   : latest?.reviewer === user ? "You cannot approve your own review"
                                   : "Approve this review"}>
                      Approve as senior
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

          <div className="card p-5">
            <h2 className="text-sm font-semibold mb-1">Review history</h2>
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
