"use client";

import { useState } from "react";
import { useApi } from "@/lib/api";
import { Banner } from "./ui";

export interface DraftColumn<T> {
  key: keyof T;
  label: string;
  type?: "number";
  options?: readonly string[];
  disabled?: (row: T) => boolean;
}

/** The same proposal endpoints as manual entry, with per-row retry semantics. */
export function AssistantDraftTable<T extends object>({ rows, onChange, columns, endpoint,
  section, canPropose, onSaved, valid, replacesActive, onBusyChange }: {
  rows: T[];
  onChange: (rows: T[]) => void;
  columns: DraftColumn<T>[];
  endpoint: string;
  section: string;
  canPropose: boolean;
  onSaved: () => Promise<void>;
  valid: (row: T) => boolean;
  replacesActive?: (row: T) => boolean;
  onBusyChange: (busy: boolean) => void;
}) {
  const { call } = useApi();
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [note, setNote] = useState("");
  const [errors, setErrors] = useState<string[]>([]);
  if (!rows.length && !note) return null;

  async function submit() {
    if (busy || !canPropose || rows.some((row) => !valid(row))) return;
    setBusy(true); onBusyChange(true); setProgress(0); setNote(""); setErrors([]);
    const failed: T[] = [];
    const failures: string[] = [];
    for (let index = 0; index < rows.length; index++) {
      try {
        await call(endpoint, { method: "POST", headers: { "Content-Type": "application/json" },
                               body: JSON.stringify(rows[index]) });
      } catch (cause) {
        failed.push(rows[index]);
        failures.push(`Row ${index + 1}: ${(cause as Error).message}`);
      }
      setProgress(index + 1);
    }
    onChange(failed);
    setErrors(failures);
    setNote(`${rows.length - failed.length} of ${rows.length} proposals submitted. `
      + "Confirm your proposals with senior or administrator rights before your next engine run."
      + (failed.length ? " Unsubmitted rows remain below for correction or retry." : ""));
    try { await onSaved(); } finally { setBusy(false); onBusyChange(false); }
  }

  return (
    <div className="assistant-drafts" data-assistant-target={section} aria-label={`${section.replaceAll("_", " ")} draft`}>
      <div className="flex items-center gap-3 flex-wrap">
        <strong className="text-sm">NYRA draft · {rows.length} {rows.length === 1 ? "row" : "rows"}</strong>
        {!!rows.length && <button type="button" className="btn text-xs" disabled={busy}
          onClick={() => { onChange([]); setErrors([]); setNote(""); }}>Clear draft</button>}
      </div>
      <p className="text-xs mt-2 mb-3">Check or edit each row, then propose the list.</p>
      {note && <Banner kind={errors.length ? "info" : "success"}>{note}</Banner>}
      {errors.length > 0 && <Banner kind="error">{errors.join(" ")}</Banner>}
      {!!rows.length && <>
        {replacesActive && rows.some(replacesActive) && <Banner kind="info">
          This draft includes active rules. Proposing replacements resets their approval until you confirm them with approval rights.
        </Banner>}
        <div className="scroll-x">
          <table className="w-full text-sm">
            <thead><tr>{columns.map((column) => <th key={String(column.key)}>{column.label}</th>)}<th /></tr></thead>
            <tbody>{rows.map((row, index) => <tr key={index}>
              {columns.map((column) => {
                const props = {
                  className: "field w-full", "aria-label": `${column.label} ${index + 1}`,
                  disabled: busy || !canPropose || column.disabled?.(row),
                  value: String(row[column.key] ?? ""),
                  onChange: (event: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
                    const value = column.type === "number"
                      ? event.target.value === "" ? null : Number(event.target.value) : event.target.value;
                    onChange(rows.map((entry, i) => i === index ? { ...entry, [column.key]: value } : entry));
                  },
                };
                return <td key={String(column.key)}>{column.options
                  ? <select {...props}>{column.options.map((option) => <option key={option}>{option}</option>)}</select>
                  : <input {...props} type={column.type || "text"} />}</td>;
              })}
              <td><button type="button" className="btn text-xs" aria-label={`Remove draft row ${index + 1}`}
                disabled={busy} onClick={() => onChange(rows.filter((_, i) => i !== index))}>Remove</button></td>
            </tr>)}</tbody>
          </table>
        </div>
        <button type="button" className="btn btn-primary mt-3" onClick={() => void submit()}
          disabled={busy || !canPropose || rows.some((row) => !valid(row))}>
          {busy ? `Proposing ${progress}/${rows.length}…` : `Propose ${rows.length} ${rows.length === 1 ? "rule" : "rules"}`}
        </button>
      </>}
    </div>
  );
}
