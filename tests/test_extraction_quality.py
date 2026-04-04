import json
from pathlib import Path

from backend.api_models import ExtractionResult
from backend.schemas import load_extraction_schema
from backend.services.extraction_postprocessors import apply_field_post_processor


def _fixture_path() -> Path:
    return Path(__file__).resolve().parent / "fixtures" / "sample_agreement_quality_fixture.json"


def _load_fixture() -> dict:
    return json.loads(_fixture_path().read_text(encoding="utf-8"))


def _assert_expected(result: ExtractionResult, expected: dict) -> None:
    match_type = expected["match_type"]
    if match_type == "absent":
        assert result.is_absent is True
        assert result.value is None
        return

    assert result.is_absent is expected["is_absent"]
    if match_type == "exact":
        assert result.value == expected["value"]
        return

    raise AssertionError(f"Unsupported match type: {match_type}")


def test_sample_agreement_quality_fixture_passes_postprocessors() -> None:
    schema = load_extraction_schema()
    field_map = {field.id: field for field in schema.fields}
    fixture = _load_fixture()

    processed_results: dict[str, ExtractionResult] = {}
    for case in fixture["cases"]:
        field = field_map[case["field_id"]]
        raw_result = ExtractionResult.model_validate(case["raw_result"])
        processed = apply_field_post_processor(field, raw_result, case["chunks"])
        _assert_expected(processed, case["expected"])
        processed_results[case["field_id"]] = processed

    assert processed_results["borrower_name"].source_section == "Preamble"
    assert processed_results["maturity_date"].page_range == "20-20"
    assert processed_results["currency"].value == "USD"
