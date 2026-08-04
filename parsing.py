from __future__ import annotations

import json
import os

from schema import CandidateProfile

SYSTEM_PROMPT = """You extract structured candidate data from resume text.
Return ONLY a single JSON object (no markdown fences, no commentary) matching
this shape:

{
  "contact": {"full_name": str|null, "email": str|null, "phone": str|null,
              "location": str|null, "linkedin_url": str|null, "portfolio_url": str|null},
  "total_years_experience": number|null,
  "current_title": str|null,
  "most_recent_company": str|null,
  "skills": [{"name": str, "category": "technical"|"soft"|"certification"|"language",
              "years_experience": number|null}],
  "work_history": [{"company": str, "title": str, "start_date": "YYYY-MM-DD"|null,
                     "end_date": "YYYY-MM-DD"|null, "is_current": bool,
                     "location": str|null, "responsibilities": [str]}],
  "education": [{"institution": str, "degree": str|null, "field_of_study": str|null,
                 "start_date": "YYYY-MM-DD"|null, "end_date": "YYYY-MM-DD"|null,
                 "gpa": number|null}],
  "ai_summary": str,
  "keywords": [str]
}

Rules:
- ai_summary must be 2-4 sentences, factual, no fluff.
- keywords: 5-15 searchable tags (roles, core technologies, domains).
- Use null for anything not present in the text. Do not invent data.
- Dates: if only a year is given, use YYYY-01-01. If "Present"/"Current", set
  is_current true and end_date null.
"""


def _call_openai(resume_text: str, model: str = "gpt-4o-mini") -> dict:
    """
    Calls the OpenAI API to structure the resume text. Requires
    OPENAI_API_KEY in the environment. Uses JSON mode so the response
    is guaranteed to be a parseable JSON object.
    """
    import openai

    client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    response = client.chat.completions.create(
        model=model,
        max_tokens=2000,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": resume_text[:15000]},
        ],
    )
    raw = response.choices[0].message.content
    return json.loads(raw)


def parse_resume_text(
    resume_text: str, source_file: str, extraction_method: str = "text"
) -> CandidateProfile:
    warnings: list[str] = []
    parsed: dict = {}

    if not resume_text.strip():
        warnings.append("No text available to parse (empty extraction).")
    else:
        try:
            parsed = _call_openai(resume_text)
        except KeyError:
            warnings.append("OPENAI_API_KEY not set; skipped LLM structuring.")
        except json.JSONDecodeError as e:
            warnings.append(f"LLM returned invalid JSON, discarded: {e}")
        except Exception as e:  # noqa: BLE001 - never let one bad resume kill a batch
            warnings.append(f"LLM structuring failed: {e}")

    profile = CandidateProfile(
        source_file=source_file,
        extraction_method=extraction_method,
        extraction_warnings=warnings,
        **{k: v for k, v in parsed.items() if k in CandidateProfile.model_fields},
    )
    return profile