"""Pretty-print a TailoredProfile to a readable plain-text draft (pre.txt).

This is the artifact the human reviews before the pipeline proceeds to PDF
generation. It is intentionally template-agnostic — the same pre.txt is
generated regardless of which LaTeX template will be used.
"""

from __future__ import annotations

import io
from pathlib import Path

from .schemas import TailoredProfile


def render_pre_text(profile: TailoredProfile) -> str:
    out = io.StringIO()

    personal = profile.personal
    contact = profile.contact
    name = personal.name or ""
    title = personal.title or ""

    if name:
        out.write(name.upper() + "\n")
        out.write("=" * len(name) + "\n")
    if title:
        out.write(title + "\n")

    contact_bits: list[str] = []
    if contact.email:
        contact_bits.append(f"Email: {contact.email}")
    if contact.phone:
        contact_bits.append(f"Phone: {contact.phone}")
    if contact.github:
        contact_bits.append(f"GitHub: {contact.github}")
    if contact.linkedin:
        contact_bits.append(f"LinkedIn: {contact.linkedin}")
    if contact.website:
        contact_bits.append(f"Web: {contact.website}")
    if contact.address:
        contact_bits.append(f"Address: {contact.address}")
    if contact_bits:
        out.write("  |  ".join(contact_bits) + "\n")

    out.write("\n")

    profile_text = profile.active_profile_text()
    if profile_text:
        out.write("PROFILE\n")
        out.write("-------\n")
        out.write(profile_text.strip() + "\n\n")

    experience = profile.included_experience()
    if experience:
        out.write(f"EXPERIENCE  ({len(experience)} entries)\n")
        out.write("-" * 40 + "\n")
        for i, e in enumerate(experience, 1):
            loc = f" ({e.location})" if e.location else ""
            out.write(
                f"{i}. {e.title} — {e.company}{loc}     [{e.start} – {e.end}]\n"
            )
            bullets = e.active_bullets()
            if bullets:
                for b in bullets:
                    out.write(f"     • {b}\n")
            out.write(f"     (relevance: {e.relevance_score:.2f})\n")
        out.write("\n")

    education = profile.included_education()
    if education:
        out.write(f"EDUCATION  ({len(education)} entries)\n")
        out.write("-" * 40 + "\n")
        for i, e in enumerate(education, 1):
            fld = f" {e.field}" if e.field else ""
            out.write(f"{i}. {e.degree}{fld} — {e.institution}     [{e.start} – {e.end}]\n")
            for b in e.bullets:
                out.write(f"     • {b}\n")
        out.write("\n")

    addl = profile.included_additional_education()
    if addl:
        out.write(f"ADDITIONAL EDUCATION  ({len(addl)} entries)\n")
        out.write("-" * 40 + "\n")
        for i, e in enumerate(addl, 1):
            fld = f" {e.field}" if e.field else ""
            out.write(f"{i}. {e.degree}{fld} — {e.institution}     [{e.start} – {e.end}]\n")
        out.write("\n")

    skills = profile.included_skills()
    if skills:
        out.write(f"SKILLS  ({len(skills)})\n")
        out.write("-" * 40 + "\n")
        for s in skills:
            cat = f"  [{s.category}]" if s.category else ""
            out.write(f"  • {s.name}{cat}\n")
        out.write("\n")

    if profile.languages:
        out.write("LANGUAGES\n")
        out.write("-" * 40 + "\n")
        out.write(
            ", ".join(f"{l.name} ({l.level})" for l in profile.languages) + "\n\n"
        )

    if profile.hobbies:
        out.write("HOBBIES\n")
        out.write("-" * 40 + "\n")
        out.write(", ".join(profile.hobbies) + "\n\n")

    excluded_experience = [e for e in profile.experience if not e.included]
    excluded_skills = [s for s in profile.skills if not s.included]
    excluded_education = [e for e in profile.education if not e.included]
    if excluded_experience or excluded_skills or excluded_education:
        out.write("EXCLUDED FROM THIS VERSION\n")
        out.write("-" * 40 + "\n")
        for e in excluded_experience:
            out.write(f"  • exp: {e.title} @ {e.company} (rel {e.relevance_score:.2f})\n")
        for ed in excluded_education:
            out.write(f"  • edu: {ed.degree} @ {ed.institution} (rel {ed.relevance_score:.2f})\n")
        for s in excluded_skills:
            out.write(f"  • skill: {s.name} (rel {s.relevance_score:.2f})\n")
        out.write("\n")

    if profile.tailoring_notes:
        out.write("TAILORING NOTES\n")
        out.write("-" * 40 + "\n")
        for n in profile.tailoring_notes:
            out.write(f"  - {n}\n")
        out.write("\n")

    return out.getvalue()


def write_pre_text(profile: TailoredProfile, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_pre_text(profile), encoding="utf-8")
    return p
