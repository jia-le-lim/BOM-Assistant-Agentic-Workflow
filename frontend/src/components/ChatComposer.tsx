"use client";

import { useEffect, useRef, useState, type RefObject } from "react";
import type { Batch, ChatSkill } from "@/lib/types";
import type { ImageAttachmentControl } from "@/lib/image-attachment";
import { ComposerImage } from "./ComposerImage";

interface Mention { kind: "workspace" | "skill"; start: number; end: number; query: string }

/** A mention starts at a word boundary, so email addresses stay ordinary text. */
function mentionAt(value: string, caret: number): Mention | null {
  const match = /(?:^|\s)@([^@\n]*)$/.exec(value.slice(0, caret));
  if (match) return { kind: "workspace", start: caret - match[1].length - 1, end: caret, query: match[1] };
  const slash = /^\s*\/([a-z0-9-]*)$/i.exec(value.slice(0, caret));
  return slash ? { kind: "skill", start: caret - slash[1].length - 1, end: caret, query: slash[1] } : null;
}

function workspaceStatus(workspace: Batch) {
  if (workspace.status === "draft") return "Awaiting datasheet";
  if (workspace.status === "uploading") return "Uploading";
  if (workspace.status === "scored") return "Ready for review";
  return "Not scored yet";
}

export function ChatComposer({ value, onChange, onSend, inputRef, busy, sending, loadingHistory,
  canUpload, onUpload, onCapture, imageAttachment, workspaceId, onWorkspaceChange, workspaces, workspacesLoading,
  workspacesError, onRetryWorkspaces, skills, skillsLoading, skillsError, onRetrySkills }: {
  value: string;
  onChange: (value: string) => void;
  onSend: (question: string) => void;
  inputRef: RefObject<HTMLTextAreaElement | null>;
  busy: boolean;
  sending: boolean;
  loadingHistory: boolean;
  canUpload: boolean;
  onUpload: (file: File) => void;
  onCapture?: (file?: File, note?: string) => void;
  imageAttachment?: ImageAttachmentControl;
  workspaceId: number | null;
  onWorkspaceChange: (id: number | null) => void;
  workspaces: Batch[];
  workspacesLoading: boolean;
  workspacesError: string | null;
  onRetryWorkspaces: () => void;
  skills: ChatSkill[];
  skillsLoading: boolean;
  skillsError: string | null;
  onRetrySkills: () => void;
}) {
  const [mention, setMention] = useState<Mention | null>(null);
  const [highlighted, setHighlighted] = useState(0);
  const filePicker = useRef<HTMLInputElement>(null);
  const form = useRef<HTMLFormElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const disabled = busy || loadingHistory;
  const open = mention !== null && !disabled;
  const isSkill = mention?.kind === "skill";
  const query = mention?.query.trim().toLocaleLowerCase() ?? "";
  const matches = workspaces.filter((workspace) =>
    `${workspace.label} ${workspace.module_filter ?? ""} ${workspace.batch_id}`.toLocaleLowerCase().includes(query));
  const choices = workspacesLoading || workspacesError ? [] : matches;
  const skillChoices = skillsLoading || skillsError ? [] : skills.filter((skill) =>
    `${skill.name} ${skill.title} ${skill.description}`.toLocaleLowerCase().includes(query))
    .sort((a, b) => {
      const rank = (skill: ChatSkill) => skill.name === query ? 0 : skill.name.startsWith(query) ? 1 : 2;
      return rank(a) - rank(b);
    });
  const optionCount = isSkill ? skillChoices.length : choices.length;
  const activeIndex = Math.min(highlighted, Math.max(0, optionCount - 1));
  const activeOption = isSkill ? skillChoices[activeIndex] && `skill-option-${skillChoices[activeIndex].name}`
    : choices[activeIndex] && `workspace-option-${choices[activeIndex].batch_id}`;
  const listId = isSkill ? "skill-options" : "workspace-mention-options";
  const selectedSkill = skills.find((skill) => value.trimStart().split(/\s/, 1)[0].toLowerCase() === `/${skill.name}`);
  const selected = workspaces.find((workspace) => workspace.batch_id === workspaceId);
  const selectedLabel = selected?.label || `Workspace #${workspaceId}`;

  useEffect(() => {
    if (!open) return;
    const dismiss = (event: PointerEvent) => {
      if (!form.current?.contains(event.target as Node)) setMention(null);
    };
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const container = list.current;
    const option = container?.querySelector<HTMLElement>(`[data-option-index="${activeIndex}"]`);
    if (!option || !container) return;
    const reveal = () => {
      const bounds = container.getBoundingClientRect(), row = option.getBoundingClientRect();
      if (row.top < bounds.top) container.scrollTop -= bounds.top - row.top;
      else if (row.bottom > bounds.bottom) container.scrollTop += row.bottom - bounds.bottom;
    };
    reveal();
    const observer = new ResizeObserver(reveal);
    observer.observe(container);
    observer.observe(option);
    return () => observer.disconnect();
  }, [activeIndex, open, query, listId, activeOption]);

  function updateMention(textarea: HTMLTextAreaElement) {
    const next = disabled || textarea.selectionStart !== textarea.selectionEnd
      ? null : mentionAt(textarea.value, textarea.selectionStart);
    // React also reports selection after an arrow key. Preserve the highlighted
    // option when the caret and search text did not actually move.
    if (next?.kind !== mention?.kind || next?.start !== mention?.start || next?.end !== mention?.end || next?.query !== mention?.query) {
      setMention(next);
      setHighlighted(0);
    }
  }

  function selectWorkspace(workspace: Batch) {
    if (!mention || disabled) return;
    // The selected chip is the mention. Keep the question separate and send
    // the real workspace ID through the API's existing batch context.
    const next = value.slice(0, mention.start) + value.slice(mention.end);
    onChange(next);
    onWorkspaceChange(workspace.batch_id);
    setMention(null);
    window.requestAnimationFrame(() => {
      inputRef.current?.focus();
      inputRef.current?.setSelectionRange(mention.start, mention.start);
    });
  }

  function startMention() {
    if (open && !isSkill) { setMention(null); inputRef.current?.focus(); return; }
    const input = inputRef.current;
    const start = input?.selectionStart ?? value.length;
    const end = input?.selectionEnd ?? start;
    const prefix = start > 0 && !/\s/.test(value[start - 1]) ? " @" : "@";
    const next = value.slice(0, start) + prefix + value.slice(end);
    onChange(next);
    setMention({ kind: "workspace", start: start + prefix.length - 1, end: start + prefix.length, query: "" });
    setHighlighted(0);
    window.requestAnimationFrame(() => {
      input?.focus();
      input?.setSelectionRange(start + prefix.length, start + prefix.length);
    });
  }

  function selectSkill(skill: ChatSkill) {
    if (!mention || disabled || !skill.available) return;
    const command = `/${skill.name} `;
    const prefix = value.slice(0, mention.start);
    onChange(prefix + command + value.slice(mention.end).replace(/^ /, ""));
    setMention(null);
    window.requestAnimationFrame(() => {
      inputRef.current?.focus();
      inputRef.current?.setSelectionRange(prefix.length + command.length, prefix.length + command.length);
    });
  }

  function startSkill() {
    if (open && isSkill) { setMention(null); inputRef.current?.focus(); return; }
    const rest = value.replace(/^\s*\/[a-z0-9-]*(?:[ \t]+|$)/i, "");
    onChange("/ " + rest);
    setMention({ kind: "skill", start: 0, end: 1, query: "" });
    setHighlighted(0);
    window.requestAnimationFrame(() => {
      inputRef.current?.focus(); inputRef.current?.setSelectionRange(1, 1);
    });
  }

  function submit() {
    if (disabled || open) return;
    setMention(null);
    if (imageAttachment?.file && onCapture) onCapture(imageAttachment.file, value);
    else if (value.trim()) onSend(value);
  }

  function selectActive() {
    if (isSkill && skillChoices[activeIndex]) selectSkill(skillChoices[activeIndex]);
    else if (!isSkill && choices[activeIndex]) selectWorkspace(choices[activeIndex]);
  }

  return (
    <form ref={form} className="composer composer-sticky" onSubmit={(event) => {
      event.preventDefault();
      submit();
    }} onPaste={(event) => {
      if (imageAttachment?.paste(event, disabled)) {
        setMention(null); inputRef.current?.focus();
      }
    }} onBlur={(event) => {
      if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setMention(null);
    }}>
      {open && !isSkill && (
        <div className="workspace-mention-menu" data-assistant-private>
          <div className="workspace-mention-heading"><strong>Focus on a workspace</strong><span>Esc to close</span></div>
          <div ref={list} id="workspace-mention-options" className="workspace-mention-options"
               role="listbox" aria-label="Workspaces" aria-busy={workspacesLoading}>
            {choices.map((workspace, index) => (
              <div key={workspace.batch_id} id={`workspace-option-${workspace.batch_id}`}
                   role="option" aria-selected={index === activeIndex} data-option-index={index}
                   className={`workspace-mention-option${index === activeIndex ? " is-highlighted" : ""}`}
                   onPointerDown={(event) => event.preventDefault()}
                   onClick={() => selectWorkspace(workspace)}
                   onPointerMove={() => setHighlighted(index)}>
                <span className="workspace-mention-symbol" aria-hidden>@</span>
                <span className="workspace-mention-copy"><strong>{workspace.label || `Workspace #${workspace.batch_id}`}</strong>
                  <span>{workspace.module_filter || "All modules"} · #{workspace.batch_id} · {workspaceStatus(workspace)}</span></span>
                {workspace.batch_id === workspaceId && <span className="workspace-mention-selected">Selected</span>}
              </div>
            ))}
          </div>
          {workspacesLoading ? <p className="workspace-mention-message" role="status">Loading workspaces…</p>
            : workspacesError ? <div className="workspace-mention-message" role="alert">Could not load workspaces.
              <button type="button" onPointerDown={(event) => event.preventDefault()}
                      onClick={() => { onRetryWorkspaces(); inputRef.current?.focus(); }}>Try again</button></div>
            : choices.length === 0 ? <p className="workspace-mention-message" role="status">
              {workspaces.length ? "No matching workspaces. Try another name or module." : "No workspaces yet. Create one from Workspaces to get started."}</p>
            : <p className="workspace-mention-help">↑ ↓ to navigate · Enter to select · Focus stays for follow-up questions</p>}
        </div>
      )}

      {open && isSkill && (
        <div className="workspace-mention-menu skill-picker-menu" data-assistant-private>
          <div className="workspace-mention-heading"><strong>Choose a skill</strong><span>Esc to close</span></div>
          <div ref={list} id="skill-options" className="workspace-mention-options" role="listbox" aria-label="Skills" aria-busy={skillsLoading}>
            {skillChoices.map((skill, index) => (
              <div key={skill.name} id={`skill-option-${skill.name}`} role="option"
                   aria-selected={index === activeIndex} aria-disabled={!skill.available} data-option-index={index}
                   className={`workspace-mention-option${index === activeIndex ? " is-highlighted" : ""}`}
                   onPointerDown={(event) => event.preventDefault()} onClick={() => selectSkill(skill)}
                   onPointerMove={() => setHighlighted(index)}>
                <span className="workspace-mention-symbol" aria-hidden>/</span>
                <span className="workspace-mention-copy"><strong>/{skill.name}<span className="skill-picker-title">{skill.title}</span></strong>
                  <span>{skill.available ? skill.description : skill.unavailable_reason}</span></span>
              </div>
            ))}
          </div>
          {skillsLoading ? <p className="workspace-mention-message" role="status">Loading skills…</p>
            : skillsError ? <div className="workspace-mention-message" role="alert">Could not load skills.
              <button type="button" onPointerDown={(event) => event.preventDefault()}
                      onClick={() => { onRetrySkills(); inputRef.current?.focus(); }}>Try again</button></div>
            : skillChoices.length === 0 ? <p className="workspace-mention-message" role="status">No matching skills. Try / to see all skills.</p>
            : <p className="workspace-mention-help">↑ ↓ to navigate · Enter to select · Uses your workspace focus</p>}
        </div>
      )}

      {workspaceId !== null && (
        <div className="composer-workspace" aria-label="Workspace focus">
          <span className="composer-workspace-label">Focusing on</span>
          <span className="composer-workspace-chip">
            <span aria-hidden>@</span><span title={`${selectedLabel} · #${workspaceId}`}>{selectedLabel}</span>
            <button type="button" disabled={disabled} aria-label="Clear workspace focus"
                    title="Use the latest scored workspace" onClick={() => {
                      onWorkspaceChange(null); inputRef.current?.focus();
                    }}>×</button>
          </span>
        </div>
      )}
      {imageAttachment?.file && <ComposerImage file={imageAttachment.file} disabled={disabled}
        onRemove={() => { imageAttachment.clear(); inputRef.current?.focus(); }} />}
      {imageAttachment?.error && <p className="composer-image-error" role="alert">{imageAttachment.error}</p>}
      <label htmlFor="ask" className="sr-only">Ask about a recommendation</label>
      <textarea id="ask" ref={inputRef} rows={1} className="composer-input" value={value}
        role="combobox" aria-autocomplete="list" aria-haspopup="listbox" aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-activedescendant={open ? activeOption : undefined}
        aria-describedby={`composer-help workspace-focus-help${selectedSkill ? " composer-skill-help" : ""}`}
        placeholder="Ask anything · @ workspace · / skills"
        onChange={(event) => { onChange(event.target.value); updateMention(event.target); }}
        onSelect={(event) => {
          if (document.activeElement === event.currentTarget) updateMention(event.currentTarget);
        }}
        onKeyDown={(event) => {
          if (event.nativeEvent.isComposing) return;
          if (open) {
            if (event.key === "ArrowDown" || event.key === "ArrowUp") {
              event.preventDefault();
              const direction = event.key === "ArrowDown" ? 1 : -1;
              setHighlighted(optionCount ? (activeIndex + direction + optionCount) % optionCount : 0);
              return;
            }
            if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); setMention(null); return; }
            if (!event.shiftKey && (event.key === "Enter" || event.key === "Tab")) {
              if (optionCount) { event.preventDefault(); selectActive(); }
              else if (event.key === "Enter") event.preventDefault();
              return;
            }
          }
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            submit();
          }
        }} />
      {selectedSkill && <p id="composer-skill-help" className="composer-skill-help">
        <span>{selectedSkill.title}</span><code>{selectedSkill.available ? selectedSkill.usage : selectedSkill.unavailable_reason}</code>
      </p>}
      <div className="composer-footer">
        <div className="composer-attachments">
          {onCapture && <button type="button" className="btn composer-attach" disabled={disabled}
            aria-label="Capture image reminder" title="Capture a screenshot or reminder"
            onClick={() => onCapture(imageAttachment?.file ?? undefined, value)}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden>
              <rect x="3" y="5" width="18" height="16" rx="3" /><circle cx="8" cy="10" r="1.5" /><path d="m3 18 5-5 4 4 4-6 5 7" />
            </svg>
          </button>}
          {canUpload && <>
            <input ref={filePicker} type="file" accept=".csv,text/csv" className="hidden"
              onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ""; if (file) onUpload(file); }} />
            <button type="button" className="btn composer-attach" disabled={disabled} aria-label="Attach CSV"
                    title="Upload a BOM extract (.csv) as a new batch" onClick={() => filePicker.current?.click()}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden><path d="M12 5v14M5 12h14" /></svg>
            </button>
          </>}
          <button type="button" className="btn composer-mention" disabled={disabled} aria-label="Choose workspace"
                  aria-expanded={open && !isSkill} aria-controls={open && !isSkill ? "workspace-mention-options" : undefined}
                  title="Choose a workspace (@)" onClick={startMention}>@</button>
          <button type="button" className="btn composer-mention" disabled={disabled} aria-label="Choose skill"
                  aria-expanded={open && isSkill} aria-controls={open && isSkill ? "skill-options" : undefined}
                  title="Choose a skill (/)" onClick={startSkill}>/</button>
          <span id="composer-help" className="composer-hint">
            {loadingHistory ? "Loading conversation…" : sending ? "NYRA is responding…" : "Enter to send"}
          </span>
        </div>
        <button className="btn btn-primary composer-send" disabled={disabled || open || (!value.trim() && !imageAttachment?.file)}
                aria-label="Send" title="Send message">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="M12 19V5m-6 6 6-6 6 6" /></svg>
        </button>
      </div>
      {imageAttachment && <p className="composer-image-hint">Ctrl+V to attach an image for a reminder.</p>}
      <p id="workspace-focus-help" className="composer-focus-help">
        {workspaceId === null ? "No focus selected · Uses the latest scored workspace"
          : selected?.status === "draft" ? "This workspace is awaiting a datasheet."
          : selected && selected.status !== "scored" ? "Recommendations will be available after this workspace is scored."
          : "Follow-up questions will use this workspace."}
      </p>
    </form>
  );
}
