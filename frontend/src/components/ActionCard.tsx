import type { StagedAction } from "@/lib/types";

const TITLE: Record<StagedAction["kind"], string> = {
  confirm_pending: "Confirm staged proposal",
  discard_pending: "Discard staged proposal",
};

function level(label: string, value: number | null) {
  if (value === null) return null;
  return (
    <span key={label}>
      <small>{label}</small>
      <strong>{value}</strong>
    </span>
  );
}

/**
 * The card the agent stages and a human presses.
 *
 * Presentational on purpose: it calls nothing. The chat page owns `confirm`
 * and `discard`, which post to the same review endpoints the pending tray
 * posts to, under the engineer's own role — which is what keeps an LLM parse
 * out of `review_history`.
 */
export function ActionCard({ action, busy, onConfirm, onDiscard }: {
  action: StagedAction;
  busy?: boolean;
  onConfirm: (action: StagedAction) => void;
  onDiscard: (action: StagedAction) => void;
}) {
  const levels = [
    level("Max", action.proposed_max),
    level("ROP", action.proposed_rop),
    level("Min", action.proposed_min),
  ].filter(Boolean);

  return (
    <section className="action-card" aria-label={TITLE[action.kind]}>
      <div className="action-card-head">
        <strong>{TITLE[action.kind]}</strong>
        <span>
          Item {action.item_id}
          {action.pending_id === null ? "" : ` · pending #${action.pending_id}`}
        </span>
      </div>

      {levels.length > 0 && (
        <div className="action-card-levels">{levels}</div>
      )}

      <p className="action-card-caveat">
        {action.kind === "confirm_pending"
          ? "Nothing has been recorded yet. Confirming records a review decision, and an override still needs senior approval before it can be exported."
          : "Nothing has been recorded yet. Discarding closes this proposal without recording any decision."}
      </p>

      {/* The primary button follows `kind`. A discard card that also offered
          Confirm would put the button that writes to review_history one
          mis-click away on the card the engineer asked to throw out. */}
      <div className="action-card-buttons">
        {action.kind === "confirm_pending" ? (
          <button type="button" disabled={busy}
                  aria-label={`Confirm the staged proposal for item ${action.item_id}`}
                  onClick={() => onConfirm(action)}>
            Confirm
          </button>
        ) : (
          <button type="button" disabled={busy}
                  aria-label={`Discard the staged proposal for item ${action.item_id}`}
                  onClick={() => onDiscard(action)}>
            Discard
          </button>
        )}
      </div>
    </section>
  );
}
