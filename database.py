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
def getconnection(db_path: Optional[Path] = None) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()    