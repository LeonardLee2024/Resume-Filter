from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from importlib.resources import path
from pathlib import Path

import fitz

class PdfStatus (str, Enum):
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
    
    if not path.exists() or not path.stat().st_size == 0:
        return ValidationResult(PdfStatus.EMPTY, message="File is missing or empty")
    
    if path.suffix.lower() != ".pdf":
        return ValidationResult(PdfStatus.NOT_A_PDF, message=f"Expected .pdf, got {path.suffix!r}")
    
    try:
        doc = fitz.open(str(path))
    except fitz.FileDataError as e:
        return ValidationResult(PdfStatus.CORRUPT, message=f"Unreadable PDF: {e}")
    except Exception as e: 
        return ValidationResult(PdfStatus.CORRUPT, message=f"Failed to open PDF: {e}")

