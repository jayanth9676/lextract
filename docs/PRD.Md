# Product Requirements Document (PRD)

**Product Name:** LexExtract (Loan Agreement AI Extractor)
**Document Date:** April 2026
**Target Release:** Q3 2026
**Status:** Final

## 1. Executive Summary

### 1.1 Business Problem
Various business teams rely on manual review of highly complex, unstructured loan agreements (often 100+ pages) to obtain critical processing data. This data is locked in legal text and not captured in downstream systems. Across the project lifecycle (Loan Approval, Commitment, Disbursement), these agreements are reviewed at least 3 times by over 15 different teams, leading to massive operational bottlenecks and high risk of human error.

### 1.2 Proposed Solution
An AI-powered document extraction pipeline that automatically ingests loan agreements, identifies key terms, and structures them into a standardized format. The system utilizes a modern RAG (Retrieval-Augmented Generation) architecture, leveraging Docling for parsing, Qdrant for hybrid retrieval, and the Claude API for deterministic data extraction. 

### 1.3 Business Value & KPIs
* **Time Savings:** Reduce manual review time per document by 80%.
* **Accuracy:** Achieve >95% extraction accuracy with a human-in-the-loop (HITL) validation interface.
* **Traceability:** Provide 100% exact-text citations for every extracted value to ensure trust and auditability.

---

## 2. Technical Stack

* **Document Parsing:** Docling (Native DOCX and PDF structural parsing)
* **Vector Database & Retrieval:** Qdrant Client (In-memory/Cloud) using Dense + Sparse (BM25) models
* **LLM Engine:** Claude API (utilizing Tool Use and Prompt Caching)
* **Data Validation:** Pydantic
* **Frontend UI:** React/Next.js (World Bank theme styling)

---

## 3. The Extraction Pipeline

### Stage 1 — Ingestion & Pre-processing
Before chunking, the system requires a normalized, structure-aware text representation of the document.
* **Docling** is used exclusively for parsing the uploaded `.docx` and `.pdf` files.
* Docling extracts the document's *structural skeleton*—the table of contents, numbered clause hierarchy, exact table layouts, and page boundaries—before chunking. This skeleton forms the metadata backbone for citations.

### Stage 2 — Hierarchical Chunking Strategy
For 100+ page agreements, the system utilizes Docling's `HybridChunker` in a two-tier strategy:
* **Tier A (Clause-level chunks, ~300–500 tokens):** Split on natural legal boundaries (numbered clauses, definition entries, schedule rows) while preserving 10% token overlap.
* **Tier B (Section-level chunks, ~5,000–10,000 tokens):** Full sections or schedules kept intact to leverage Claude's large context window for broader disambiguation.
* **Metadata Tagging:** Every chunk is automatically tagged by Docling with `clause_id`, `section_title`, `page_start`, and `page_end`.

### Stage 3 — Dual Embedding & Indexing (Qdrant)
Chunks are stored in a single Qdrant collection using two complementary search mechanisms:
* **Dense Embeddings** (e.g., `all-MiniLM-L6-v2`) to capture semantic similarity.
* **Sparse Index** (e.g., `Qdrant/bm25`) to capture exact legal keyword matches (e.g., "LIBOR", "DSCR").

### Stage 4 — Field-Aware Hybrid Retrieval
The system runs parallel targeted retrievals for each field defined in the extraction schema (e.g., 48 distinct fields).
* Each field utilizes a pre-designed query semantically tuned to that specific term.
* **Native RRF:** The Python orchestration layer uses Qdrant's `Prefetch` API to grab both Dense and Sparse results, and natively fuses them using Qdrant’s `FusionQuery(fusion=models.Fusion.RRF)`.
* The system isolates the top 3 clause-level chunks per field to pass to the LLM.

### Stage 5 — Claude API Extraction (Tool Use & Caching)
For each target field, the system calls the **Claude API** via an asynchronous batching script.
1. **Tool Use:** The required output schema (`value`, `confidence`, `source_clause`, `source_section`, `page_range`, `is_absent`) is enforced using Pydantic and passed to Claude as a required Tool/Function.
2. **Prompt Caching:** System instructions and global document definitions are cached using Claude's Prompt Caching to drastically reduce latency and cost for the parallel field queries.
3. **Zero Hallucination Constraint:** Claude is strictly instructed to return `is_absent: true` if the provided chunks do not contain explicit evidence for the field, resulting in a safe `[NOT FOUND]` output rather than an AI guess.

### Stage 6 — Output & Citation Generation
The final deliverable provides the extracted values alongside their verifiable sources.
* For every non-null field, the output includes the exact `clause_id`, `section_title`, `page_range`, and verbatim chunk text.
* Reviewers can instantly verify the LLM's work without searching the entire document.

---

## 4. UI / UX Requirements

* **Theme:** Professional enterprise styling utilizing the World Bank color palette (Blues, Whites, neutral grays).
* **Ingestion:** "Upload Loan Agreement" button supporting drag-and-drop for `.docx` and `.pdf` files.
* **Review Interface (HitL):**
  * Dual-pane view: "Source Document Viewer" on one side, "Extracted Data (CSV view)" on the other.
  * Clicking an extracted term dynamically scrolls the document viewer to the exact cited page and highlights the text.
  * Inline editing capabilities to correct or modify extracted values.
* **Export:** One-click download to export the verified data as JSON or CSV for downstream core banking systems.

---

## 5. Non-Functional Requirements (NFRs)

* **Performance:** The end-to-end pipeline (Docling parsing → Qdrant retrieval → Claude extraction) for a 500-page document must complete in under 90 seconds.
* **Auditability:** Any manual edits made by users in the UI must be logged (timestamp, user ID, original value, new value) for compliance tracking. with backend integration. should be professional & enterprise ready.