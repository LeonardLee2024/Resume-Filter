"""
Local SQLite storage for parsed resumes — fully relational, keyed by
email, built for larger batches.

Six tables, same shape as before, but:
    - candidates.email is now the PRIMARY KEY (source_file is just a
      regular column now, since the same person's resume might get
      re-processed under a renamed file and should still merge into
      one candidate record).
    - search_candidates() accepts LISTS of search terms (any-of logic
      within each field) instead of one term at a time.
    - get_candidate() lets you pick which columns to pull back per
      child table, instead of always fetching every column.
    - bulk_upsert_candidates() saves many profiles in ONE transaction
      instead of one connection/commit per resume — this is the part
      that matters once you're processing hundreds or thousands of
      resumes, not just testing with one or two.

IMPORTANT: a candidate with no extracted email can't be stored, since
email is now the primary key. upsert_candidate() raises
MissingEmailError in that case; bulk_upsert_candidates() catches this
per-profile and reports it in the "skipped" list instead of failing
the whole batch.
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
    email                    TEXT PRIMARY KEY,
    source_file              TEXT,
    full_name                 TEXT,
    phone                      TEXT,
    location                   TEXT,
    current_title               TEXT,
    most_recent_company          TEXT,
    total_years_experience        REAL,
    ai_summary                     TEXT,
    extraction_method                TEXT,
    created_at                        TEXT,
    updated_at                         TEXT
);

CREATE TABLE IF NOT EXISTS skills (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_email          TEXT NOT NULL REFERENCES candidates(email) ON DELETE CASCADE,
    name                       TEXT NOT NULL,
    category                    TEXT,
    years_experience             REAL
);

CREATE TABLE IF NOT EXISTS work_history (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_email          TEXT NOT NULL REFERENCES candidates(email) ON DELETE CASCADE,
    company                    TEXT,
    title                       TEXT,
    start_date                   TEXT,
    end_date                      TEXT,
    is_current                     INTEGER,
    location                        TEXT,
    responsibilities                 TEXT
);

CREATE TABLE IF NOT EXISTS education (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_email          TEXT NOT NULL REFERENCES candidates(email) ON DELETE CASCADE,
    institution                 TEXT,
    degree                       TEXT,
    field_of_study                 TEXT,
    start_date                       TEXT,
    end_date                          TEXT,
    gpa                                 REAL
);

CREATE TABLE IF NOT EXISTS keywords (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_email          TEXT NOT NULL REFERENCES candidates(email) ON DELETE CASCADE,
    keyword                    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS extraction_warnings (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_email          TEXT NOT NULL REFERENCES candidates(email) ON DELETE CASCADE,
    warning                    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_candidates_source_file ON candidates(source_file);
CREATE INDEX IF NOT EXISTS idx_candidates_title ON candidates(current_title);
CREATE INDEX IF NOT EXISTS idx_skills_candidate ON skills(candidate_email);
CREATE INDEX IF NOT EXISTS idx_skills_name ON skills(name);
CREATE INDEX IF NOT EXISTS idx_work_candidate ON work_history(candidate_email);
CREATE INDEX IF NOT EXISTS idx_education_candidate ON education(candidate_email);
CREATE INDEX IF NOT EXISTS idx_keywords_candidate ON keywords(candidate_email);
CREATE INDEX IF NOT EXISTS idx_warnings_candidate ON extraction_warnings(candidate_email);
"""

CHILD_TABLES = ("skills", "work_history", "education", "keywords", "extraction_warnings")

# Whitelist of selectable columns per child table -- used to validate any
# caller-supplied `fields` argument before it goes anywhere near SQL.
ALL_COLUMNS = {
    "skills": ["name", "category", "years_experience"],
    "work_history": ["company", "title", "start_date", "end_date", "is_current", "location", "responsibilities"],
    "education": ["institution", "degree", "field_of_study", "start_date", "end_date", "gpa"],
    "keywords": ["keyword"],
    "extraction_warnings": ["warning"],
}


class MissingEmailError(Exception):
    """Raised when a candidate has no extracted email -- can't be stored, since email is the primary key."""
    def __init__(self, source_file: str):
        self.source_file = source_file
        super().__init__(f"Cannot store candidate from {source_file!r}: no email was extracted.")


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
        # WAL mode: lets reads happen concurrently with writes, and is
        # generally faster for write-heavy workloads like batch imports.
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)


def _upsert_one(conn: sqlite3.Connection, profile, now: str) -> str:
    """
    Does the actual insert/replace work for one profile on an already-open
    connection. Does NOT commit -- the caller controls the transaction, so
    this can be reused for both a single upsert and a large batch upsert
    sharing one transaction.
    """
    email = profile.contact.email
    if not email:
        raise MissingEmailError(profile.source_file)

    contact = profile.contact
    existing = conn.execute(
        "SELECT created_at FROM candidates WHERE email = ?", (email,)
    ).fetchone()
    created_at = existing["created_at"] if existing else now

    conn.execute(
        """
        INSERT INTO candidates (
            email, source_file, full_name, phone, location, current_title,
            most_recent_company, total_years_experience, ai_summary,
            extraction_method, created_at, updated_at
        ) VALUES (
            :email, :source_file, :full_name, :phone, :location, :current_title,
            :most_recent_company, :total_years_experience, :ai_summary,
            :extraction_method, :created_at, :updated_at
        )
        ON CONFLICT(email) DO UPDATE SET
            source_file = excluded.source_file,
            full_name = excluded.full_name,
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
            "email": email,
            "source_file": profile.source_file,
            "full_name": contact.full_name,
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

    for table in CHILD_TABLES:
        conn.execute(f"DELETE FROM {table} WHERE candidate_email = ?", (email,))

    conn.executemany(
        "INSERT INTO skills (candidate_email, name, category, years_experience) VALUES (?, ?, ?, ?)",
        [(email, s.name, s.category, s.years_experience) for s in profile.skills],
    )
    conn.executemany(
        """INSERT INTO work_history
           (candidate_email, company, title, start_date, end_date, is_current, location, responsibilities)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                email, w.company, w.title,
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
           (candidate_email, institution, degree, field_of_study, start_date, end_date, gpa)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                email, e.institution, e.degree, e.field_of_study,
                e.start_date.isoformat() if e.start_date else None,
                e.end_date.isoformat() if e.end_date else None,
                e.gpa,
            )
            for e in profile.education
        ],
    )
    conn.executemany(
        "INSERT INTO keywords (candidate_email, keyword) VALUES (?, ?)",
        [(email, k) for k in profile.keywords],
    )
    conn.executemany(
        "INSERT INTO extraction_warnings (candidate_email, warning) VALUES (?, ?)",
        [(email, w) for w in profile.extraction_warnings],
    )

    return email


def upsert_candidate(profile, db_path: str | Path = DEFAULT_DB_PATH) -> str:
    """Save a single candidate. Raises MissingEmailError if no email was extracted."""
    now = datetime.now(timezone.utc).isoformat()
    with get_connection(db_path) as conn:
        email = _upsert_one(conn, profile, now)
    logger.debug("Upserted candidate: %s", email)
    return email


def bulk_upsert_candidates(
    profiles: list, db_path: str | Path = DEFAULT_DB_PATH
) -> dict:
    """
    Save many candidates in ONE transaction (one connection, one commit).
    This is the one to use for batches -- opening/committing a fresh
    connection per resume is the single biggest cost at scale, and this
    avoids it entirely.

    Returns {"saved": [emails...], "skipped": [{"source_file", "reason"}]}
    -- profiles with no email are skipped individually rather than
    aborting the whole batch.
    """
    now = datetime.now(timezone.utc).isoformat()
    saved: list[str] = []
    skipped: list[dict] = []

    with get_connection(db_path) as conn:
        for profile in profiles:
            try:
                email = _upsert_one(conn, profile, now)
                saved.append(email)
            except MissingEmailError as e:
                logger.warning("Skipping (no email): %s", e.source_file)
                skipped.append({"source_file": e.source_file, "reason": "no email extracted"})

    logger.info("Bulk upsert: %d saved, %d skipped", len(saved), len(skipped))
    return {"saved": saved, "skipped": skipped}


def _validate_columns(table: str, columns: list[str]) -> list[str]:
    allowed = ALL_COLUMNS[table]
    invalid = [c for c in columns if c not in allowed]
    if invalid:
        raise ValueError(f"Invalid column(s) for {table}: {invalid}. Allowed: {allowed}")
    return columns


def get_candidate(
    email: str,
    db_path: str | Path = DEFAULT_DB_PATH,
    fields: Optional[dict[str, list[str]]] = None,
) -> Optional[dict]:
    """
    Reconstructs the full nested profile via joins. `fields` optionally
    limits which columns come back per child table, e.g.:

        get_candidate(email, fields={"education": ["gpa", "degree"]})

    only returns gpa and degree for education rows, instead of every
    column. Tables not mentioned in `fields` still return all columns.
    """
    fields = fields or {}
    with get_connection(db_path) as conn:
        candidate = conn.execute(
            "SELECT * FROM candidates WHERE email = ?", (email,)
        ).fetchone()
        if not candidate:
            return None

        result = dict(candidate)
        for table in CHILD_TABLES:
            cols = _validate_columns(table, fields.get(table, ALL_COLUMNS[table]))
            col_list = ", ".join(cols)
            key = "extraction_warnings" if table == "extraction_warnings" else table
            rows = conn.execute(
                f"SELECT {col_list} FROM {table} WHERE candidate_email = ?", (email,)
            ).fetchall()
            if table == "keywords":
                result["keywords"] = [r["keyword"] for r in rows]
            elif table == "extraction_warnings":
                result["extraction_warnings"] = [r["warning"] for r in rows]
            else:
                result[table] = [dict(r) for r in rows]
        return result


def list_candidates(
    limit: int = 50, offset: int = 0, db_path: str | Path = DEFAULT_DB_PATH
) -> list[dict]:
    """Lightweight list -- candidate rows only, no child-table joins. Paginated for large tables."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM candidates ORDER BY updated_at DESC LIMIT ? OFFSET ?", (limit, offset)
        ).fetchall()
        return [dict(r) for r in rows]


def search_candidates(
    skills: Optional[str | list[str]] = None,
    titles: Optional[str | list[str]] = None,
    min_years_experience: Optional[float] = None,
    limit: int = 50,
    offset: int = 0,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> list[dict]:
    """
    Relational search with multi-term support. Accepts a single string
    or a list of strings for `skills` / `titles` -- matching ANY term
    in that list (e.g. skills=["Python", "Go"] finds candidates who know
    Python OR Go). Multiple filter types (skills AND titles AND
    min_years_experience) combine with AND. Paginated via limit/offset.
    """
    if isinstance(skills, str):
        skills = [skills]
    if isinstance(titles, str):
        titles = [titles]

    joins = []
    clauses = []
    params: list = []

    base = "SELECT DISTINCT c.* FROM candidates c"

    if skills:
        joins.append("JOIN skills sk ON sk.candidate_email = c.email")
        skill_clause = " OR ".join(["sk.name LIKE ?"] * len(skills))
        clauses.append(f"({skill_clause})")
        params.extend(f"%{s}%" for s in skills)

    if titles:
        title_clause = " OR ".join(["c.current_title LIKE ?"] * len(titles))
        clauses.append(f"({title_clause})")
        params.extend(f"%{t}%" for t in titles)

    if min_years_experience is not None:
        clauses.append("c.total_years_experience >= ?")
        params.append(min_years_experience)

    query = base + " " + " ".join(joins)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY c.updated_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    with get_connection(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def delete_candidate(email: str, db_path: str | Path = DEFAULT_DB_PATH) -> bool:
    """Deletes the candidate row; ON DELETE CASCADE removes all their child rows automatically."""
    with get_connection(db_path) as conn:
        cursor = conn.execute("DELETE FROM candidates WHERE email = ?", (email,))
        return cursor.rowcount > 0


def count_candidates(db_path: str | Path = DEFAULT_DB_PATH) -> int:
    with get_connection(db_path) as conn:
        return conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]