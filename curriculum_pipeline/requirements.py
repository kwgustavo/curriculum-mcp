"""Load raw job description text and extract structured Requirements via LLM.

If a `.requirements.json` already exists next to the raw `.txt`, it is reused
so re-runs don't re-call the LLM. The LLM call is OpenAI-compatible and uses
the configured text model.
"""

from __future__ import annotations

import json
from pathlib import Path

from .llm_client import LLMClient, extract_json
from .schemas import Requirements


def load_raw_text(path: str | Path) -> str:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Requirements file not found: {p}")
    return p.read_text(encoding="utf-8")


def load_or_extract(
    raw_text_path: str | Path,
    llm: LLMClient,
    model: str | None = None,
) -> Requirements:
    """Load cached `.requirements.json` if present, otherwise extract via LLM."""
    raw_path = Path(raw_text_path)
    cached = raw_path.with_suffix(".requirements.json")
    if cached.exists():
        data = json.loads(cached.read_text(encoding="utf-8"))
        data["raw_text"] = load_raw_text(raw_path)
        return Requirements.model_validate(data)

    raw_text = load_raw_text(raw_path)
    req = extract_requirements(raw_text, llm, model=model)
    req.raw_text = raw_text

    cached.write_text(
        json.dumps(req.model_dump(exclude={"raw_text"}), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return req


def extract_requirements(
    raw_text: str,
    llm: LLMClient,
    model: str | None = None,
) -> Requirements:
    """Use the LLM to extract structured fields from raw job-description text."""
    system = (
        "You are a senior technical recruiter. You extract hiring signals from "
        "job descriptions that determine whether a candidate is a strong fit. "
        "Focus on what recruiters actually screen for: must-have skills, scope "
        "(years, seniority, team size), domain, and concrete responsibilities. "
        "Respond ONLY with valid JSON matching the schema. No commentary."
    )

    user = f"""Job description:
\"\"\"
{raw_text}
\"\"\"

Extract into JSON with this exact shape:
{{
  "role_title": "<string, e.g. 'Senior Data Scientist'>",
  "company": "<string, or '' if unknown>",
  "seniority": "<one of: intern | junior | mid | senior | staff | principal | lead | manager | ''>",
  "required_skills": ["<skill>", ...],
  "nice_skills": ["<skill>", ...],
  "keywords": ["<domain keyword or term>", ...],
  "responsibilities": ["<key responsibility a hire must own>", ...],
  "years_experience_min": <int or null>,
  "domain": "<industry or product area, e.g. 'fintech', 'developer tools', 'healthcare AI' or ''>"
}}

Rules:
- required_skills = must-have technical skills mentioned in the posting (the ones a recruiter screens for first)
- nice_skills = bonus / nice-to-have skills (clearly marked optional in the ad)
- keywords = important nouns/adjectives that should be mirrored in the CV (e.g. 'scalable', 'distributed', 'data pipelines', 'cross-functional', 'production-grade')
- responsibilities = 3-6 concrete things this person will own (e.g. 'design ML pipelines', 'lead migration to K8s'). Write them as verb-led phrases, not full sentences.
- years_experience_min = minimum years explicitly required (null if not stated)
- domain = the industry/product area (best single phrase)
- Keep skills lowercase, deduplicated, concise (no punctuation)
- If a field is not present in the posting, use [], '', or null as appropriate
- Prefer specificity over coverage: 5 sharp required skills beats 15 generic ones
"""

    raw = llm.chat_json(system=system, user=user, model=model, temperature=0.0)
    data = extract_json(raw)
    return Requirements.model_validate(data)
