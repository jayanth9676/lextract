from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from backend.config import get_settings


def sanitize_filename(filename: str) -> str:
    safe = "".join(ch for ch in filename if ch.isalnum() or ch in {"-", "_", "."}).strip(".")
    return safe or f"document-{uuid4().hex}.bin"


async def save_upload(job_id: str, upload: UploadFile) -> Path:
    settings = get_settings()
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    job_dir = settings.uploads_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    filename = sanitize_filename(upload.filename or "upload.bin")
    target = job_dir / filename

    with target.open("wb") as file_obj:
        while chunk := await upload.read(1024 * 1024):
            file_obj.write(chunk)

    await upload.close()
    return target
