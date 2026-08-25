"""Pydantic schemas for the curriculum pipeline.

The Profile schema is generic across all templates. Each template declares
its own field-to-placeholder mapping (see template_schema.py). TailoredProfile
extends Profile with per-item relevance scoring and an inclusion flag so the
iteration loop can drop lowest-relevance items when content overflows.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


# ── Base profile models ──────────────────────────────────────────────────────


class PersonalInfo(BaseModel):
    name: str
    first_name: str | None = None
    last_name: str | None = None
    title: str | None = None
    photo_path: str | None = None
    citizenship: str | None = None
    family_status: str | None = None
    year_of_birth: int | None = None


class ContactInfo(BaseModel):
    email: str | None = None
    phone: str | None = None
    github: str | None = None
    linkedin: str | None = None
    website: str | None = None
    address: str | None = None


class ExperienceItem(BaseModel):
    title: str
    company: str
    location: str | None = None
    start: str
    end: str
    bullets: list[str] = Field(default_factory=list)


class EducationItem(BaseModel):
    degree: str
    field: str | None = None
    institution: str
    start: str
    end: str
    bullets: list[str] = Field(default_factory=list)


class SkillItem(BaseModel):
    name: str
    category: str | None = None
    proficiency: Literal["beginner", "intermediate", "advanced", "expert"] | None = None


class LanguageItem(BaseModel):
    name: str
    level: str  # e.g. "native", "C1", "B2"


class Profile(BaseModel):
    personal: PersonalInfo
    contact: ContactInfo = Field(default_factory=ContactInfo)
    profile_text: str = ""
    experience: list[ExperienceItem] = Field(default_factory=list)
    education: list[EducationItem] = Field(default_factory=list)
    additional_education: list[EducationItem] = Field(default_factory=list)
    skills: list[SkillItem] = Field(default_factory=list)
    languages: list[LanguageItem] = Field(default_factory=list)
    hobbies: list[str] = Field(default_factory=list)

    @field_validator("experience", "education", "additional_education")
    @classmethod
    def _non_empty_when_present(cls, v: list) -> list:
        return v or []


# ── Requirements (extracted from raw job description) ───────────────────────


class Requirements(BaseModel):
    role_title: str = ""
    company: str = ""
    seniority: Literal[
        "intern", "junior", "mid", "senior", "staff", "principal", "lead", "manager", ""
    ] = ""
    required_skills: list[str] = Field(default_factory=list)
    nice_skills: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    years_experience_min: int | None = None
    domain: str = ""
    raw_text: str = ""

    def all_skills(self) -> list[str]:
        return [*self.required_skills, *self.nice_skills, *self.keywords]


# ── Tailored profile (Profile + relevance scoring + inclusion flag) ─────────


class TailoredExperienceItem(ExperienceItem):
    relevance_score: float = 0.5
    included: bool = True
    rewritten_bullets: list[str] | None = None

    def active_bullets(self) -> list[str]:
        return self.rewritten_bullets if self.rewritten_bullets is not None else self.bullets


class TailoredEducationItem(EducationItem):
    relevance_score: float = 0.5
    included: bool = True


class TailoredSkillItem(SkillItem):
    relevance_score: float = 0.5
    included: bool = True


class TailoredProfile(BaseModel):
    personal: PersonalInfo
    contact: ContactInfo = Field(default_factory=ContactInfo)
    profile_text: str = ""
    profile_text_rewritten: str | None = None
    experience: list[TailoredExperienceItem] = Field(default_factory=list)
    education: list[TailoredEducationItem] = Field(default_factory=list)
    additional_education: list[TailoredEducationItem] = Field(default_factory=list)
    skills: list[TailoredSkillItem] = Field(default_factory=list)
    languages: list[LanguageItem] = Field(default_factory=list)
    hobbies: list[str] = Field(default_factory=list)
    requirements: Requirements | None = None
    tailoring_notes: list[str] = Field(default_factory=list)

    def active_profile_text(self) -> str:
        return self.profile_text_rewritten or self.profile_text

    def included_experience(self) -> list[TailoredExperienceItem]:
        return [e for e in self.experience if e.included]

    def included_education(self) -> list[TailoredEducationItem]:
        return [e for e in self.education if e.included]

    def included_additional_education(self) -> list[TailoredEducationItem]:
        return [e for e in self.additional_education if e.included]

    def included_skills(self) -> list[TailoredSkillItem]:
        return [s for s in self.skills if s.included]

    def drop_least_relevant(self, k: int = 1) -> None:
        """Drop k lowest-scoring included items across all lists."""
        pools: list[tuple[float, str, object]] = []
        for i, e in enumerate(self.experience):
            if e.included:
                pools.append((e.relevance_score, "experience", (i, e)))
        for i, e in enumerate(self.education):
            if e.included:
                pools.append((e.relevance_score, "education", (i, e)))
        for i, e in enumerate(self.additional_education):
            if e.included:
                pools.append((e.relevance_score, "additional_education", (i, e)))
        for i, s in enumerate(self.skills):
            if s.included:
                pools.append((s.relevance_score, "skill", (i, s)))

        pools.sort(key=lambda x: x[0])
        for _, kind, payload in pools[:k]:
            idx, item = payload
            item.included = False

    def as_profile_dict(self) -> dict:
        """Return a plain-dict view with only included items, for renderers."""
        return {
            "personal": self.personal.model_dump(exclude_none=True),
            "contact": self.contact.model_dump(exclude_none=True),
            "profile_text": self.active_profile_text(),
            "experience": [e.model_dump(exclude_none=True) for e in self.included_experience()],
            "education": [e.model_dump(exclude_none=True) for e in self.included_education()],
            "additional_education": [
                e.model_dump(exclude_none=True) for e in self.included_additional_education()
            ],
            "skills": [s.model_dump(exclude_none=True) for s in self.included_skills()],
            "languages": [lang.model_dump(exclude_none=True) for lang in self.languages],
            "hobbies": self.hobbies,
        }


# ── Verification result models ───────────────────────────────────────────────


class Issue(BaseModel):
    severity: Literal["critical", "major", "minor"] = "major"
    area: str  # e.g. "layout", "content", "relevance", "build", "photo"
    description: str
    suggested_fix: str = ""
    page: int | None = None  # which page the issue is on (None = not page-specific)


class VerificationResult(BaseModel):
    passed: bool
    page_count: int = 0
    issues: list[Issue] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


# ── Pipeline config ─────────────────────────────────────────────────────────


class PipelineConfig(BaseModel):
    application_dir: str
    template: str
    max_iterations: int = 5
    relevance_threshold: float = 0.3
    vision_enabled: bool = True
    vision_pages: list[int] = Field(default_factory=lambda: [0])  # pages to screenshot (0-indexed); [] = auto-detect all
    vision_model: str = "MiniMax-M3-NanoGPT"
    text_model: str = "MiniMax-M3-NanoGPT"
    requires_biber: bool = False
    max_page_count: int = 1  # enforce single-page by default
