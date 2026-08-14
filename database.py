"""
Local SQLite storage for parsed resumes — fully normalized (relational)
version.

Six tables instead of one:
    candidates            - one row per resume (the "parent")
    skills                - one row per skill,        linked to a candidate
    work_history           - one row per job,           linked to a candidate
    education                - one row per degree,        linked to a candidate
    keywords                  - one row per search tag,     linked to a candidate
    extraction_warnings         - one row per pipeline warning, linked to a candidate

Every child table has a `candidate_source_file` column that's a
foreign key back to `candidates.source_file`. That link is what makes
this relational: skills aren't text crammed into a cell, they're real
rows the database can filter, count, and JOIN on directly.

Usage:
    from database import init_db, upsert_candidate, get_candidate, list_candidates

    init_db()
    upsert_candidate(profile)
    get_candidate("resume.pdf")          # reconstructs the full nested profile via joins
    search_candidates(skill="python")    # real WHERE + JOIN, not text search
"""

from __future__ import annotations

import logging
import sqlite3

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).parent / "candidates.db"

SCHEMA = """
PRAGMA foreign_keys = ON;

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
    created_at                 TEXT,
    updated_at                  TEXT
);

CREATE TABLE IF NOT EXISTS skills (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_source_file    TEXT NOT NULL REFERENCES candidates(source_file) ON DELETE CASCADE,
    name                       TEXT NOT NULL,
    category                    TEXT,
    years_experience             REAL
);

CREATE TABLE IF NOT EXISTS work_history (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_source_file    TEXT NOT NULL REFERENCES candidates(source_file) ON DELETE CASCADE,
    company                    TEXT,
    title                       TEXT,
    start_date                   TEXT,
    end_date                      TEXT,
    is_current                     INTEGER,
    location                        TEXT,
    responsibilities                 TEXT  -- bullet points joined with newlines
);

CREATE TABLE IF NOT EXISTS education (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_source_file    TEXT NOT NULL REFERENCES candidates(source_file) ON DELETE CASCADE,
    institution                 TEXT,
    degree                       TEXT,
    field_of_study                 TEXT,
    start_date                       TEXT,
    end_date                          TEXT,
    gpa                                 REAL
);

CREATE TABLE IF NOT EXISTS keywords (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_source_file    TEXT NOT NULL REFERENCES candidates(source_file) ON DELETE CASCADE,
    keyword                    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS extraction_warnings (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_source_file    TEXT NOT NULL REFERENCES candidates(source_file) ON DELETE CASCADE,
    warning                    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_candidates_email ON candidates(email);
CREATE INDEX IF NOT EXISTS idx_candidates_title ON candidates(current_title);
CREATE INDEX IF NOT EXISTS idx_skills_candidate ON skills(candidate_source_file);
CREATE INDEX IF NOT EXISTS idx_skills_name ON skills(name);
CREATE INDEX IF NOT EXISTS idx_work_candidate ON work_history(candidate_source_file);
CREATE INDEX IF NOT EXISTS idx_education_candidate ON education(candidate_source_file);
CREATE INDEX IF NOT EXISTS idx_keywords_candidate ON keywords(candidate_source_file);
CREATE INDEX IF NOT EXISTS idx_warnings_candidate ON extraction_warnings(candidate_source_file);
"""

CHILD_TABLES = ("skills", "work_history", "education", "keywords", "extraction_warnings")


@contextmanager
def get_connection(db_path: str | Path = DEFAULT_DB_PATH) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: str | Path = DEFAULT_DB_PATH) -> None:
    logger.info("Initializing relational database at %s", db_path)
    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA)


def upsert_candidate(profile, db_path: str | Path = DEFAULT_DB_PATH) -> None:
    """
    Insert or fully replace a candidate and all of their child rows.
    Re-processing the same resume file wipes and reinserts its skills/
    work_history/education/keywords/warnings, so you never end up with
    stale or duplicate child rows from an earlier run.
    """
    now = datetime.now(timezone.utc).isoformat()
    contact = profile.contact
    source_file = profile.source_file

    with get_connection(db_path) as conn:
        existing = conn.execute(
            "SELECT created_at FROM candidates WHERE source_file = ?", (source_file,)
        ).fetchone()
        created_at = existing["created_at"] if existing else now

        conn.execute(
            """
            INSERT INTO candidates (
                source_file, full_name, email, phone, location, current_title,
                most_recent_company, total_years_experience, ai_summary,
                extraction_method, created_at, updated_at
            ) VALUES (
                :source_file, :full_name, :email, :phone, :location, :current_title,
                :most_recent_company, :total_years_experience, :ai_summary,
                :extraction_method, :created_at, :updated_at
            )
            ON CONFLICT(source_file) DO UPDATE SET
                full_name = excluded.full_name,
                email = excluded.email,
                phone = excluded.phone,
                location = excluded.location,
                current_title = excluded.current_title,
                most_recent_company = excluded.most_recent_company,
                total_years_experience = excluded.total_years_experience,
                ai_summary = excluded.ai_summary,
                extraction_method = excluded.extraction_method,
                updated_at = excluded.updated_at
            """,
            {
                "source_file": source_file,
                "full_name": contact.full_name,
                "email": contact.email,
                "phone": contact.phone,
                "location": contact.location,
                "current_title": profile.current_title,
                "most_recent_company": profile.most_recent_company,
                "total_years_experience": profile.total_years_experience,
                "ai_summary": profile.ai_summary,
                "extraction_method": profile.extraction_method,
                "created_at": created_at,
                "updated_at": now,
            },
        )

        # Replace child rows: delete old ones for this candidate, insert fresh.
        for table in CHILD_TABLES:
            conn.execute(f"DELETE FROM {table} WHERE candidate_source_file = ?", (source_file,))

        conn.executemany(
            "INSERT INTO skills (candidate_source_file, name, category, years_experience) "
            "VALUES (?, ?, ?, ?)",
            [(source_file, s.name, s.category, s.years_experience) for s in profile.skills],
        )

        conn.executemany(
            """INSERT INTO work_history
               (candidate_source_file, company, title, start_date, end_date,
                is_current, location, responsibilities)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    source_file, w.company, w.title,
                    w.start_date.isoformat() if w.start_date else None,
                    w.end_date.isoformat() if w.end_date else None,
                    int(w.is_current), w.location,
                    "\n".join(w.responsibilities),
                )
                for w in profile.work_history
            ],
        )

        conn.executemany(
            """INSERT INTO education
               (candidate_source_file, institution, degree, field_of_study,
                start_date, end_date, gpa)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    source_file, e.institution, e.degree, e.field_of_study,
                    e.start_date.isoformat() if e.start_date else None,
                    e.end_date.isoformat() if e.end_date else None,
                    e.gpa,
                )
                for e in profile.education
            ],
        )

        conn.executemany(
            "INSERT INTO keywords (candidate_source_file, keyword) VALUES (?, ?)",
            [(source_file, k) for k in profile.keywords],
        )

        conn.executemany(
            "INSERT INTO extraction_warnings (candidate_source_file, warning) VALUES (?, ?)",
            [(source_file, w) for w in profile.extraction_warnings],
        )

    logger.debug("Upserted candidate + child rows: %s", source_file)


def get_candidate(source_file: str, db_path: str | Path = DEFAULT_DB_PATH) -> Optional[dict]:
    """Reconstructs the full nested profile by joining across all child tables."""
    with get_connection(db_path) as conn:
        candidate = conn.execute(
            "SELECT * FROM candidates WHERE source_file = ?", (source_file,)
        ).fetchone()
        if not candidate:
            return None

        result = dict(candidate)
        result["skills"] = [
            dict(r) for r in conn.execute(
                "SELECT name, category, years_experience FROM skills WHERE candidate_source_file = ?",
                (source_file,),
            ).fetchall()
        ]
        result["work_history"] = [
            dict(r) for r in conn.execute(
                "SELECT company, title, start_date, end_date, is_current, location, responsibilities "
                "FROM work_history WHERE candidate_source_file = ?",
                (source_file,),
            ).fetchall()
        ]
        result["education"] = [
            dict(r) for r in conn.execute(
                "SELECT institution, degree, field_of_study, start_date, end_date, gpa "
                "FROM education WHERE candidate_source_file = ?",
                (source_file,),
            ).fetchall()
        ]
        result["keywords"] = [
            r["keyword"] for r in conn.execute(
                "SELECT keyword FROM keywords WHERE candidate_source_file = ?", (source_file,)
            ).fetchall()
        ]
        result["extraction_warnings"] = [
            r["warning"] for r in conn.execute(
                "SELECT warning FROM extraction_warnings WHERE candidate_source_file = ?",
                (source_file,),
            ).fetchall()
        ]
        return result


def list_candidates(limit: int = 50, db_path: str | Path = DEFAULT_DB_PATH) -> list[dict]:
    """Lightweight list — candidate rows only, no child-table joins (fast for browsing)."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM candidates ORDER BY updated_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def search_candidates(
    skill: Optional[str] = None,
    title: Optional[str] = None,
    min_years_experience: Optional[float] = None,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> list[dict]:
    """
    Real relational search: JOINs against the skills table instead of
    text-matching a JSON blob. Returns distinct candidates matching all
    given filters.
    """
    joins = []
    clauses = []
    params: dict = {}

    base = "SELECT DISTINCT c.* FROM candidates c"

    if skill:
        joins.append("JOIN skills sk ON sk.candidate_source_file = c.source_file")
        clauses.append("sk.name LIKE :skill")
        params["skill"] = f"%{skill}%"
    if title:
        clauses.append("c.current_title LIKE :title")
        params["title"] = f"%{title}%"
    if min_years_experience is not None:
        clauses.append("c.total_years_experience >= :min_years")
        params["min_years"] = min_years_experience

    query = base + " " + " ".join(joins)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY c.updated_at DESC"

    with get_connection(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def delete_candidate(source_file: str, db_path: str | Path = DEFAULT_DB_PATH) -> bool:
    """Deletes the candidate row; ON DELETE CASCADE removes all their child rows automatically."""
    with get_connection(db_path) as conn:
        cursor = conn.execute("DELETE FROM candidates WHERE source_file = ?", (source_file,))
        return cursor.rowcount > 0