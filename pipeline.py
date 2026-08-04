from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path

from resumeextraction import extract_text_native, extract_text_ocr
from parsing import parse_resume_text
from schema import CandidateProfile
from pdfvalidation import PdfStatus, validate_pdf


class ResumeProcessingError(Exception):
    def __init__(self, file_path: str, status: PdfStatus, message: str):
        self.file_path = file_path
        self.status = status
        self.message = message
        super().__init__(f"{file_path}: [{status.value}] {message}")


def process_resume(file_path: str | Path) -> CandidateProfile:
    file_path = str(file_path)

    validation = validate_pdf(file_path)
    if not validation.is_processable:
        raise ResumeProcessingError(file_path, validation.status, validation.message)

    if validation.needs_ocr:
        extraction = extract_text_ocr(file_path)
    else:
        extraction = extract_text_native(file_path)

    profile = parse_resume_text(
        resume_text=extraction.full_text,
        source_file=file_path,
        extraction_method=extraction.method,
    )
    profile.extraction_warnings = extraction.warnings + profile.extraction_warnings
    return profile


@dataclass
class BatchResult:
    succeeded: list[CandidateProfile]
    failed: list[dict]  # {"file": str, "status": str, "message": str}


def process_batch(file_paths: list[str | Path]) -> BatchResult:
    succeeded: list[CandidateProfile] = []
    failed: list[dict] = []

    for fp in file_paths:
        try:
            succeeded.append(process_resume(fp))
        except ResumeProcessingError as e:
            failed.append({"file": e.file_path, "status": e.status.value, "message": e.message})
        except Exception as e:  # noqa: BLE001 - keep the batch going
            failed.append({"file": str(fp), "status": "unexpected_error", "message": str(e)})

    return BatchResult(succeeded=succeeded, failed=failed)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python pipeline.py <resume.pdf> [more.pdf ...]")
        sys.exit(1)

    result = process_batch(sys.argv[1:])
    for profile in result.succeeded:
        print(profile.model_dump_json(indent=2))
    for failure in result.failed:
        print(f"FAILED: {failure}", file=sys.stderr)
