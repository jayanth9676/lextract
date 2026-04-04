export type Job = {
  id: string;
  filename: string;
  original_filename: string;
  status: string;
  progress: number;
  error_message?: string | null;
  created_at: string;
  updated_at: string;
};

export type FieldValue = {
  field_id: string;
  label: string;
  category: string;
  value: string | null;
  confidence: number | null;
  source_clause: string | null;
  source_section: string | null;
  page_range: string | null;
  is_absent: boolean;
  verbatim_evidence: string | null;
  chunk_ids: string[];
  extra: Record<string, unknown>;
};

export type JobResult = {
  job: Job;
  fields: FieldValue[];
  document_url: string;
  preview_url: string;
};
