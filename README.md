requirements.txt includes all libraries needed to run the code.  Note: does not include openai module for parsing.py
Run "pip install -r requirements.txt" in Windows Powershell

To use, run in terminal: "python pipeline.py {resume name}", or alternatively using script:
from pipeline import process_resume, process_batch

profile = process_resume("candidate.pdf")<br>
print(profile.ai_summary)<br>
print([s.name for s in profile.skills])<br>

result = process_batch(["a.pdf", "b.pdf", "c.pdf"])<br>
print(f"{len(result.succeeded)} parsed, {len(result.failed)} failed")<br>
for f in result.failed:<br>
    print(f)<br>

pdfvalidation.py — opens the PDF with PyMuPDF, rejects corrupt/empty/ non-PDF files, detects password-protected files, and flags files with no real text layer (OK_SCANNED) so they route to OCR instead of silently returning empty text <br>
resumeextraction.py — Native text: PyMuPDF block extraction, with column-aware reordering so 2-3 column resumes don't interleave. Tables (e.g. a skills grid) are pulled separately with pdfplumber since flat text extraction destroys table structure <br>
Scanned PDFs: rendered to images at 300 DPI and sent to OCR (Tesseract by default; able to swap ocr_page_image() for a cloud OCR call — AWS Textract, Google Document AI, Azure) <br>
parsing.py — sends the raw text to Claude with a constrained JSON schema prompt to produce structured fields (contact info, work history, education, skills) plus the AI summary and search keywords. Falls back to a partially-empty profile (with a warning recorded) if the API call or JSON parsing fails, rather than dropping the candidate. <br>
schema.py — the CandidateProfile Pydantic model everything validates against. to_search_document() gives a flat dict ready <br>
pipeline.py — orchestrates the above. process_batch() keeps going past individual file failures and returns successes/failures separately <br>