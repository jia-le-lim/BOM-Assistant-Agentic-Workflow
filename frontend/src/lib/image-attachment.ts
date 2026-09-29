"use client";

import { useState, type ClipboardEvent } from "react";

export const IMAGE_ACCEPT = "image/png,image/jpeg,image/webp";
const IMAGE_TYPES = new Set(IMAGE_ACCEPT.split(","));

/** Clipboard items are used by Windows screenshots; files is a browser fallback. */
export function clipboardImage(data: DataTransfer): File | null {
  for (const item of Array.from(data.items ?? [])) {
    if (item.kind === "file" && item.type.startsWith("image/")) {
      const file = item.getAsFile();
      if (file) return file;
    }
  }
  return Array.from(data.files ?? []).find((file) => file.type.startsWith("image/")) ?? null;
}

export function useImageAttachment(scope: string) {
  const [draft, setDraft] = useState<{ scope: string; file: File | null; error: string | null } | null>(null);
  const current = draft?.scope === scope ? draft : null;
  function attach(file: File) {
    const error = !IMAGE_TYPES.has(file.type) ? "Use a PNG, JPEG, or WebP image."
      : file.size > 5 * 1024 * 1024 ? "Choose an image up to 5 MB." : null;
    // An invalid replacement must not discard a valid image already attached.
    setDraft({ scope, file: error ? current?.file ?? null : file, error });
  }
  function clear() { setDraft(null); }
  function paste(event: ClipboardEvent, disabled = false) {
    const file = clipboardImage(event.clipboardData);
    if (!file) return false; // Let the browser paste normal text as usual.
    event.preventDefault();
    if (!disabled) attach(file);
    return true;
  }
  return { file: current?.file ?? null, error: current?.error ?? null, attach, clear, paste };
}

export type ImageAttachmentControl = ReturnType<typeof useImageAttachment>;
