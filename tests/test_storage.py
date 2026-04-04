from backend.services.storage import sanitize_filename


def test_sanitize_filename_removes_unsafe_characters() -> None:
    assert sanitize_filename("../../loan agreement?.pdf") == "loanagreement.pdf"
