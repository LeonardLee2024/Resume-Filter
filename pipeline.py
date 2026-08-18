from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from database import init_db, upsert_candidate
from resumeextraction import extract_text_native, extract_text_ocr
from parsing import parse_resume_text
from schema import CandidateProfile
from pdfvalidation import PdfStatus, validate_pdf

logger = logging.getLogger(__name__)

class ResumeProcessingError(Exception):
    def __init__(self, file_path: str, status: PdfStatus, message: str):
        self.file_path = file_path
        self.status = status
        self.message = message
        super().__init__(f"{file_path}: [{status.value}] {message}")


def process_resume(file_path: str | Path) -> CandidateProfile:
    file_path = str(file_path)
    logger.info("=== Starting: %s ===", file_path)
 
    logger.debug("Step 1/4: validating PDF...")
    validation = validate_pdf(file_path)
    logger.debug(
        "Validation result: status=%s pages=%d chars=%d message=%r",
        validation.status.value, validation.page_count,
        validation.text_char_count, validation.message,
    )
    if not validation.is_processable:
        logger.warning("Rejected at validation: %s (%s)", validation.status.value, validation.message)
        raise ResumeProcessingError(file_path, validation.status, validation.message)
 
    logger.debug("Step 2/4: extracting text (needs_ocr=%s)...", validation.needs_ocr)
    if validation.needs_ocr:
        extraction = extract_text_ocr(file_path)
    else:
        extraction = extract_text_native(file_path)
    logger.debug(
        "Extraction result: method=%s pages=%d total_chars=%d warnings=%s",
        extraction.method, len(extraction.pages), len(extraction.full_text),
        extraction.warnings,
    )
 
    logger.debug("Step 3/4: parsing text into structured profile...")
    profile = parse_resume_text(
        resume_text=extraction.full_text,
        source_file=file_path,
        extraction_method=extraction.method,
    )
    profile.extraction_warnings = extraction.warnings + profile.extraction_warnings
    logger.debug("Parsing warnings: %s", profile.extraction_warnings)
 
    logger.debug("Step 4/4: saving to database...")
    upsert_candidate(profile)
    logger.info("Saved to database: %s", file_path)
 
    logger.info("=== Finished: %s ===", file_path)
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
            logger.error("FAILED (%s): %s -> %s", e.status.value, e.file_path, e.message)
            failed.append({"file": e.file_path, "status": e.status.value, "message": e.message})
        except Exception as e:  # noqa: BLE001 - keep the batch going
            logger.exception("UNEXPECTED ERROR while processing %s", fp)
            failed.append({"file": str(fp), "status": "unexpected_error", "message": str(e)})
 
    return BatchResult(succeeded=succeeded, failed=failed)
 
 
if __name__ == "__main__":
    import sys
 
    logging.basicConfig(
        level=logging.DEBUG,  # change to logging.INFO for a shorter trace
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
 
    if len(sys.argv) < 2:
        print("Usage: python pipeline.py <resume.pdf> [more.pdf ...]")
        sys.exit(1)
 
    init_db()  # creates candidates.db in this folder if it doesn't exist yet
 
    result = process_batch(sys.argv[1:])
 
    print("\n----- RESULTS -----")
    for profile in result.succeeded:
        print(profile.model_dump_json(indent=2))
    for failure in result.failed:
        print(f"FAILED: {failure}", file=sys.stderr)
 
    if result.succeeded:
        print(f"\nSaved {len(result.succeeded)} candidate(s) to candidates.db")