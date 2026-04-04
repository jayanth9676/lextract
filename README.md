# LexExtract

LexExtract is an AI-powered loan agreement extraction system built for high-traceability review workflows:

- Backend: FastAPI + Docling + Qdrant + Anthropic Claude
- Frontend: Next.js App Router + TypeScript
- Persistence: SQLite for jobs, extracted fields, and audit logs

## Features

- Upload `.pdf` and `.docx` loan agreements
- Parse documents with Docling and create clause-level plus section-level chunks
- Index dense+sparse vectors in Qdrant using hybrid RRF retrieval
- Extract structured fields with Claude using `messages.parse` and Pydantic output
- Review results in a dual-pane UI with citations, inline corrections, and export
- Log manual edits with `X-User-Id` for compliance-style audit history

## Prerequisites

- Windows 10/11
- Python 3.13
- Node.js 20+
- `uv` installed
- Qdrant endpoint and API key
- Anthropic API key

## Environment

Copy `.env.example` to `.env` and fill in your values:

```bash
DATABASE_URL=sqlite+aiosqlite:///./data/lexextract.db
CORS_ORIGINS=http://localhost:3000
QDRANT_URL=https://your-qdrant-cluster.example.com
QDRANT_API_KEY=
QDRANT_COLLECTION=loan_agreement_chunks
CLAUDE_API_KEY=
CLAUDE_MODEL=claude-sonnet-4-20250514
DENSE_MODEL=sentence-transformers/all-MiniLM-L6-v2
SPARSE_MODEL=Qdrant/bm25
EXTRACTION_CONCURRENCY=6
```

## Backend setup with uv

```powershell
uv venv .venv
. .\.venv\Scripts\Activate.ps1
uv add -r requirements.txt -p .\.venv\Scripts\python.exe
uv add -r requirements-dev.txt --dev -p .\.venv\Scripts\python.exe
```

Run the API:

```powershell
. .\.venv\Scripts\Activate.ps1
python main.py
```

The API is available at `http://127.0.0.1:8000`.

## Frontend setup

```powershell
Set-Location .\frontend
npm install
$env:NEXT_PUBLIC_API_BASE_URL = "http://127.0.0.1:8000"
npm run dev
```

The UI is available at `http://localhost:3000`.

## Tests

```powershell
. .\.venv\Scripts\Activate.ps1
pytest
```

## Notes

- `.env.example` contains placeholders only. Never commit real secrets.
- The project is configured for Windows CPU-only inference dependencies.
- First-time Docling and FastEmbed usage may download model assets from Hugging Face.
