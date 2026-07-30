from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import fitz  
import pdfplumber

@dataclass
class PageExtraction:
    page_number: int
    text: str
    tables: list[list[list[Optional[str]]]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.text = (self.text or "").strip()
        if self.tables is None:
            self.tables = []

    def to_dict(self) -> dict[str, object]:
        return {
            "page_number": self.page_number,
            "text": self.text,
            "tables": self.tables,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "PageExtraction":
        return cls(
            page_number=int(data.get("page_number", 0)),
            text=str(data.get("text", "")),
            tables=list(data.get("tables", [])),
        )


@dataclass
class ExtractionResult:
    source_file: str
    method: str  #"text" or "ocr"
    pages: list[PageExtraction]
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.source_file = str(self.source_file or "")
        self.method = str(self.method or "")
        self.warnings = [str(w) for w in (self.warnings or []) if str(w).strip()]
        self.pages = [
            page if isinstance(page, PageExtraction) else PageExtraction.from_dict(page)
            for page in (self.pages or [])
        ]

    @property
    def full_text(self) -> str: 
        return "\n\n".join(p.text for p in self.pages if p.text.strip())

    def to_dict(self) -> dict[str, object]:
        return {
            "source_file": self.source_file,
            "method": self.method,
            "pages": [page.to_dict() for page in self.pages],
            "warnings": self.warnings,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "ExtractionResult":
        pages = data.get("pages", [])
        return cls(
            source_file=str(data.get("source_file", "")),
            method=str(data.get("method", "")),
            pages=[
                PageExtraction.from_dict(page)
                if isinstance(page, dict)
                else page
                for page in pages
            ],
            warnings=[str(w) for w in data.get("warnings", [])],
        )

#Text Extraction 
def _order_blocks_by_column(blocks: list[tuple], page_width: float) -> list[tuple]:
    if not blocks:
        return blocks

    midpoints = sorted(set(round((b[0] + b[2]) / 2) for b in blocks))
    gap_threshold = page_width * 0.08  
    columns: list[list[float]] = [[midpoints[0]]]
    for m in midpoints[1:]:
        if m - columns[-1][-1] > gap_threshold:
            columns.append([m])
        else:
            columns[-1].append(m)

    column_ranges = [(min(c), max(c)) for c in columns]

    def column_index(block) -> int:
        mid = (block[0] + block[2]) / 2
        best_i, best_dist = 0, float("inf")
        for i, (lo, hi) in enumerate(column_ranges):
            dist = 0 if lo <= mid <= hi else min(abs(mid - lo), abs(mid - hi))
            if dist < best_dist:
                best_i, best_dist = i, dist
        return best_i

    ordered: list[tuple] = []
    for col_i in range(len(column_ranges)):
        col_blocks = [b for b in blocks if column_index(b) == col_i]
        col_blocks.sort(key=lambda b: b[1])  # top-to-bottom by y0
        ordered.extend(col_blocks)
    return ordered


def extract_text_native(file_path: str | Path) -> ExtractionResult:
    path = Path(file_path)
    doc = fitz.open(str(path))
    pages: list[PageExtraction] = []
    warnings: list[str] = []

    try:
        for page in doc:
            blocks = page.get_text("blocks") 
            ordered = _order_blocks_by_column(blocks, page.rect.width)
            text = "\n".join(b[4].strip() for b in ordered if b[4].strip())
            pages.append(PageExtraction(page_number=page.number + 1, text=text))
    finally:
        doc.close()

    tables_by_page = _extract_tables(path)
    for p in pages:
        p.tables = tables_by_page.get(p.page_number, [])
        if p.tables:
            warnings.append(f"Page {p.page_number}: {len(p.tables)} table(s) detected.")

    return ExtractionResult(
        source_file=str(path), method="text", pages=pages, warnings=warnings
    )


def _extract_tables(path: Path) -> dict[int, list]:
    tables_by_page: dict[int, list] = {}
    with pdfplumber.open(str(path)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            tables = page.extract_tables()
            if tables:
                tables_by_page[i] = tables
    return tables_by_page

#OCR Fallback
def ocr_page_image(pix: "fitz.Pixmap") -> str:
    import pytesseract
    from PIL import Image
    import io

    img = Image.open(io.BytesIO(pix.tobytes("png")))
    return pytesseract.image_to_string(img)

def extract_text_ocr(file_path: str | Path, dpi: int = 300) -> ExtractionResult:
    path = Path(file_path)
    doc = fitz.open(str(path))
    pages: list[PageExtraction] = []
    warnings: list[str] = [
        "Extracted via OCR fallback; verify field accuracy, especially dates and numbers."
    ]

    try:
        zoom = dpi / 72
        matrix = fitz.Matrix(zoom, zoom)
        for page in doc:
            pix = page.get_pixmap(matrix=matrix)
            text = ocr_page_image(pix)
            pages.append(PageExtraction(page_number=page.number + 1, text=text))
    finally:
        doc.close()
        
    return ExtractionResult(
        source_file=str(path), method="ocr", pages=pages, warnings=warnings
    )
