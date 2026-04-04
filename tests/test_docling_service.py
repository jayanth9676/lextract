from backend.services.docling_service import DoclingService, ParsedChunk


def test_section_chunks_group_same_heading() -> None:
    service = DoclingService.__new__(DoclingService)

    clause_chunks = [
        ParsedChunk(
            id="a1",
            chunk_tier="A",
            text="Borrower details",
            clause_id="1",
            section_title="Parties",
            page_start=1,
            page_end=1,
            token_count=100,
            metadata={},
        ),
        ParsedChunk(
            id="a2",
            chunk_tier="A",
            text="Lender details",
            clause_id="2",
            section_title="Parties",
            page_start=1,
            page_end=2,
            token_count=120,
            metadata={},
        ),
        ParsedChunk(
            id="a3",
            chunk_tier="A",
            text="Repayment mechanics",
            clause_id="3",
            section_title="Repayment",
            page_start=2,
            page_end=3,
            token_count=150,
            metadata={},
        ),
    ]

    tier_b = service._build_section_chunks(clause_chunks)

    assert len(tier_b) == 2
    assert tier_b[0].section_title == "Parties"
    assert tier_b[0].page_start == 1
    assert tier_b[0].page_end == 2
    assert tier_b[1].section_title == "Repayment"


def test_split_text_limits_large_chunks() -> None:
    service = DoclingService.__new__(DoclingService)
    long_text = " ".join(["loan"] * 900)

    parts = service._split_text(long_text, max_tokens=420, overlap_tokens=40)

    assert len(parts) > 1
    assert all(service._estimate_tokens(part) <= 420 for part in parts)


def test_noise_filter_skips_toc_like_chunks() -> None:
    service = DoclingService.__new__(DoclingService)
    toc_text = "SECTION 1 = 1, SECTION 2 = 3, SECTION 3 = 7, SECTION 4 = 11, SECTION 5 = 13"

    assert service._is_noise_chunk(toc_text) is True
    assert service._is_noise_chunk("Borrower shall repay the loans on the maturity date.") is False
