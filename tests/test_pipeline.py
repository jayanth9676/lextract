from backend.models import DocumentChunk
from backend.schemas import ExtractionFieldDef
from backend.services.pipeline import PipelineService


def test_parse_page_range_accepts_missing_and_mixed_values() -> None:
    assert PipelineService._parse_page_range(None) == (None, None)
    assert PipelineService._parse_page_range("12-14") == (12, 14)
    assert PipelineService._parse_page_range("Page 9") == (9, 9)
    assert PipelineService._parse_page_range("None - None") == (None, None)


def test_supplement_field_chunks_promotes_exact_definition_matches() -> None:
    field = ExtractionFieldDef(
        id="maturity_date",
        label="Maturity / Final Repayment Date",
        answer_style="date",
        retrieval_limit=3,
        aliases=["termination date"],
        evidence_hints=["Termination Date means"],
    )
    retrieved_chunks = [
        {
            "chunk_id": "retrieved-1",
            "clause_id": "SECTION 2.3.1",
            "section_title": "Section 2.3.1",
            "page_start": 22,
            "page_end": 22,
            "text": "The letter of credit remains outstanding until the scheduled Termination Date.",
        }
    ]
    all_chunks = [
        DocumentChunk(
            id="exact-1",
            job_id="job-1",
            chunk_tier="A",
            clause_id=None,
            section_title="Definitions",
            page_start=20,
            page_end=20,
            token_count=30,
            text="Termination Date means the earlier to occur of (a) October 11, 2007 or (b) such other date on which the Commitments terminate pursuant to Section 6 or 14.",
            chunk_metadata={},
        )
    ]

    supplemented = PipelineService._supplement_field_chunks(field, retrieved_chunks, all_chunks)

    assert supplemented[0]["chunk_id"] == "exact-1"
    assert "October 11, 2007" in supplemented[0]["text"]

