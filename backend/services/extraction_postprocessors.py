from __future__ import annotations

import re
from collections.abc import Callable

from backend.api_models import ExtractionResult
from backend.schemas import ExtractionFieldDef


GENERIC_ENTITY_VALUES = {
    "administrative agent",
    "agent",
    "borrower",
    "company",
    "issuer",
    "issuing lender",
    "lender",
    "lenders",
    "loan party",
    "loan parties",
    "obligor",
}

DATE_PATTERN = re.compile(
    r"\b("
    r"January|February|March|April|May|June|July|August|September|October|November|December"
    r")\s+\d{1,2},\s+\d{4}\b"
)
DATE_CAPTURE = (
    r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+\d{1,2},\s+\d{4}"
)


def apply_field_post_processor(
    field_def: ExtractionFieldDef,
    result: ExtractionResult,
    chunks: list[dict],
) -> ExtractionResult:
    processed = result.model_copy(deep=True)
    processor_name = field_def.post_processor or field_def.id
    processor = _PROCESSORS.get(processor_name) or _ANSWER_STYLE_PROCESSORS.get(field_def.answer_style)
    if processor is not None:
        processed = processor(processed, chunks)

    if processed.value:
        processed.value = _clean_value(processed.value)

    if processed.value:
        processed.is_absent = False
    elif processed.is_absent:
        processed.confidence = processed.confidence or 0.0

    return processed


def _borrower_name(result: ExtractionResult, chunks: list[dict]) -> ExtractionResult:
    if not _is_generic_entity(result.value):
        normalized = _normalize_entity_name(result.value)
        if normalized:
            result.value = normalized
            return result

    patterns = [
        re.compile(
            r"entered into among\s+(?P<name>.+?)\s*\(\s*(?:the\s+)?[^)]{0,40}?company[^)]{0,10}\)",
            flags=re.IGNORECASE | re.DOTALL,
        ),
        re.compile(r"(?P<name>[A-Z][A-Z0-9&.,'() /\-]{4,}?)\s*\(\s*(?:the\s+)?[^)]{0,40}?company[^)]{0,10}\)", flags=re.IGNORECASE),
    ]
    return _extract_with_patterns(result, chunks, patterns, _normalize_entity_name, group_name="name")


def _lender_name(result: ExtractionResult, chunks: list[dict]) -> ExtractionResult:
    normalized = _normalize_entity_name(result.value)
    if normalized and not _is_generic_entity(normalized):
        result.value = normalized
        return result

    patterns = [
        re.compile(
            r"and\s+(?P<name>[A-Z][A-Z0-9&.,'() /\-]{4,}?)\s*\(in its individual capacity",
            flags=re.IGNORECASE,
        ),
        re.compile(
            r"(?P<name>[A-Z][A-Z0-9&.,'() /\-]{4,}?),\s+as administrative agent",
            flags=re.IGNORECASE,
        ),
    ]
    return _extract_with_patterns(result, chunks, patterns, _normalize_entity_name, group_name="name")


def _facility_amount(result: ExtractionResult, chunks: list[dict]) -> ExtractionResult:
    value = _clean_value(result.value)
    if value and _looks_like_overall_facility_amount(value):
        result.value = value
        return result

    if value and _looks_like_bad_facility_amount(value):
        result.value = None
        result.is_absent = True
        result.confidence = 0.0

    patterns = [
        re.compile(
            r"(?:aggregate|total|maximum)\s+(?:principal amount|commitments?|facility)[^$]{0,80}\$(?P<amount>[0-9][0-9,]+(?:\.\d{2})?)",
            flags=re.IGNORECASE,
        ),
        re.compile(
            r"(?:facility|commitment)\s+amount[^$]{0,40}\$(?P<amount>[0-9][0-9,]+(?:\.\d{2})?)",
            flags=re.IGNORECASE,
        ),
    ]
    return _extract_with_patterns(
        result,
        chunks,
        patterns,
        lambda amount: f"${amount}",
        should_accept=lambda match, text: "letter of credit" not in text.lower() and "not less than" not in text.lower(),
        group_name="amount",
    )


def _currency(result: ExtractionResult, chunks: list[dict]) -> ExtractionResult:
    normalized = _normalize_currency(result.value)
    if normalized:
        result.value = normalized
        return result

    for chunk in chunks:
        text = _chunk_text(chunk)
        normalized = _normalize_currency(text)
        if normalized:
            result.value = normalized
            result.is_absent = False
            result.confidence = max(result.confidence or 0.0, 0.92)
            _apply_chunk_citation(result, chunk)
            return result
    return result


def _maturity_date(result: ExtractionResult, chunks: list[dict]) -> ExtractionResult:
    date_value = _extract_date(result.value)
    if date_value:
        result.value = date_value
        return result

    patterns = [
        re.compile(r"Termination Date means[^.]{0,220}?(?P<value>" + DATE_CAPTURE + r")", flags=re.IGNORECASE),
        re.compile(r"Maturity Date means[^.]{0,220}?(?P<value>" + DATE_CAPTURE + r")", flags=re.IGNORECASE),
    ]
    return _extract_with_patterns(result, chunks, patterns, _clean_value)


def _interest_rate(result: ExtractionResult, chunks: list[dict]) -> ExtractionResult:
    value = _clean_value(result.value)
    if value and "specific percentages referenced" not in value.lower():
        result.value = value
        return result

    patterns = [
        re.compile(
            r"rate per annum equal to the sum of the Base Rate[^.]+Base Rate Margin[^.]+LIBOR Rate[^.]+LIBOR Margin[^.]+",
            flags=re.IGNORECASE,
        ),
    ]
    return _extract_with_patterns(result, chunks, patterns, _clean_value, group_name=None)


def _governing_law(result: ExtractionResult, chunks: list[dict]) -> ExtractionResult:
    jurisdiction = _extract_jurisdiction(result.value)
    if jurisdiction:
        result.value = jurisdiction
        return result

    patterns = [
        re.compile(r"laws of the State of (?P<value>[A-Z][a-z]+(?: [A-Z][a-z]+)*)", flags=re.IGNORECASE),
        re.compile(r"governed by the laws of (?P<value>[A-Z][a-z]+(?: [A-Z][a-z]+)*)", flags=re.IGNORECASE),
    ]
    return _extract_with_patterns(
        result,
        chunks,
        patterns,
        lambda value: f"State of {value}" if not value.lower().startswith("state of ") else value,
    )


def _default_summary(result: ExtractionResult, _: list[dict]) -> ExtractionResult:
    if result.value:
        result.value = _clean_value(result.value)
    return result


def _generic_entity(result: ExtractionResult, chunks: list[dict]) -> ExtractionResult:
    normalized = _normalize_entity_name(result.value)
    if normalized and not _is_generic_entity(normalized):
        result.value = normalized
        return result

    for chunk in chunks:
        text = _chunk_text(chunk)
        match = re.search(r"\b([A-Z][A-Z0-9&.,'() /\-]{5,})\b", text)
        if not match:
            continue
        normalized = _normalize_entity_name(match.group(1))
        if not normalized or _is_generic_entity(normalized):
            continue
        result.value = normalized
        result.is_absent = False
        result.confidence = max(result.confidence or 0.0, 0.85)
        _apply_chunk_citation(result, chunk)
        return result
    return result


def _generic_date(result: ExtractionResult, chunks: list[dict]) -> ExtractionResult:
    extracted = _extract_date(result.value)
    if extracted:
        result.value = extracted
        return result

    for chunk in chunks:
        extracted = _extract_date(_chunk_text(chunk))
        if not extracted:
            continue
        result.value = extracted
        result.is_absent = False
        result.confidence = max(result.confidence or 0.0, 0.88)
        _apply_chunk_citation(result, chunk)
        return result
    return result


def _generic_amount(result: ExtractionResult, chunks: list[dict]) -> ExtractionResult:
    value = _clean_value(result.value)
    if value and ("$" in value or re.search(r"\b\d[\d,]*(?:\.\d{2})?\b", value)):
        result.value = value
        return result

    for chunk in chunks:
        text = _chunk_text(chunk)
        match = re.search(r"(\$[0-9][0-9,]+(?:\.\d{2})?)", text)
        if not match:
            continue
        result.value = match.group(1)
        result.is_absent = False
        result.confidence = max(result.confidence or 0.0, 0.85)
        _apply_chunk_citation(result, chunk)
        return result
    return result


_PROCESSORS: dict[str, Callable[[ExtractionResult, list[dict]], ExtractionResult]] = {
    "borrower_name": _borrower_name,
    "currency": _currency,
    "facility_amount": _facility_amount,
    "financial_covenants": _default_summary,
    "governing_law": _governing_law,
    "interest_rate": _interest_rate,
    "lender_name": _lender_name,
    "maturity_date": _maturity_date,
    "security_package": _default_summary,
}

_ANSWER_STYLE_PROCESSORS: dict[str, Callable[[ExtractionResult, list[dict]], ExtractionResult]] = {
    "amount": _generic_amount,
    "currency": _currency,
    "date": _generic_date,
    "entity": _generic_entity,
    "jurisdiction": _governing_law,
    "rate": _default_summary,
    "summary": _default_summary,
    "text": _default_summary,
}


def _extract_with_patterns(
    result: ExtractionResult,
    chunks: list[dict],
    patterns: list[re.Pattern[str]],
    normalizer: Callable[[str], str | None],
    *,
    should_accept: Callable[[re.Match[str], str], bool] | None = None,
    group_name: str = "value",
) -> ExtractionResult:
    for chunk in chunks:
        text = _chunk_text(chunk)
        for pattern in patterns:
            match = pattern.search(text)
            if not match:
                continue
            if should_accept is not None and not should_accept(match, text):
                continue

            raw_value = match.group(group_name) if group_name else match.group(0)
            normalized = normalizer(raw_value)
            if not normalized:
                continue

            result.value = normalized
            result.is_absent = False
            result.confidence = max(result.confidence or 0.0, 0.92)
            _apply_chunk_citation(result, chunk)
            result.verbatim_evidence = _clean_value(match.group(0))[:400]
            return result
    return result


def _apply_chunk_citation(result: ExtractionResult, chunk: dict) -> None:
    page_start = chunk.get("page_start")
    page_end = chunk.get("page_end") or page_start

    if chunk.get("clause_id"):
        result.source_clause = chunk["clause_id"]
    if chunk.get("section_title"):
        result.source_section = chunk["section_title"]
    if page_start:
        result.page_range = f"{page_start}-{page_end}"
    if chunk.get("chunk_id"):
        result.chunk_ids = [chunk["chunk_id"]]
    if not result.verbatim_evidence:
        result.verbatim_evidence = _clean_value(_chunk_text(chunk)[:400])


def _chunk_text(chunk: dict) -> str:
    return str(chunk.get("text") or "").replace("\xa0", " ")


def _clean_value(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip(" \n\t.;:")
    return cleaned or None


def _normalize_entity_name(value: str | None) -> str | None:
    cleaned = _clean_value(value)
    if not cleaned:
        return None

    cleaned = cleaned.replace("�", "")
    cleaned = re.sub(r"\(.*?\)", "", cleaned).strip(" ,;:")
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned or None


def _normalize_currency(value: str | None) -> str | None:
    cleaned = (_clean_value(value) or "").lower()
    if not cleaned:
        return None
    if "united states dollars" in cleaned or "lawful money of the united states of america" in cleaned:
        return "USD"
    if re.search(r"\busd\b", cleaned):
        return "USD"
    if "dollars" in cleaned and "canadian" not in cleaned and "australian" not in cleaned:
        return "USD"
    return None


def _extract_date(value: str | None) -> str | None:
    cleaned = _clean_value(value)
    if not cleaned:
        return None
    match = DATE_PATTERN.search(cleaned)
    return match.group(0) if match else None


def _extract_jurisdiction(value: str | None) -> str | None:
    cleaned = _clean_value(value)
    if not cleaned:
        return None

    state_match = re.search(r"State of ([A-Z][a-z]+(?: [A-Z][a-z]+)*)", cleaned)
    if state_match:
        return f"State of {state_match.group(1)}"
    return None


def _is_generic_entity(value: str | None) -> bool:
    cleaned = (_clean_value(value) or "").lower()
    if not cleaned:
        return True
    return cleaned in GENERIC_ENTITY_VALUES


def _looks_like_bad_facility_amount(value: str) -> bool:
    lowered = value.lower()
    bad_signals = [
        "annex a",
        "each lender",
        "higher integral multiple",
        "letter of credit",
        "minimum",
        "not less than",
        "reduction",
    ]
    return any(signal in lowered for signal in bad_signals)


def _looks_like_overall_facility_amount(value: str) -> bool:
    if "$" not in value:
        return False
    return not _looks_like_bad_facility_amount(value)
