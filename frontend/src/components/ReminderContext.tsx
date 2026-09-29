"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useApi } from "@/lib/api";
import { can } from "@/lib/session";
import { reminderLabel, type ReminderPage } from "@/lib/reminders";
import { ReminderEditor } from "./ReminderEditor";
import "./reminders.css";

export function ReminderContext({ batchId, itemId, stockroomId, canCreate = false }: {
  batchId: number; itemId?: string; stockroomId?: string; canCreate?: boolean;
}) {
  const { call, user, role } = useApi();
  const [page, setPage] = useState<ReminderPage | null>(null);
  const [error, setError] = useState("");
  const [editor, setEditor] = useState(false);
  const load = useCallback(async (signal?: AbortSignal) => {
    const query = new URLSearchParams({ batch_id: String(batchId), limit: "5" });
    if (itemId !== undefined) query.set("item_id", itemId);
    if (stockroomId !== undefined) query.set("stockroom_id", stockroomId);
    try {
      const result = await call<ReminderPage>("reminders?" + query, { signal });
      if (!signal?.aborted) { setPage(result); setError(""); }
    } catch { if (!signal?.aborted) setError("Reminders could not load."); }
  }, [call, batchId, itemId, stockroomId]);
  useEffect(() => {
    const controller = new AbortController();
    const refresh = () => void load(controller.signal);
    const frame = requestAnimationFrame(refresh);
    window.addEventListener("bom:reminders-changed", refresh);
    return () => { cancelAnimationFrame(frame); controller.abort(); window.removeEventListener("bom:reminders-changed", refresh); };
  }, [load]);
  if (!itemId && !error && !page?.total) return null;
  return <section className="reminder-context" aria-label="Engineer reminders for this review">
    <div className="reminder-context-head"><strong>{itemId ? "Your reminders for this part" : "Your reminders in this workspace"}
      {page?.total ? " (" + page.total + ")" : ""}</strong>
      {canCreate && can.review(role) && <button className="btn text-xs" onClick={() => setEditor(true)}>Add reminder</button>}
    </div>
    {error && <p role="status">{error} <button className="btn text-xs" onClick={() => void load()}>Retry</button></p>}
    {page && !page.total && <p className="reminder-field-note">Save a follow-up for a date or the next review cycle.</p>}
    {!!page?.total && <ul>{page.reminders.map((reminder) => <li key={reminder.reminder_id}>
      <Link href={"/reminders#reminder-" + reminder.reminder_id}>{reminder.title}</Link>
      <small>{reminder.item_id} · {reminder.stockroom_id} · {reminderLabel(reminder)}</small>
    </li>)}</ul>}
    {page && page.total > 5 && <Link href="/reminders">View all reminders</Link>}
    {editor && <ReminderEditor key={user} batchId={batchId} itemId={itemId} stockroomId={stockroomId}
      onClose={() => setEditor(false)} onSaved={() => { setEditor(false); void load(); }} />}
  </section>;
}
