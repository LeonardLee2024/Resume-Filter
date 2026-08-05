from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
import logging
from pathlib import Path

import fitz

logger = logging.getLogger(__name__)


class PdfStatus(str, Enum):
    OK_TEXT = "ok_text"
    OK_SCANNED = "ok_scanned"
    ENCRYPTED = "encrypted"
    CORRUPT = "corrupt"
    EMPTY = "empty"
    NOT_A_PDF = "not_a_pdf"


@dataclass
class ValidationResult:
    status: PdfStatus
    page_count: int = 0
    text_char_count: int = 0
    message: str = ""

    @property
    def is_processable(self) -> bool:
        return self.status in {PdfStatus.OK_TEXT, PdfStatus.OK_SCANNED}

    @property
    def needs_ocr(self) -> bool:
        return self.status == PdfStatus.OK_SCANNED


MIN_CHARS_PER_PAGE = 40


def validate_pdf(file_path: Path) -> ValidationResult:
    path = Path(file_path)
    logger.debug("Checking existence/size of %s", path)

    if not path.exists() or path.stat().st_size == 0:
        logger.debug("-> EMPTY (missing or zero bytes)")
        return ValidationResult(PdfStatus.EMPTY, message="File is missing or empty")

    if path.suffix.lower() != ".pdf":
        logger.debug("-> NOT_A_PDF (suffix=%r)", path.suffix)
        return ValidationResult(PdfStatus.NOT_A_PDF, message=f"Expected .pdf, got {path.suffix!r}")

    logger.debug("Attempting to open with fitz...")
    try:
        doc = fitz.open(str(path))
    except fitz.FileDataError as e:
        logger.debug("-> CORRUPT (FileDataError: %s)", e)
        return ValidationResult(PdfStatus.CORRUPT, message=f"Unreadable PDF: {e}")
    except Exception as e:
        logger.debug("-> CORRUPT (unexpected open failure: %s)", e)
        return ValidationResult(PdfStatus.CORRUPT, message=f"Failed to open PDF: {e}")

    try:
        logger.debug("Opened OK. is_encrypted=%s page_count=%s", doc.is_encrypted, doc.page_count)
        if doc.is_encrypted:
            if not doc.authenticate(""):
                logger.debug("-> ENCRYPTED (empty-password auth failed)")
                return ValidationResult(
                    PdfStatus.ENCRYPTED,
                    page_count=doc.page_count,
                    message="PDF is password protected.",
                )

        if doc.page_count == 0:
            logger.debug("-> EMPTY (zero pages)")
            return ValidationResult(PdfStatus.EMPTY, message="PDF has zero pages.")

        total_chars = 0
        for page in doc:
            total_chars += len(page.get_text("text").strip())

        avg_chars_per_page = total_chars / doc.page_count
        logger.debug("total_chars=%d avg_chars_per_page=%.1f", total_chars, avg_chars_per_page)

        if avg_chars_per_page < MIN_CHARS_PER_PAGE:
            logger.debug("-> OK_SCANNED (below MIN_CHARS_PER_PAGE=%d)", MIN_CHARS_PER_PAGE)
            return ValidationResult(
                PdfStatus.OK_SCANNED,
                page_count=doc.page_count,
                text_char_count=total_chars,
                message="No meaningful text layer detected; route to OCR.",
            )

        logger.debug("-> OK_TEXT")
        return ValidationResult(
            PdfStatus.OK_TEXT,
            page_count=doc.page_count,
            text_char_count=total_chars,
            message="Text layer present.",
        )
    finally:
        doc.close()