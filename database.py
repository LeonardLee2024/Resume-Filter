from __future__ import annotations

import json
import logging
import sqlite3

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).parent / "candidates.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
    source_file             TEXT PRIMARY KEY,
    full_name                TEXT,
    email                     TEXT,
    phone                     TEXT,
    location                  TEXT,
    current_title             TEXT,
    most_recent_company       TEXT,
    total_years_experience    REAL,
    ai_summary                TEXT,
    extraction_method         TEXT,
    skills_json                TEXT,   -- JSON array of {name, category, years_experience}
    work_history_json          TEXT,   -- JSON array of work experience objects
    education_json              TEXT,   -- JSON array of education objects
    keywords_json                TEXT,   -- JSON array of strings
    extraction_warnings_json      TEXT,   -- JSON array of strings
    raw_profile_json               TEXT,   -- full CandidateProfile as JSON, for fidelity
    created_at                       TEXT,
    updated_at                       TEXT
);
 
CREATE INDEX IF NOT EXISTS idx_candidates_email ON candidates(email);
CREATE INDEX IF NOT EXISTS idx_candidates_title ON candidates(current_title);
"""

@contextmanager
def get_connection(db_path: Optional[Path] = None) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()    


def init_db(db_path: str | Path = DEFAULT_DB_PATH) -> None:
    logger.info("Initializing database at %s", db_path)
    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA)
 
 
def upsert_candidate(profile, db_path: str | Path = DEFAULT_DB_PATH) -> None:
    """
    Insert a new candidate row, or overwrite the existing one if
    source_file already exists (re-processing the same resume file
    replaces its prior record rather than duplicating it).
    """
    now = datetime.now(timezone.utc).isoformat()
    contact = profile.contact
 
    row = {
        "source_file": profile.source_file,
        "full_name": contact.full_name,
        "email": contact.email,
        "phone": contact.phone,
        "location": contact.location,
        "current_title": profile.current_title,
        "most_recent_company": profile.most_recent_company,
        "total_years_experience": profile.total_years_experience,
        "ai_summary": profile.ai_summary,
        "extraction_method": profile.extraction_method,
        "skills_json": json.dumps([s.model_dump(mode="json") for s in profile.skills]),
        "work_history_json": json.dumps([w.model_dump(mode="json") for w in profile.work_history]),
        "education_json": json.dumps([e.model_dump(mode="json") for e in profile.education]),
        "keywords_json": json.dumps(profile.keywords),
        "extraction_warnings_json": json.dumps(profile.extraction_warnings),
        "raw_profile_json": profile.model_dump_json(),
        "updated_at": now,
    }
 
    with get_connection(db_path) as conn:
        existing = conn.execute(
            "SELECT created_at FROM candidates WHERE source_file = ?", (row["source_file"],)
        ).fetchone()
        row["created_at"] = existing["created_at"] if existing else now
 
        columns = ", ".join(row.keys())
        placeholders = ", ".join(f":{k}" for k in row.keys())
        update_clause = ", ".join(f"{k} = excluded.{k}" for k in row.keys() if k != "source_file")
 
        conn.execute(
            f"""
            INSERT INTO candidates ({columns}) VALUES ({placeholders})
            ON CONFLICT(source_file) DO UPDATE SET {update_clause}
            """,
            row,
        )
    logger.debug("Upserted candidate: %s", profile.source_file)
 
 
def get_candidate(source_file: str, db_path: str | Path = DEFAULT_DB_PATH) -> Optional[dict]:
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM candidates WHERE source_file = ?", (source_file,)
        ).fetchone()
        return _row_to_dict(row) if row else None
 
 
def list_candidates(
    limit: int = 50, db_path: str | Path = DEFAULT_DB_PATH
) -> list[dict]:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM candidates ORDER BY updated_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
 
 
def search_candidates(
    skill: Optional[str] = None,
    title: Optional[str] = None,
    min_years_experience: Optional[float] = None,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> list[dict]:
    """
    Simple filtered search. `skill` matches against the skills JSON
    text (substring match — good enough for a first pass; move to a
    normalized skills table or full-text index if this needs to scale).
    """
    clauses = []
    params: dict = {}
 
    if skill:
        clauses.append("skills_json LIKE :skill")
        params["skill"] = f"%{skill}%"
    if title:
        clauses.append("current_title LIKE :title")
        params["title"] = f"%{title}%"
    if min_years_experience is not None:
        clauses.append("total_years_experience >= :min_years")
        params["min_years"] = min_years_experience
 
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
 
    with get_connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT * FROM candidates {where} ORDER BY updated_at DESC", params
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
 
 
def delete_candidate(source_file: str, db_path: str | Path = DEFAULT_DB_PATH) -> bool:
    with get_connection(db_path) as conn:
        cursor = conn.execute("DELETE FROM candidates WHERE source_file = ?", (source_file,))
        return cursor.rowcount > 0
 
 
def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for json_field in (
        "skills_json", "work_history_json", "education_json",
        "keywords_json", "extraction_warnings_json",
    ):
        key = json_field.replace("_json", "")
        d[key] = json.loads(d.pop(json_field))
    d["raw_profile"] = json.loads(d.pop("raw_profile_json"))
    return d