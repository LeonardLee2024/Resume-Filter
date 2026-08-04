from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator


class SkillCategory(str, Enum):
    TECHNICAL = "technical"
    SOFT = "soft"
    CERTIFICATION = "certification"
    LANGUAGE = "language"


class ContactInfo(BaseModel):
    full_name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    location: Optional[str] = None
    linkedin_url: Optional[str] = None
    portfolio_url: Optional[str] = None

    @field_validator("phone")
    @classmethod
    def strip_phone(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return v.strip()


class Skill(BaseModel):
    name: str
    category: SkillCategory = SkillCategory.TECHNICAL
    years_experience: Optional[float] = None


class WorkExperience(BaseModel):
    company: str
    title: str
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    is_current: bool = False
    location: Optional[str] = None
    responsibilities: list[str] = Field(default_factory=list)

    @property
    def duration_months(self) -> Optional[int]:
        if not self.start_date:
            return None
        end = self.end_date or date.today()
        return (end.year - self.start_date.year) * 12 + (
            end.month - self.start_date.month
        )


class Education(BaseModel):
    institution: str
    degree: Optional[str] = None
    field_of_study: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    gpa: Optional[float] = None


class CandidateProfile(BaseModel):

    source_file: str
    contact: ContactInfo = Field(default_factory=ContactInfo)
    total_years_experience: Optional[float] = None
    current_title: Optional[str] = None
    most_recent_company: Optional[str] = None

    skills: list[Skill] = Field(default_factory=list)
    work_history: list[WorkExperience] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
 
    ai_summary: Optional[str] = Field(
        default=None,
        description="2-4 sentence AI-generated overview of the candidate.",
    )
    keywords: list[str] = Field(
        default_factory=list,
        description="Searchable tags derived from the resume (roles, tech, domains).",
    )

    extraction_method: str = Field(
        default="text",
        description="'text' (native PDF text layer) or 'ocr' (image-based fallback).",
    )
    extraction_warnings: list[str] = Field(default_factory=list)

    def to_search_document(self) -> dict:
        """Flat dict suited for indexing in a search engine (e.g. Elasticsearch)."""
        return {
            "source_file": self.source_file,
            "name": self.contact.full_name,
            "current_title": self.current_title,
            "years_experience": self.total_years_experience,
            "skills": [s.name for s in self.skills],
            "companies": [w.company for w in self.work_history],
            "keywords": self.keywords,
            "summary": self.ai_summary,
        }
