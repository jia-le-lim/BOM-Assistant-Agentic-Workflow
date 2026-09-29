"use client";
/* eslint-disable @next/next/no-img-element -- A clipboard image stays in a local object URL until explicitly sent. */

import { useEffect, useState } from "react";
import "./composer-image.css";

export function ComposerImage({ file, onRemove, disabled = false }: {
  file: File; onRemove: () => void; disabled?: boolean;
}) {
  const [preview, setPreview] = useState<{ file: File; url: string } | null>(null);
  useEffect(() => {
    const url = URL.createObjectURL(file);
    const frame = requestAnimationFrame(() => setPreview({ file, url }));
    return () => { cancelAnimationFrame(frame); URL.revokeObjectURL(url); };
  }, [file]);
  const url = preview?.file === file ? preview.url : undefined;
  return <div className="composer-image" role="group" aria-label="Attached image" data-assistant-private>
    <div className="composer-image-thumb">{url && <img src={url} alt="Attached image preview" />}</div>
    <div className="composer-image-copy"><strong title={file.name}>{file.name || "Pasted image"}</strong>
      <span>Image attached. Send to review the reminder.</span></div>
    <button type="button" className="composer-image-remove" aria-label="Remove attached image"
      title="Remove image" disabled={disabled} onClick={onRemove}>
      <svg width="14" height="14" viewBox="0 0 20 20" stroke="currentColor" strokeWidth="1.8" aria-hidden><path d="m5 5 10 10M15 5 5 15" /></svg>
    </button>
  </div>;
}
