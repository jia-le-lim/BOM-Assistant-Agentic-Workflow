"use client";

import { useRef } from "react";
import { fmtUsd } from "@/lib/api";
import type { AssistResult, Recommendation } from "@/lib/types";
import { AssistChip, RiskChip, StatusChip } from "./ui";
import { ItemReview, type ReviewDraft } from "./ItemReview";
import { transitionReview } from "@/lib/review-motion";

export const reviewRowKey = (row: { item_id: string; stockroom_id: string }) =>
  `${row.item_id}::${row.stockroom_id}`;

export function ReviewSplitView({ batchId, rows, activeKey, onActivate, verdicts,
  selected, onToggle, canReview, disabled, onReviewed, onBusyChange, onDraftChange, drafts, revision, blockedKeys,
}: {
  batchId: number; rows: Recommendation[]; activeKey: string | null;
  onActivate: (key: string) => void; verdicts: Map<string, AssistResult>;
  selected: Set<string>; onToggle: (key: string) => void;
  canReview: boolean; disabled: boolean; onReviewed: () => Promise<void>;
  onBusyChange: (busy: boolean) => void; drafts: Map<string, ReviewDraft>;
  revision: number;
  blockedKeys: Set<string>; onDraftChange: (key: string, overriding: boolean) => void;
}) {
  const detail = useRef<HTMLDivElement>(null);
  const row = rows.find((r) => reviewRowKey(r) === activeKey) ?? rows[0];
  if (!row) return null;
  const active = reviewRowKey(row);
  const assist = verdicts.get(active);

  function activate(key: string) {
    transitionReview("item", () => {
      onActivate(key);
      if (key !== active) detail.current?.scrollTo({ top: 0, behavior: "instant" });
    }, key !== active);
  }

  return (
    <div className="review-split">
      <nav className="review-item-list" aria-label="Items in the review queue">
        <div className="review-list-heading">Items <span>{rows.length} on this page</span></div>
        <div className="review-list-scroll">
          {rows.map((r) => {
            const key = reviewRowKey(r);
            return (
              <div key={key} data-review-morph={`row:${key}`} className={`review-list-row${key === active ? " is-active" : ""}`}>
                {canReview && <input type="checkbox" checked={selected.has(key)}
                  disabled={disabled || r.status !== "pending_review" || blockedKeys.has(key)}
                  title={blockedKeys.has(key) ? "This item has edited values. Record its individual decision to use them." : undefined}
                  onChange={() => onToggle(key)}
                  aria-label={`Select ${r.item_id} in ${r.stockroom_id}`} />}
                <button type="button" className="review-item-button" disabled={disabled}
                  aria-current={key === active ? "true" : undefined}
                  aria-controls="review-item-detail"
                  onClick={() => activate(key)}>
                  <span className="review-item-title"><strong data-review-morph={`title:${key}`}>{r.item_id}</strong><RiskChip level={r.risk_level} compact /></span>
                  <span className="review-item-description" data-review-morph={`description:${key}`} title={r.item_desc}>{r.item_desc || "No description"}</span>
                  <span className="review-item-meta" data-review-morph={`meta:${key}`}><span>{r.stockroom_id}</span><span>{fmtUsd(r.exposure_usd)}</span><StatusChip status={r.status} compact /></span>
                </button>
              </div>
            );
          })}
        </div>
      </nav>
      <section className="review-detail" id="review-item-detail" aria-label={`Details for ${row.item_id} in ${row.stockroom_id}`}
        data-assistant-item-id={row.item_id} data-assistant-stockroom-id={row.stockroom_id}>
        <header className="review-detail-header">
          <div><span className="review-detail-eyebrow">ITEM DETAIL · {row.stockroom_id}</span>
            <h3 className="font-mono font-semibold">{row.item_id}</h3></div>
          <StatusChip status={row.status} />
        </header>
        <div className="review-detail-scroll" ref={detail} tabIndex={0} aria-label="Item detail content">
          <div className="review-at-a-glance" aria-label="Item values at a glance">
            <div><span>Current Max / ROP</span><strong>{row.current_max ?? "—"} / {row.current_rop ?? "—"}</strong></div>
            <div><span>Engine Max / ROP</span><strong>{row.new_max} / {row.new_rop}</strong></div>
            <div><span>Assist Max / ROP</span><strong>{assist?.suggested_max ?? "—"} / {assist?.suggested_rop ?? "—"}</strong></div>
          </div>
          {assist && <div className="review-assist-preview"><AssistChip verdict={assist.verdict} />
            {assist.narrative && <p>{assist.narrative}</p>}</div>}
          <fieldset disabled={disabled} className="review-detail-fields">
            <ItemReview key={`${active}:${revision}`} batchId={batchId} itemId={row.item_id} stockroomId={row.stockroom_id}
              embedded onReviewed={onReviewed} onBusyChange={onBusyChange} onDraftChange={onDraftChange} drafts={drafts} />
          </fieldset>
        </div>
      </section>
    </div>
  );
}
