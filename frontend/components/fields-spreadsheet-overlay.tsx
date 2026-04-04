"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { FieldValue } from "../lib/types";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") || "http://127.0.0.1:8000";

type Props = {
  open: boolean;
  onClose: () => void;
  fields: FieldValue[];
  jobId: string | null;
  onAfterSave: () => Promise<void>;
  onError: (message: string | null) => void;
};

function IconSpinner({ className = "" }: { className?: string }) {
  return <div className={`spinner ${className}`} style={{ width: "1rem", height: "1rem" }} />;
}

export function FieldsSpreadsheetOverlay({
  open,
  onClose,
  fields,
  jobId,
  onAfterSave,
  onError,
}: Props) {
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [savingId, setSavingId] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    const next: Record<string, string> = {};
    for (const f of fields) {
      next[f.field_id] = f.value ?? "";
    }
    setDrafts(next);
  }, [open, fields]);

  const saveField = useCallback(
    async (fieldId: string, value: string) => {
      if (!jobId) return;
      const field = fields.find((f) => f.field_id === fieldId);
      if (!field) return;
      const server = field.value ?? "";
      if (value === server) return;

      setSavingId(fieldId);
      onError(null);
      try {
        const response = await fetch(`${API_BASE_URL}/api/v1/jobs/${jobId}/fields/${fieldId}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json", "X-User-Id": "local-analyst-1" },
          body: JSON.stringify({ value }),
        });
        if (!response.ok) throw new Error(await response.text());
        await onAfterSave();
      } catch (e) {
        onError(e instanceof Error ? e.message : "Save failed.");
        setDrafts((d) => ({ ...d, [fieldId]: server }));
      } finally {
        setSavingId(null);
      }
    },
    [jobId, fields, onAfterSave, onError],
  );

  const rows = useMemo(() => fields, [fields]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="spreadsheet-overlay" role="dialog" aria-modal="true" aria-label="Field grid editor">
      <div className="spreadsheet-backdrop" onClick={onClose} aria-hidden />
      <div className="spreadsheet-panel">
        <header className="spreadsheet-toolbar">
          <div>
            <h2 className="spreadsheet-title">Export fields — grid view</h2>
            <p className="spreadsheet-subtitle">Edit values inline; changes save when you leave a cell.</p>
          </div>
          <button type="button" className="btn btn-secondary" onClick={onClose}>
            Close
          </button>
        </header>

        <div className="spreadsheet-scroll">
          <table className="spreadsheet-table">
            <thead>
              <tr>
                <th className="spreadsheet-col-idx">#</th>
                <th className="spreadsheet-col-label">Parameter</th>
                <th className="spreadsheet-col-cat">Category</th>
                <th className="spreadsheet-col-value">Value</th>
                <th className="spreadsheet-col-status">Status</th>
                <th className="spreadsheet-col-num">Conf.</th>
                <th className="spreadsheet-col-pages">Pages</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((field, index) => (
                <tr key={field.field_id}>
                  <td className="spreadsheet-cell-muted">{index + 1}</td>
                  <td className="spreadsheet-cell-label">{field.label}</td>
                  <td className="spreadsheet-cell-muted">{field.category}</td>
                  <td className="spreadsheet-cell-value">
                    <div className="spreadsheet-value-wrap">
                      <textarea
                        className="spreadsheet-cell-input"
                        rows={2}
                        value={drafts[field.field_id] ?? ""}
                        disabled={!jobId || savingId === field.field_id}
                        onChange={(e) =>
                          setDrafts((d) => ({ ...d, [field.field_id]: e.target.value }))
                        }
                        onBlur={(e) => void saveField(field.field_id, e.currentTarget.value)}
                      />
                      {savingId === field.field_id && (
                        <span className="spreadsheet-saving">
                          <IconSpinner />
                        </span>
                      )}
                    </div>
                  </td>
                  <td>
                    <span className={`badge badge-${field.is_absent ? "warning" : "success"}`}>
                      {field.is_absent ? "Flagged" : "Pass"}
                    </span>
                  </td>
                  <td className="spreadsheet-cell-muted">
                    {field.confidence == null ? "—" : `${(field.confidence * 100).toFixed(0)}%`}
                  </td>
                  <td className="spreadsheet-cell-muted">{field.page_range ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {rows.length === 0 && (
            <p className="spreadsheet-empty">No fields to display. Complete extraction first.</p>
          )}
        </div>
      </div>
    </div>
  );
}
