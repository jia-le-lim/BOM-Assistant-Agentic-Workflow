export interface EngineerReminder {
  reminder_id: string;
  owner_user: string;
  title: string;
  item_id: string;
  stockroom_id: string;
  note: string;
  timing: "next_cycle" | "date";
  due_date: string | null;
  status: "open" | "completed" | "dismissed";
  origin_batch_id: number | null;
  matched_batch_id: number | null;
  has_image: boolean;
  is_due: boolean;
  created_at: string;
  updated_at: string;
}
export interface ReminderPage { reminders: EngineerReminder[]; total: number }
export interface ReminderExtraction {
  extraction: {
    title: string;
    item_id: string | null;
    stockroom_id: string | null;
    description: string | null;
    request_text: string | null;
    current_max: number | null;
    current_rop: number | null;
    proposed_change_text: string | null;
    uncertainties: string[];
  };
  model: string | null;
  warning: string | null;
}
export function remindersChanged() {
  window.dispatchEvent(new Event("bom:reminders-changed"));
}
export function reminderLabel(reminder: EngineerReminder) {
  if (reminder.status === "completed") return "Completed";
  if (reminder.status === "dismissed") return "Dismissed";
  if (reminder.timing === "date") return reminder.is_due ? "Due" : "Scheduled";
  if (!reminder.item_id || !reminder.stockroom_id) return "Needs part details";
  return reminder.matched_batch_id ? "Ready for review" : "Waiting for next cycle";
}
