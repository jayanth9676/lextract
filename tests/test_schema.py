from backend.schemas import load_extraction_schema


def test_extraction_schema_loads_representative_fields() -> None:
    schema = load_extraction_schema()

    field_ids = {field.id for field in schema.fields}
    assert len(schema.fields) == 49
    assert len(field_ids) == 49
    assert all(field.category for field in schema.fields)
    assert "borrower_name" in field_ids
    assert "facility_amount" in field_ids
    assert "loan_agreement_id" in field_ids
    assert "agent_bank_details_role" in field_ids
    assert "language_of_agreement" in field_ids
    assert all(field.retrieval_queries for field in schema.fields)
    assert all(field.prompt_instructions for field in schema.fields)

    borrower_field = next(field for field in schema.fields if field.id == "borrower_name")
    assert borrower_field.category == "Identification"
    assert borrower_field.answer_style == "entity"
    assert borrower_field.post_processor == "borrower_name"
    assert "entered into among" in borrower_field.retrieval_text
