"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useApi } from "@/lib/api";
import { can } from "@/lib/session";
import { reminderLabel, remindersChanged, type EngineerReminder, type ReminderPage } from "@/lib/reminders";
import { ReminderEditor } from "@/components/ReminderEditor";
import { ReminderScreenshot } from "@/components/ReminderScreenshot";
import "@/components/reminders.css";

export default function RemindersPage() {
  const { call, role, user } = useApi();
  const [filter, setFilter] = useState("open");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<ReminderPage | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [editor, setEditor] = useState<EngineerReminder | "new" | null>(null);
  const [image, setImage] = useState<File | undefined>();
  const [notice, setNotice] = useState("");
  const [shownImages, setShownImages] = useState<Set<string>>(() => new Set());
  const editable = can.review(role);

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true); setError("");
    const query = new URLSearchParams({ status: filter === "due" ? "open" : filter,
      limit: "25", offset: String(offset) });
    if (filter === "due") query.set("due_only", "true");
    try {
      const result = await call<ReminderPage>("reminders?" + query, { signal });
      if (!signal?.aborted) {
        setPage(result);
        if (offset > 0 && result.reminders.length === 0) setOffset(Math.max(0, Math.ceil(result.total / 25) * 25 - 25));
      }
    } catch (e) { if (!signal?.aborted) setError((e as Error).message); }
    finally { if (!signal?.aborted) setLoading(false); }
  }, [call, filter, offset]);

  useEffect(() => {
    const controller = new AbortController();
    const refresh = () => void load(controller.signal);
    const frame = requestAnimationFrame(refresh);
    window.addEventListener("bom:reminders-changed", refresh);
    return () => {
      cancelAnimationFrame(frame); controller.abort();
      window.removeEventListener("bom:reminders-changed", refresh);
    };
  }, [load]);

  async function status(reminder: EngineerReminder, value: EngineerReminder["status"]) {
    setBusy(reminder.reminder_id); setError("");
    try {
      await call("reminders/" + reminder.reminder_id + "/status", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: value }),
      });
      remindersChanged(); await load();
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(null); }
  }
  function create(file?: File) { setImage(file); setEditor("new"); setNotice(""); }

  return <div className="reminder-page" onPaste={(event) => {
    if (editor || !editable) return;
    const file = Array.from(event.clipboardData.files).find((candidate) => candidate.type.startsWith("image/"));
    if (file) { event.preventDefault(); create(file); }
  }}>
    <header className="reminder-page-head">
      <div><p className="reminder-eyebrow">BETWEEN REVIEW CYCLES</p><h1>Engineer reminders</h1>
        <p>Your follow-ups stay here until you complete them.</p></div>
      {editable && <button className="btn btn-primary" onClick={() => create()}>New reminder</button>}
    </header>
    <div className="reminder-tabs" role="group" aria-label="Filter reminders">
      {[["open", "Open"], ["due", "Due now"], ["completed", "Completed"], ["dismissed", "Dismissed"]].map(([value, label]) =>
        <button key={value} className="btn" aria-pressed={filter === value}
          onClick={() => { setFilter(value); setOffset(0); }}>{label}</button>)}
    </div>
    {notice && <p role="status" className="reminder-notice">{notice}</p>}
    {error && <p role="alert" className="reminder-error">{error} <button className="btn" onClick={() => void load()}>Retry</button></p>}
    {loading ? <p role="status">Loading reminders…</p> : page && page.reminders.length === 0 ?
      <div className="reminder-empty"><h2>{filter === "open" ? "Keep the next review in mind" : "No reminders in this view"}</h2>
        <p>{filter === "open" ? "Capture a screenshot or write a request. Nyra will keep it ready for a date or the next matching BOM upload."
          : filter === "due" ? "Date reminders appear here when due. Cycle reminders appear after a new BOM contains the exact part and stockroom."
          : "Your reminders will appear here when their status changes."}</p>
        {editable && filter === "open" && <button className="btn" onClick={() => create()}>Capture a reminder</button>}
      </div> :
      <div className="reminder-list" aria-label="Your reminders">
        {page?.reminders.map((reminder) => <article id={"reminder-" + reminder.reminder_id} key={reminder.reminder_id}
          className={"reminder-card" + (reminder.is_due ? " is-due" : "")}>
          <div className="reminder-card-head"><h2>{reminder.title}</h2><span className="reminder-state">{reminderLabel(reminder)}</span></div>
          <div className="reminder-meta">
            <span>Part: {reminder.item_id || "To confirm"}</span><span>Stockroom: {reminder.stockroom_id || "To confirm"}</span>
            <span>{reminder.timing === "date" ? "Due " + reminder.due_date : "Next matching review cycle"}</span>
          </div>
          {reminder.note && <p className="reminder-note">{reminder.note}</p>}
          {shownImages.has(reminder.reminder_id) && <ReminderScreenshot reminderId={reminder.reminder_id} />}
          <div className="reminder-actions">
            {reminder.matched_batch_id && <Link className="btn" href={"/batches/" + reminder.matched_batch_id + "?status=&q=" + encodeURIComponent(reminder.item_id)}>
              Open matching review</Link>}
            {reminder.has_image && <button className="btn" aria-expanded={shownImages.has(reminder.reminder_id)}
              onClick={() => setShownImages((old) => { const next = new Set(old); if (next.has(reminder.reminder_id)) next.delete(reminder.reminder_id); else next.add(reminder.reminder_id); return next; })}>
              {shownImages.has(reminder.reminder_id) ? "Hide screenshot" : "View screenshot"}</button>}
            {editable && <>
              <button className="btn" disabled={busy !== null} onClick={() => { setImage(undefined); setEditor(reminder); }}>Edit</button>
              {reminder.status === "open" ? <>
                <button className="btn btn-primary" disabled={busy !== null} onClick={() => void status(reminder, "completed")}>
                  {busy === reminder.reminder_id ? "Saving…" : "Mark complete"}</button>
                <button className="btn" disabled={busy !== null} onClick={() => void status(reminder, "dismissed")}>Dismiss</button>
              </> : <button className="btn" disabled={busy !== null} onClick={() => void status(reminder, "open")}>Reopen</button>}
            </>}
          </div>
        </article>)}
      </div>}
    {page && page.total > 25 && <div className="reminder-pagination">
      <button className="btn" disabled={loading || offset === 0} onClick={() => setOffset(Math.max(0, offset - 25))}>Previous</button>
      <span>{offset + 1}–{Math.min(offset + 25, page.total)} of {page.total}</span>
      <button className="btn" disabled={loading || offset + 25 >= page.total} onClick={() => setOffset(offset + 25)}>Next</button>
    </div>}
    {editor && <ReminderEditor key={user + (editor === "new" ? "new" : editor.reminder_id)}
      initialFile={image} reminder={editor === "new" ? undefined : editor}
      onClose={() => setEditor(null)} onSaved={() => { setEditor(null); setNotice("Reminder saved."); void load(); }} />}
  </div>;
}
