"""LLM-driven tailoring of a Profile against Requirements.

Produces a TailoredProfile where each experience bullet, education entry, and
skill is scored for relevance to the job posting. The top bullets may be
lightly rewritten to mirror job terminology, but original facts are preserved.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from .llm_client import LLMClient, extract_json
from .schemas import (
    ExperienceItem,
    EducationItem,
    Profile,
    Requirements,
    SkillItem,
    TailoredEducationItem,
    TailoredExperienceItem,
    TailoredProfile,
    TailoredSkillItem,
)


class _ScoredBullet(BaseModel):
    relevance: float
    rewritten: str | None = None


class _ScoredExperience(BaseModel):
    relevance: float
    included: bool
    bullets: list[_ScoredBullet]


class _ScoredEducation(BaseModel):
    relevance: float
    included: bool


class _ScoredSkill(BaseModel):
    relevance: float
    included: bool


class _ScoringResult(BaseModel):
    experience: list[_ScoredExperience]
    education: list[_ScoredEducation]
    additional_education: list[_ScoredEducation]
    skills: list[_ScoredSkill]
    profile_text_rewritten: str | None = None
    notes: list[str] = []


def tailor_profile(
    profile: Profile,
    requirements: Requirements,
    llm: LLMClient,
    *,
    threshold: float = 0.3,
    model: str | None = None,
) -> TailoredProfile:
    """Run LLM scoring + light rewriting, then build TailoredProfile."""

    payload = {
        "requirements": {
            "role_title": requirements.role_title,
            "company": requirements.company,
            "seniority": requirements.seniority,
            "required_skills": requirements.required_skills,
            "nice_skills": requirements.nice_skills,
            "keywords": requirements.keywords,
            "responsibilities": requirements.responsibilities,
            "years_experience_min": requirements.years_experience_min,
            "domain": requirements.domain,
        },
        "profile": {
            "profile_text": profile.profile_text,
            "experience": [e.model_dump() for e in profile.experience],
            "education": [e.model_dump() for e in profile.education],
            "additional_education": [e.model_dump() for e in profile.additional_education],
            "skills": [s.model_dump() for s in profile.skills],
        },
    }

    system = (
        "You are a senior technical recruiter and CV strategist with 15 years "
        "placing candidates at top-tier companies. You know what hiring managers "
        "actually screen for in 6 seconds: quantified impact, scope, recent "
        "relevance, and crisp action verbs. You score each piece of a "
        "candidate's background against a specific job posting and lightly "
        "rewrite the top bullets to mirror the job's language. You never invent "
        "facts, numbers, or claims. If a bullet has no metric, do not invent one. "
        "Respond ONLY with valid JSON matching the requested schema."
    )

    user = f"""Score and lightly rewrite the candidate's profile for this job.

What recruiters screen for, in priority order:
1. Impact and metrics — bullets with quantified outcomes (%, $, latency, users, scale)
2. Scope — team size, budget, system scale, seniority of stakeholders
3. Recent relevance — last 2-3 roles matter most for a senior role
4. Required-skill match — direct overlap with must-have skills
5. Domain context — same industry / product area signals transferability

Job requirements:
{json.dumps(payload['requirements'], indent=2)}

Candidate profile:
{json.dumps(payload['profile'], indent=2)}

Return JSON with this exact shape:
{{
  "experience": [
    {{
      "relevance": <float 0..1>,
      "included": <bool>,
      "bullets": [{{"relevance": <float>, "rewritten": <str|null>}}, ...]
    }}, ...
  ],
  "education": [{{"relevance": <float>, "included": <bool>}}, ...],
  "additional_education": [{{"relevance": <float>, "included": <bool>}}, ...],
  "skills": [{{"relevance": <float>, "included": <bool>}}, ...],
  "profile_text_rewritten": <str|null>,
  "notes": [<short string explaining key tailoring decisions>, ...]
}}

Rules:

SCORING:
- Score relevance 0..1 against the role (1.0 = directly required + recent + scoped to seniority; 0.0 = unrelated or stale)
- Boost recent experience (last 3-5 years): same job title or domain in last role = +0.1-0.2
- Penalize stale experience (>8 years old, no progression): cap at 0.3 unless directly required
- Include items whose overall relevance is above {threshold}
- For skills: score 1.0 only if the skill is named in required_skills AND the candidate has it at expert/advanced level; demote "exposure" to nice_skills level (0.5)

BULLET REWRITING (only rewrite the top-3 most relevant bullets per role, only those with relevance > 0.6):
- Lead with a strong action verb in past tense (Built, Led, Shipped, Owned, Designed, Reduced, Scaled, Migrated, Architected) — NEVER "Responsible for", "Worked on", "Helped with"
- Preserve the original metric if present (percentages, dollar amounts, user counts, latency). If no metric exists, do NOT fabricate one.
- Mirror 1-2 keywords from the job ad when the original bullet describes equivalent work (e.g. ad says "distributed systems", candidate has "microservices" → use "distributed systems")
- Add scope signal when missing: if the bullet implies scale but doesn't quantify it, surface the strongest scope word already implicit ("team of 5", "10M events/day")
- Keep the rewritten text concise — under 180 characters — and grounded in the original (no new claims)
- If the original bullet is already strong, leave it as null (no rewrite needed)

PROFILE TEXT (summary at top of CV):
- If rewriting, keep it to 2-3 sentences, lead with the role-aligned headline, follow with years of relevant experience + 1-2 differentiators that map to required skills
- If the original is already strong for this role, leave profile_text_rewritten as null

GENERAL:
- Preserve the input order of all lists
- Notes should explain the 2-3 most consequential decisions (e.g. "Emphasized X because ad stresses Y", "Demoted old role Z — >8 years stale")
"""

    raw = llm.chat_json(system=system, user=user, model=model, temperature=0.2)
    data = extract_json(raw)
    scored = _ScoringResult.model_validate(data)

    # Re-attach rewritten bullets / scores back to the profile items.
    tailored = _attach_scoring(profile, requirements, scored)
    return tailored


def _attach_scoring(
    profile: Profile,
    requirements: Requirements,
    scored: _ScoringResult,
) -> TailoredProfile:
    n_exp = len(profile.experience)
    n_edu = len(profile.education)
    n_edu_add = len(profile.additional_education)
    n_skills = len(profile.skills)

    if len(scored.experience) != n_exp:
        scored.experience = _pad_or_trim(scored.experience, n_exp)
    if len(scored.education) != n_edu:
        scored.education = _pad_or_trim(scored.education, n_edu)
    if len(scored.additional_education) != n_edu_add:
        scored.additional_education = _pad_or_trim(scored.additional_education, n_edu_add)
    if len(scored.skills) != n_skills:
        scored.skills = _pad_or_trim(scored.skills, n_skills)

    experience: list[TailoredExperienceItem] = []
    for src, sc in zip(profile.experience, scored.experience):
        bullets_out = []
        n_src_bullets = len(src.bullets)
        n_sc_bullets = len(sc.bullets)
        if n_sc_bullets != n_src_bullets:
            sc.bullets = _pad_or_trim_bullets(sc.bullets, n_src_bullets)
        for b_src, b_sc in zip(src.bullets, sc.bullets):
            rewritten = b_sc.rewritten if (b_sc.rewritten and b_sc.rewritten.strip()) else None
            if rewritten and rewritten.strip() == b_src.strip():
                rewritten = None
            bullets_out.append(rewritten)
        experience.append(
            TailoredExperienceItem(
                title=src.title,
                company=src.company,
                location=src.location,
                start=src.start,
                end=src.end,
                bullets=src.bullets,
                rewritten_bullets=bullets_out if any(bullets_out) else None,
                relevance_score=sc.relevance,
                included=sc.included,
            )
        )

    education: list[TailoredEducationItem] = [
        TailoredEducationItem(
            degree=src.degree,
            field=src.field,
            institution=src.institution,
            start=src.start,
            end=src.end,
            bullets=src.bullets,
            relevance_score=sc.relevance,
            included=sc.included,
        )
        for src, sc in zip(profile.education, scored.education)
    ]

    additional_education: list[TailoredEducationItem] = [
        TailoredEducationItem(
            degree=src.degree,
            field=src.field,
            institution=src.institution,
            start=src.start,
            end=src.end,
            bullets=src.bullets,
            relevance_score=sc.relevance,
            included=sc.included,
        )
        for src, sc in zip(profile.additional_education, scored.additional_education)
    ]

    skills: list[TailoredSkillItem] = [
        TailoredSkillItem(
            name=src.name,
            category=src.category,
            proficiency=src.proficiency,
            relevance_score=sc.relevance,
            included=sc.included,
        )
        for src, sc in zip(profile.skills, scored.skills)
    ]

    return TailoredProfile(
        personal=profile.personal,
        contact=profile.contact,
        profile_text=profile.profile_text,
        profile_text_rewritten=scored.profile_text_rewritten,
        experience=experience,
        education=education,
        additional_education=additional_education,
        skills=skills,
        languages=profile.languages,
        hobbies=profile.hobbies,
        requirements=requirements,
        tailoring_notes=scored.notes,
    )


def _pad_or_trim(items: list, n: int) -> list:
    if len(items) > n:
        return items[:n]
    pad_obj = items[-1].model_copy() if items else None
    out = list(items)
    while len(out) < n:
        if pad_obj:
            copy = pad_obj.model_copy(deep=True)
            out.append(copy)
        else:
            break
    return out


def _pad_or_trim_bullets(items: list[_ScoredBullet], n: int) -> list[_ScoredBullet]:
    if len(items) > n:
        return items[:n]
    out = list(items)
    while len(out) < n:
        out.append(_ScoredBullet(relevance=0.0, rewritten=None))
    return out
