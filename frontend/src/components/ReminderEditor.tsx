"use client";
/* eslint-disable @next/next/no-img-element -- Local blobs and authenticated evidence bypass the image optimizer. */

import { useCallback, useEffect, useRef, useState } from "react";
import { useApi } from "@/lib/api";
import { remindersChanged, type EngineerReminder, type ReminderExtraction } from "@/lib/reminders";
import { ReminderScreenshot } from "./ReminderScreenshot";
import "./reminders.css";

export function ReminderEditor({ initialFile, initialNote = "", reminder, batchId, itemId = "", stockroomId = "", onClose, onSaved }: {
  initialFile?: File;
  initialNote?: string;
  reminder?: EngineerReminder;
  batchId?: number | null;
  itemId?: string;
  stockroomId?: string;
  onClose: () => void;
  onSaved: (reminder: EngineerReminder) => void;
}) {
  const { call, user } = useApi();
  const dialog = useRef<HTMLDialogElement>(null);
  const picker = useRef<HTMLInputElement>(null);
  const request = useRef<AbortController | null>(null);
  const [requestId] = useState(() => crypto.randomUUID());
  const [title, setTitle] = useState(reminder?.title ?? "");
  const [item, setItem] = useState(reminder?.item_id ?? itemId);
  const [room, setRoom] = useState(reminder?.stockroom_id ?? stockroomId);
  const [note, setNote] = useState(reminder?.note ?? initialNote);
  const [timing, setTiming] = useState<"next_cycle" | "date">(reminder?.timing ?? "next_cycle");
  const [dueDate, setDueDate] = useState(reminder?.due_date ?? "");
  const [file, setFile] = useState<File | null>(initialFile ?? null);
  const [preview, setPreview] = useState("");
  const [reading, setReading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [checked, setChecked] = useState(false);
  const [warning, setWarning] = useState<string | null>(null);
  const [uncertainties, setUncertainties] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const el = dialog.current;
    el?.showModal();
    return () => { el?.close(); request.current?.abort(); };
  }, []);
  useEffect(() => {
    if (!file) return;
    const url = URL.createObjectURL(file);
    const frame = requestAnimationFrame(() => setPreview(url));
    return () => { cancelAnimationFrame(frame); URL.revokeObjectURL(url); };
  }, [file]);

  const readImage = useCallback(async (image: File) => {
    if (image.size > 5 * 1024 * 1024) { setError("Choose an image smaller than 5 MB."); return; }
    if (!["image/png", "image/jpeg", "image/webp"].includes(image.type)) {
      setError("Choose a PNG, JPEG, or WebP screenshot."); return;
    }
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setFile(image); setPreview(""); setReading(true); setChecked(false);
    setTitle(""); setItem(itemId); setRoom(stockroomId); setNote(initialNote);
    setError(null); setWarning(null); setUncertainties([]);
    const body = new FormData(); body.set("file", image);
    try {
      const result = await call<ReminderExtraction>("reminders/extract", {
        method: "POST", body, signal: controller.signal,
      });
      if (controller.signal.aborted) return;
      const d = result.extraction;
      setTitle(d.title);
      setItem(d.item_id ?? itemId);
      setRoom(d.stockroom_id ?? stockroomId);
      setNote([
        initialNote.trim() && "Engineer note: " + initialNote.trim(),
        d.description,
        d.request_text && "Request: " + d.request_text,
        (d.current_max !== null || d.current_rop !== null)
          && "Screenshot values: Max " + (d.current_max ?? "unclear") + " / ROP " + (d.current_rop ?? "unclear"),
        d.proposed_change_text && "Requested change: " + d.proposed_change_text,
        ...d.uncertainties.map((value) => "Check: " + value),
      ].filter(Boolean).join("\n"));
      setWarning(result.warning);
      setUncertainties(d.uncertainties);
    } catch (e) {
      if (!controller.signal.aborted) setWarning((e as Error).message + " You can enter the details manually.");
    } finally { if (!controller.signal.aborted) setReading(false); }
  }, [call, itemId, stockroomId, initialNote]);

  useEffect(() => {
    if (!initialFile) return;
    const frame = requestAnimationFrame(() => void readImage(initialFile));
    return () => cancelAnimationFrame(frame);
  }, [initialFile, readImage]);

  function paste(event: React.ClipboardEvent) {
    if (reminder || reading || saving) return;
    const image = Array.from(event.clipboardData.files).find((candidate) => candidate.type.startsWith("image/"));
    if (image) { event.preventDefault(); void readImage(image); }
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (reading || saving || (file && !checked)) return;
    setSaving(true); setError(null);
    const payload = { request_id: requestId, title, item_id: item, stockroom_id: room,
      note, timing, due_date: timing === "date" ? dueDate : null,
      origin_batch_id: reminder?.origin_batch_id ?? batchId ?? null };
    try {
      let saved: EngineerReminder;
      if (reminder) {
        saved = await call<EngineerReminder>("reminders/" + reminder.reminder_id + "/edit", {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
        });
      } else {
        const body = new FormData(); body.set("payload", JSON.stringify(payload));
        if (file) body.set("file", file);
        saved = await call<EngineerReminder>("reminders", { method: "POST", body });
      }
      remindersChanged(); onSaved(saved);
    } catch (e) { setError((e as Error).message); setSaving(false); }
  }

  return <dialog ref={dialog} className="reminder-dialog" aria-labelledby="reminder-editor-title"
    onCancel={(event) => { event.preventDefault(); if (!saving) onClose(); }} onPaste={paste}>
    <form onSubmit={save}>
      <header className="reminder-editor-head">
        <div><p className="reminder-eyebrow">NYRA · FOLLOW UP</p>
          <h2 id="reminder-editor-title">{reminder ? "Edit reminder" : "Capture a reminder"}</h2>
          <p>Keep a request ready for the next review.</p></div>
        <button type="button" className="btn" aria-label="Close reminder" disabled={saving} onClick={onClose}>Close</button>
      </header>
      <div className="reminder-editor-body">
        {!reminder && <div className="reminder-drop"
          onDragOver={(e) => { e.preventDefault(); }}
          onDrop={(e) => { e.preventDefault(); if (!reading && !saving && e.dataTransfer.files[0]) void readImage(e.dataTransfer.files[0]); }}>
          <input ref={picker} type="file" accept="image/png,image/jpeg,image/webp" className="sr-only"
            aria-label="Reminder screenshot" disabled={reading || saving}
            onChange={(e) => { const image = e.target.files?.[0]; e.target.value = ""; if (image) void readImage(image); }} />
          <button type="button" className="btn" disabled={reading || saving} onClick={() => picker.current?.click()}>
            {file ? "Replace screenshot" : "Choose screenshot"}</button>
          {file && <button type="button" className="btn" disabled={saving} onClick={() => {
            request.current?.abort(); setFile(null); setPreview(""); setReading(false);
            setChecked(false); setWarning(null); setUncertainties([]); setError(null);
          }}>Remove screenshot</button>}
          <p>Or paste / drop an image here. PNG, JPEG, WebP · up to 5 MB.</p>
        </div>}
        {preview && <a href={preview} target="_blank" rel="noreferrer" title="Open full screenshot">
          <img className="reminder-preview" src={preview} alt="Screenshot attached to this reminder" /></a>}
        {reminder?.has_image && <ReminderScreenshot reminderId={reminder.reminder_id} />}
        {reading && <p role="status" className="reminder-notice">Nyra is reading the screenshot…</p>}
        {warning && <p role="status" className="reminder-notice">{warning}</p>}
        {uncertainties.length > 0 && <div className="reminder-notice"><strong>Check before saving</strong>
          <ul>{uncertainties.map((text, i) => <li key={i}>{text}</li>)}</ul></div>}
        {error && <p role="alert" className="reminder-error">{error}</p>}
        <fieldset disabled={reading || saving}>
          <label>Reminder title<input className="field" required maxLength={160} value={title}
            onChange={(e) => setTitle(e.target.value)} placeholder="Review request to increase ROP" /></label>
          <div className="reminder-fields">
            <label>Part number<input className="field" maxLength={100} value={item}
              onChange={(e) => setItem(e.target.value)} placeholder="Confirm from screenshot" /></label>
            <label>Stockroom<input className="field" maxLength={100} value={room}
              onChange={(e) => setRoom(e.target.value)} placeholder="Exact stockroom ID" /></label>
          </div>
          <label>Request and notes<textarea aria-label="Request and notes" className="field" rows={5} maxLength={10000}
            value={note} onChange={(e) => setNote(e.target.value)} placeholder="What should be checked at the next review?" /></label>
          <div className="reminder-fields">
            <label>Remind me<select className="field" aria-label="Remind me" value={timing} onChange={(e) => setTiming(e.target.value as typeof timing)}>
              <option value="next_cycle">At the next matching review cycle</option><option value="date">On a date</option>
            </select></label>
            {timing === "date" ? <label>Reminder date<input className="field" type="date" required value={dueDate}
              onChange={(e) => setDueDate(e.target.value)} /></label> : <div className="reminder-field-note">
              A future BOM upload with this exact part and stockroom will bring this reminder forward.
            </div>}
          </div>
          {timing === "next_cycle" && (!item.trim() || !room.trim()) && <p className="reminder-field-note">
            You can save now. Add both the part number and stockroom to enable automatic cycle matching.</p>}
          <p className="reminder-field-note">Owner: {user}. Reminders stay on your list until completed or dismissed.</p>
          {file && <label className="reminder-check"><input type="checkbox" checked={checked}
            onChange={(e) => setChecked(e.target.checked)} />I checked the screenshot and corrected the extracted details.</label>}
        </fieldset>
      </div>
      <footer className="reminder-editor-footer">
        <p>Saving a reminder does not approve stocking changes.</p>
        <button className="btn btn-primary" disabled={reading || saving || (!!file && !checked)}>
          {saving ? "Saving…" : reminder ? "Save changes" : "Save reminder"}</button>
      </footer>
    </form>
  </dialog>;
}
