"use client";

import { useCallback, useEffect, useMemo, useState, type CSSProperties } from "react";
import type { FieldValue, Job, JobResult } from "../lib/types";
import { FieldsSpreadsheetOverlay } from "./fields-spreadsheet-overlay";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") || "http://127.0.0.1:8000";

const ACTIVE_STATUSES = new Set(["queued", "parsing", "indexing", "extracting"]);

function formatPercent(progress: number) {
  return `${Math.round((progress || 0) * 100)}%`;
}

function getPipelineEvents(progress: number, status: string | undefined) {
  const steps = [
    { title: "Parsing Engine", threshold: 0.15 },
    { title: "Hybrid Retrieval", threshold: 0.40 },
    { title: "Reranking & Extraction", threshold: 0.70 },
    { title: "Audit Verification", threshold: 0.95 },
  ];

  return steps.map(step => {
    let state = "pending";
    if (status === "failed") state = "pending";
    else if (status === "completed" || progress >= step.threshold) state = "done";
    else if (progress > 0 && progress < step.threshold && progress >= (steps[steps.indexOf(step)-1]?.threshold ?? 0)) state = "active";
    
    // Auto-activate the first step immediately
    if (step.threshold === 0.15 && progress > 0 && progress < 0.15) state = "active";
    
    return { ...step, state };
  });
}

function IconSpinner({ className = "", style }: { className?: string; style?: CSSProperties }) {
  return <div className={`spinner ${className}`} style={style} />;
}

function IconCheck() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}

export function ReviewWorkbench() {
  const [file, setFile] = useState<File | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [result, setResult] = useState<JobResult | null>(null);
  const [baseDocPreviewHtml, setBaseDocPreviewHtml] = useState<string | null>(null);
  const [docPreviewHtml, setDocPreviewHtml] = useState<string | null>(null);
  const [docPreviewText, setDocPreviewText] = useState<string | null>(null);
  const [selectedFieldId, setSelectedFieldId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [requiresActionOnly, setRequiresActionOnly] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [savingFieldId, setSavingFieldId] = useState<string | null>(null);
  const [spreadsheetOpen, setSpreadsheetOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fields = result?.fields ?? [];
  const selectedField = useMemo(() => {
    const fallback = fields[0] ?? null;
    return fields.find((field) => field.field_id === selectedFieldId) ?? fallback;
  }, [fields, selectedFieldId]);
  const documentMatches = useMemo(
    () => buildDocumentMatches(docPreviewText, selectedField),
    [docPreviewText, selectedField],
  );

  const filteredFields = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    return fields.filter((field) => {
      if (requiresActionOnly) {
        const needsAction = field.is_absent || !field.value?.trim();
        if (!needsAction) return false;
      }
      if (!query) return true;
      const flagText = field.is_absent ? "flagged requires action review" : "pass";
      return [field.label, field.value ?? "", field.source_clause ?? "", field.source_section ?? "", flagText]
        .join(" ")
        .toLowerCase()
        .includes(query);
    });
  }, [fields, requiresActionOnly, searchQuery]);

  const stats = useMemo(() => {
    const total = fields.length;
    const found = fields.filter((field) => !field.is_absent && field.value).length;
    const missing = fields.filter((field) => field.is_absent).length;
    const avgConfidence = fields
      .map((field) => field.confidence)
      .filter((value): value is number => value != null)
      .reduce((sum, value, _, arr) => sum + value / arr.length, 0) || 0;

    return { total, found, missing, avgConfidence };
  }, [fields]);

  useEffect(() => {
    if (!job || !ACTIVE_STATUSES.has(job.status)) {
      return;
    }

    const timer = window.setInterval(async () => {
      try {
        const nextJob = await fetchJson<Job>(`${API_BASE_URL}/api/v1/jobs/${job.id}`);
        setJob(nextJob);

        try {
           const nextResult = await fetchJson<JobResult>(`${API_BASE_URL}/api/v1/jobs/${job.id}/result`);
           setResult(nextResult);
           setSelectedFieldId((prev) => {
             if (!prev && nextResult.fields.length > 0) return nextResult.fields[0].field_id;
             return prev;
           });
        } catch (e) {
           // Ignore result missing if it truly isn't ready
        }

        if (!ACTIVE_STATUSES.has(nextJob.status)) {
          if (nextJob.error_message) setError(nextJob.error_message);
        }
      } catch (pollError) {
        setError(pollError instanceof Error ? pollError.message : "Polling failed.");
      }
    }, 1500);

    return () => window.clearInterval(timer);
  }, [job]);

  useEffect(() => {
    async function loadPreview() {
      if (!job || !result) {
        setBaseDocPreviewHtml(null);
        setDocPreviewHtml(null);
        setDocPreviewText(null);
        return;
      }

      if (!job.original_filename.toLowerCase().endsWith(".pdf")) {
        try {
          const response = await fetch(`${API_BASE_URL}${result.preview_url}`, { cache: "no-store" });
          if (!response.ok) throw new Error(await response.text());
          setBaseDocPreviewHtml(await response.text());
        } catch {
          setBaseDocPreviewHtml(null);
        }
      } else {
        setBaseDocPreviewHtml(null);
      }

      try {
        const response = await fetch(`${API_BASE_URL}/api/v1/jobs/${job.id}/preview?format=text`, { cache: "no-store" });
        if (!response.ok) throw new Error(await response.text());
        setDocPreviewText(await response.text());
      } catch {
        setDocPreviewText(null);
      }
    }
    void loadPreview();
  }, [job, result]);

  useEffect(() => {
    if (!baseDocPreviewHtml) {
      setDocPreviewHtml(null);
      return;
    }
    setDocPreviewHtml(buildHighlightedPreviewHtml(baseDocPreviewHtml, selectedField));
  }, [baseDocPreviewHtml, selectedField]);

  async function handleUpload(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) {
      setError("Choose a target file.");
      return;
    }

    setError(null);
    setIsUploading(true);
    setResult(null);
    setBaseDocPreviewHtml(null);
    setDocPreviewHtml(null);
    setDocPreviewText(null);
    setSelectedFieldId(null);

    try {
      const body = new FormData();
      body.append("file", file);
      const response = await fetch(`${API_BASE_URL}/api/v1/jobs`, { method: "POST", body });
      if (!response.ok) throw new Error(await response.text());
      const createdJob = (await response.json()) as Job;
      setJob(createdJob);
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : "Upload failed.");
    } finally {
      setIsUploading(false);
    }
  }

  const handleRefresh = useCallback(async () => {
    if (!job) return;
    const nextJob = await fetchJson<Job>(`${API_BASE_URL}/api/v1/jobs/${job.id}`);
    setJob(nextJob);
    if (nextJob.error_message) setError(nextJob.error_message);
    try {
      const nextResult = await fetchJson<JobResult>(`${API_BASE_URL}/api/v1/jobs/${job.id}/result`);
      setResult(nextResult);
    } catch {
      /* result may be missing mid-run */
    }
  }, [job]);

  const saveFieldValue = useCallback(
    async (fieldId: string, value: string) => {
      if (!job) return;
      const field = fields.find((f) => f.field_id === fieldId);
      if (!field) return;
      const server = field.value ?? "";
      if (value === server) return;

      setSavingFieldId(fieldId);
      setError(null);
      try {
        const response = await fetch(`${API_BASE_URL}/api/v1/jobs/${job.id}/fields/${fieldId}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json", "X-User-Id": "local-analyst-1" },
          body: JSON.stringify({ value }),
        });
        if (!response.ok) throw new Error(await response.text());
        await handleRefresh();
      } catch (saveError) {
        setError(saveError instanceof Error ? saveError.message : "Save failed.");
      } finally {
        setSavingFieldId(null);
      }
    },
    [job, fields, handleRefresh],
  );

  const documentUrl = result && job ? `${API_BASE_URL}${result.document_url}${buildPdfAnchor(selectedField)}` : null;
  const isProcessing = Boolean(job && ACTIVE_STATUSES.has(job.status));
  const pipelineEvents = getPipelineEvents(job?.progress ?? 0, job?.status);

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="brand-section">
          <div className="brand-logo">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" />
              <polyline points="3.27 6.96 12 12.01 20.73 6.96" />
              <line x1="12" y1="22.08" x2="12" y2="12" />
            </svg>
            LexExtract
          </div>
          <span className="badge badge-outline">Enterprise Core</span>
        </div>

        <form className="flex items-center gap-4" onSubmit={handleUpload}>
          <label className="file-upload-wrapper">
            <span className="file-upload-trigger">
              {file ? file.name : "Select Document"}
            </span>
            <input type="file" className="file-upload-input" accept=".pdf,.docx" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          </label>
          <button className="btn btn-primary" type="submit" disabled={isUploading || isProcessing || !file}>
            {isUploading ? "Uploading..." : "Process"}
          </button>
          {job && (
            <button className="btn btn-secondary flex items-center gap-2" onClick={handleRefresh} type="button" disabled={isProcessing}>
               <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8" /><path d="M21 3v5h-5" /></svg>
               Sync
            </button>
          )}
        </form>
      </header>

      <main className="main-content">
        {error && (
          <div className="ui-card" style={{ background: "var(--destructive)", color: "var(--destructive-foreground)", padding: "1rem" }}>
            <span className="font-semibold text-sm">{error}</span>
          </div>
        )}

        {/* Global Tracker */}
        {(job || file) && (
          <div className="ui-card">
            <div className="ui-card-header row">
              <div>
                <h3 className="ui-card-title">Job Status</h3>
                <p className="ui-card-description">{job?.original_filename || file?.name || "Ready"}</p>
              </div>
              <div className="flex items-center gap-4">
                {job && <span className={`badge badge-${toneForJobStatus(job.status)}`}>{job.status}</span>}
                {isProcessing && <span className="text-sm font-medium">{formatPercent(job?.progress ?? 0)}</span>}
              </div>
            </div>

            {isProcessing && (
              <div className="ui-card-content" style={{ padding: "1.5rem" }}>
                <div className="stepper-container">
                  {pipelineEvents.map((evt, idx) => (
                    <div key={idx} style={{ flex: idx !== pipelineEvents.length - 1 ? 1 : 0, display: "flex", alignItems: "center" }}>
                      <div className={`stepper-step ${evt.state}`}>
                        <div className="stepper-icon">
                          {evt.state === "done" ? <IconCheck /> : evt.state === "active" ? <IconSpinner style={{ width: "1rem", height: "1rem", borderTopColor: "transparent" }} /> : idx + 1}
                        </div>
                        <span className="stepper-label">{evt.title}</span>
                      </div>
                      {idx !== pipelineEvents.length - 1 && <div className={`stepper-connector ${evt.state === "done" ? "active" : ""}`} />}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        <div className="metrics-grid">
           <div className="ui-card metric-item">
             <span className="metric-title">Identified Parameters</span>
             <span className="metric-value">{stats.found}</span>
           </div>
           <div className="ui-card metric-item">
             <span className="metric-title">Analyst Flags</span>
             <span className="metric-value" style={{ color: stats.missing > 0 ? "var(--warning)" : "var(--foreground)" }}>{stats.missing}</span>
           </div>
           <div className="ui-card metric-item">
             <span className="metric-title">Global Certainty</span>
             <span className="metric-value">{stats.avgConfidence ? `${(stats.avgConfidence * 100).toFixed(1)}%` : "-"}</span>
           </div>
        </div>

        <div className="workspace">
          
          <div className="flex flex-col gap-6">
            <div className="ui-card">
              <div className="ui-card-header row">
                <div>
                  <h3 className="ui-card-title">Document Reference</h3>
                  <p className="ui-card-description">Immutable source visualization.</p>
                </div>
                <div className="flex gap-2">
                  {result && (
                    <>
                      <a className="btn btn-outline text-xs" style={{ height: "2rem" }} href={`${API_BASE_URL}${result.document_url}`} target="_blank" rel="noreferrer">Asset</a>
                      <a className="btn btn-outline text-xs" style={{ height: "2rem" }} href={`${API_BASE_URL}${result.preview_url}`} target="_blank" rel="noreferrer">HTML</a>
                    </>
                  )}
                </div>
              </div>
              <div className="ui-card-content p-0" style={{ padding: 0 }}>
                 <div className="document-viewer">
                    {job && job.original_filename.toLowerCase().endsWith(".pdf") && documentUrl ? (
                       <iframe className="document-iframe" src={documentUrl} title="Document Viewer" />
                    ) : docPreviewHtml ? (
                       <iframe className="document-iframe" srcDoc={docPreviewHtml} title="Document Preview" sandbox="allow-same-origin allow-scripts clamp-to-bounds" />
                    ) : (
                       <div className="placeholder-box">
                          {isProcessing ? (
                             <>
                               <IconSpinner style={{ width: "2rem", height: "2rem" }} />
                               <span className="font-medium text-foreground">Synchronizing preview artifact...</span>
                             </>
                          ) : (
                             <span>Load a document to begin.</span>
                          )}
                       </div>
                    )}
                 </div>
              </div>
            </div>

          </div>

          <div className="flex flex-col gap-6">
             <div className="ui-card">
               <div className="ui-card-header">
                 <h3 className="ui-card-title">Extracted Entities</h3>
                 <p className="ui-card-description">Semantic data points populated by the agentic core.</p>
               </div>
               
               <div className="ui-card-content" style={{ padding: "0 1.5rem 1.5rem" }}>
                 <div className="flex items-center justify-between gap-4" style={{ marginBottom: "1rem", marginTop: "-0.5rem" }}>
                   <div style={{ flex: 1 }}>
                     <input type="text" className="input text-sm" value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)} placeholder="Filter outputs..." />
                   </div>
                   <label className="checkbox-wrap text-sm font-medium cursor-pointer" title="Show only flagged items or parameters with no extracted value">
                     <input
                       type="checkbox"
                       className="checkbox"
                       checked={requiresActionOnly}
                       onChange={(e) => setRequiresActionOnly(e.target.checked)}
                     />
                     Requires Action
                   </label>
                 </div>

                 <div className="table-container">
                   <table className="data-table">
                     <thead>
                       <tr>
                         <th>Parameter</th>
                         <th>Value</th>
                         <th style={{ width: "88px" }}>Status</th>
                         <th style={{ width: "80px" }}>Conf.</th>
                         <th style={{ width: "80px" }}>Pages</th>
                       </tr>
                     </thead>
                     <tbody>
                       {filteredFields.map((field) => (
                         <FieldRow
                           key={field.field_id}
                           field={field}
                           active={field.field_id === selectedField?.field_id}
                           disabled={!job || job.status !== "completed"}
                           saving={savingFieldId === field.field_id}
                           onSelect={() => setSelectedFieldId(field.field_id)}
                           onCommit={(value) => void saveFieldValue(field.field_id, value)}
                         />
                       ))}
                       {filteredFields.length === 0 && (
                         <tr>
                           <td colSpan={5} style={{ textAlign: "center", padding: "3rem 1rem", color: "var(--muted-foreground)" }}>
                             {isProcessing ? "Building vector space representations..." : "No targets located."}
                           </td>
                         </tr>
                       )}
                     </tbody>
                   </table>
                 </div>
               </div>

               <div className="extracted-panel-footer">
                 <div className="extracted-panel-footer-row">
                   <button
                     type="button"
                     className="btn btn-primary"
                     disabled={!job || job.status !== "completed" || fields.length === 0}
                     onClick={() => setSpreadsheetOpen(true)}
                   >
                     Spreadsheet view
                   </button>
                 </div>
                 <div className="export-actions">
                   {job?.status === "completed" ? (
                     <>
                       <a className="btn btn-outline export-btn" href={`${API_BASE_URL}/api/v1/jobs/${job.id}/export?format=json`}>
                         Export JSON
                       </a>
                       <a className="btn btn-outline export-btn" href={`${API_BASE_URL}/api/v1/jobs/${job.id}/export?format=csv`}>
                         Export CSV
                       </a>
                       <a className="btn btn-outline export-btn" href={`${API_BASE_URL}/api/v1/jobs/${job.id}/export?format=xlsx`}>
                         Export Excel
                       </a>
                     </>
                   ) : (
                     <span className="export-placeholder text-sm text-muted-foreground">Exports and grid view unlock when the job completes.</span>
                   )}
                 </div>
               </div>
             </div>

             <div className="ui-card">
               <div className="ui-card-header">
                 <h3 className="ui-card-title">Citation Context</h3>
               </div>
               <div className="ui-card-content">
                 {selectedField ? (
                   <div className="flex flex-col gap-4">
                     <div className="flex flex-col gap-4">
                        <div>
                          <span className="label text-xs text-muted-foreground uppercase">Section</span>
                          <span className="font-medium text-foreground">{selectedField.source_section ?? "N/A"}</span>
                        </div>
                        <div>
                          <span className="label text-xs text-muted-foreground uppercase">Clause</span>
                          <span className="font-medium text-foreground block" style={{ display: "block" }}>{selectedField.source_clause ?? "N/A"}</span>
                        </div>
                     </div>
                     <div className="code-block" style={{ marginTop: "0.5rem" }}>
                        {selectedField.verbatim_evidence ?? "Verbatim mapping unavailable."}
                     </div>
                     <div className="flex flex-col gap-3">
                       <div>
                         <span className="label text-xs text-muted-foreground uppercase">Present In Preview</span>
                         <span className="font-medium text-foreground block" style={{ display: "block" }}>
                           {selectedField.page_range ? `Pages ${selectedField.page_range}` : "Preview text search"}
                         </span>
                       </div>
                       {documentMatches.length > 0 ? (
                         <div className="flex flex-col gap-2">
                           {documentMatches.map((match, index) => (
                             <div key={`${match.term}-${index}`} className="code-block">
                               {match.snippet}
                             </div>
                           ))}
                         </div>
                       ) : (
                         <p className="text-sm text-muted-foreground">
                           {docPreviewText
                             ? "No exact phrase match was located in the current preview text."
                             : "Preview text is not available for this document yet."}
                         </p>
                       )}
                     </div>
                   </div>
                 ) : (
                   <p className="text-sm text-muted-foreground">Select an entity to review citation.</p>
                 )}
               </div>
             </div>
          </div>
        </div>
      </main>

      <FieldsSpreadsheetOverlay
        open={spreadsheetOpen}
        onClose={() => setSpreadsheetOpen(false)}
        fields={fields}
        jobId={job?.status === "completed" ? job.id : null}
        onAfterSave={handleRefresh}
        onError={setError}
      />
    </div>
  );
}

function FieldRow({
  field,
  active,
  disabled,
  saving,
  onSelect,
  onCommit,
}: {
  field: FieldValue;
  active: boolean;
  disabled: boolean;
  saving: boolean;
  onSelect: () => void;
  onCommit: (value: string) => void;
}) {
  const serverValue = field.value ?? "";
  const [local, setLocal] = useState(serverValue);

  useEffect(() => {
    setLocal(serverValue);
  }, [field.field_id, serverValue]);

  const placeholder = field.is_absent ? "[Review required]" : "";

  return (
    <tr className={active ? "selected" : ""} onClick={onSelect}>
      <td className="font-medium text-foreground field-row-label">{field.label}</td>
      <td className="field-row-value-cell">
        <div className="inline-value-wrap">
          <textarea
            className="inline-field-value"
            rows={2}
            value={local}
            disabled={disabled || saving}
            placeholder={placeholder}
            aria-label={`Value for ${field.label}`}
            onChange={(e) => setLocal(e.target.value)}
            onFocus={onSelect}
            onBlur={(e) => onCommit(e.currentTarget.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                setLocal(serverValue);
                (e.target as HTMLTextAreaElement).blur();
              }
            }}
          />
          {saving && (
            <span className="inline-value-saving" aria-hidden>
              <IconSpinner style={{ width: "0.875rem", height: "0.875rem" }} />
            </span>
          )}
        </div>
      </td>
      <td>
        <span className={`badge badge-${field.is_absent ? "warning" : "success"}`}>
          {field.is_absent ? "Flagged" : "Pass"}
        </span>
      </td>
      <td>
        <span className={`badge badge-${toneForConfidence(field.confidence)}`}>
          {field.confidence == null ? "-" : `${(field.confidence * 100).toFixed(0)}%`}
        </span>
      </td>
      <td className="text-muted-foreground">{field.page_range ?? "-"}</td>
    </tr>
  );
}

function toneForConfidence(confidence: number | null): string {
  if (confidence == null) return "neutral";
  if (confidence >= 0.9) return "success";
  if (confidence >= 0.7) return "info";
  return "warning";
}

function toneForJobStatus(status: string | undefined): string {
  switch (status) {
    case "completed": return "success";
    case "failed": return "destructive";
    case "extracting":
    case "indexing":
    case "parsing":
    case "queued": return "info";
    default: return "neutral";
  }
}

function buildPdfAnchor(field: FieldValue | null): string {
  if (!field?.page_range) return "";
  const page = field.page_range.split("-")[0];
  return `#page=${page}`;
}

function buildDocumentMatches(previewText: string | null, field: FieldValue | null) {
  if (!previewText || !field) return [];

  const normalizedPreview = normalizeForDocumentMatch(previewText);
  const matches: Array<{ term: string; snippet: string }> = [];
  const seenSnippets = new Set<string>();

  for (const term of buildFieldSearchTerms(field)) {
    const normalizedTerm = normalizeForDocumentMatch(term);
    if (normalizedTerm.length < 8) continue;

    const match = buildSearchPattern(normalizedTerm).exec(normalizedPreview);
    if (!match || match.index == null) continue;

    const start = Math.max(0, match.index - 140);
    const end = Math.min(normalizedPreview.length, match.index + match[0].length + 140);
    const snippet = normalizedPreview.slice(start, end).replace(/\s+/g, " ").trim();
    const displaySnippet = `${start > 0 ? "..." : ""}${snippet}${end < normalizedPreview.length ? "..." : ""}`;
    const dedupeKey = displaySnippet.toLowerCase();
    if (seenSnippets.has(dedupeKey)) continue;

    seenSnippets.add(dedupeKey);
    matches.push({ term, snippet: displaySnippet });
    if (matches.length >= 3) break;
  }

  return matches;
}

function buildFieldSearchTerms(field: FieldValue | null): string[] {
  if (!field) return [];

  const sources = [field.verbatim_evidence, field.value, field.source_clause]
    .map((value) => normalizeWhitespace(value ?? ""))
    .filter((value) => value.length >= 3)
    .sort((left, right) => right.length - left.length);

  const expanded: string[] = [];
  for (const text of sources) {
    expanded.push(...expandSearchPhrases(text));
  }

  const unique = Array.from(new Set(expanded));
  unique.sort((a, b) => a.length - b.length);
  return unique.slice(0, 12);
}

/** Long LLM excerpts rarely match preview text verbatim; try shorter word windows first. */
function expandSearchPhrases(text: string): string[] {
  const out: string[] = [];
  const words = text.split(" ").filter(Boolean);
  if (words.length === 0) return out;

  const push = (s: string) => {
    const t = normalizeWhitespace(s);
    if (t.length >= 8) out.push(t);
  };

  if (text.length <= 160) {
    push(text);
    return out;
  }

  push(words.slice(0, 14).join(" "));
  push(words.slice(0, 22).join(" "));
  if (words.length > 28) push(words.slice(6, 22).join(" "));
  if (words.length > 36) push(words.slice(12, 28).join(" "));
  push(text.slice(0, 100));
  push(text.slice(0, 160));
  push(text);

  return out;
}

function normalizeForDocumentMatch(value: string): string {
  return normalizeWhitespace(
    value
      .replace(/\u00a0/g, " ")
      .replace(/[\u2018\u2019\u201a\u201b]/g, "'")
      .replace(/[\u201c\u201d\u201e\u201f]/g, '"')
      .replace(/\s*&\s*/g, " and ")
      .replace(/[\u2013\u2014]/g, "-"),
  );
}

function buildHighlightedPreviewHtml(previewHtml: string, field: FieldValue | null): string {
  if (typeof window === "undefined") return previewHtml;

  const searchTerms = buildFieldSearchTerms(field);
  if (searchTerms.length === 0) return previewHtml;

  const parser = new DOMParser();
  const documentNode = parser.parseFromString(previewHtml, "text/html");
  const style = documentNode.createElement("style");
  style.textContent = 'mark[data-entity-match="true"] { background: #fde68a; color: inherit; padding: 0 0.15rem; border-radius: 0.2rem; }';
  documentNode.head.appendChild(style);

  const textNodes: Text[] = [];
  const walker = documentNode.createTreeWalker(documentNode.body, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const parent = node.parentElement;
      if (!parent || !node.textContent?.trim()) return NodeFilter.FILTER_REJECT;
      if (["MARK", "SCRIPT", "STYLE", "NOSCRIPT"].includes(parent.tagName)) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    },
  });

  while (walker.nextNode()) {
    textNodes.push(walker.currentNode as Text);
  }

  let highlightCount = 0;
  for (const textNode of textNodes) {
    const content = textNode.textContent ?? "";
    for (const term of searchTerms) {
      const match = buildSearchPattern(term).exec(content);
      if (!match || match.index == null) continue;

      const highlightedNode = textNode.splitText(match.index);
      highlightedNode.splitText(match[0].length);
      const mark = documentNode.createElement("mark");
      mark.setAttribute("data-entity-match", "true");
      mark.textContent = highlightedNode.textContent;
      highlightedNode.parentNode?.replaceChild(mark, highlightedNode);
      highlightCount += 1;
      break;
    }

    if (highlightCount >= 20) break;
  }

  if (highlightCount > 0) {
    const script = documentNode.createElement("script");
    script.textContent =
      'window.addEventListener("load", function () { const target = document.querySelector(\'mark[data-entity-match="true"]\'); if (target) target.scrollIntoView({ behavior: "smooth", block: "center" }); });';
    documentNode.body.appendChild(script);
  }

  return `<!DOCTYPE html>\n${documentNode.documentElement.outerHTML}`;
}

function buildSearchPattern(term: string): RegExp {
  const normalized = normalizeWhitespace(term);
  const parts = normalized.split(" ").filter(Boolean).map(escapeRegExp);
  if (parts.length === 0) return /$^/i;
  // Allow flexible whitespace and optional punctuation between words (PDF/HTML extraction varies).
  const sep = "[\\s\\u00a0]*(?:[\\-–—,;:.]\\s*)?";
  return new RegExp(parts.join(sep), "i");
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function normalizeWhitespace(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

async function fetchJson<T>(url: string): Promise<T> {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw new Error(await response.text());
  return (await response.json()) as T;
}
