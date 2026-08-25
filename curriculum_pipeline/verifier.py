"""Verify a rendered CV PDF.

Programmatic checks (always run):
  - Page count <= max_page_count (default: 1)
  - No text block bounding box exceeds the page rectangle (overflow)
  - All required skills / keywords appear in extracted text
  - Build log has no critical errors

Vision check (enabled by default, requires an OpenAI-compatible vision-capable model):
  - Screenshots all pages of the PDF to JPEG
  - Asks the vision model to spot issues (visual balance, clipping, alignment,
    readability, recruiter screen concerns)
  - Returns structured Issues that the iteration loop can act on
  - Graceful fallback: if vision fails, continues with programmatic-only
"""

from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF

from .llm_client import LLMClient, extract_json, default_vision_model
from .schemas import Issue, Requirements, VerificationResult


def screenshot_page(pdf_path: Path, page: int = 0, width: int = 840) -> Path:
    """Render a single page to JPEG, return output path."""
    doc = fitz.open(str(pdf_path))
    if page >= len(doc):
        doc.close()
        raise IndexError(f"page {page} does not exist (pdf has {len(doc)} pages)")
    pg = doc[page]
    page_width_inches = pg.rect.width / 72
    dpi = max(72, int(width / page_width_inches))
    pix = pg.get_pixmap(dpi=dpi)
    out = pdf_path.with_name(f"{pdf_path.stem}_p{page}.jpg")
    pix.save(str(out), jpg_quality=88)
    doc.close()
    return out


def screenshot_all_pages(pdf_path: Path, width: int = 840) -> list[Path]:
    """Render all pages to JPEG, return list of output paths."""
    doc = fitz.open(str(pdf_path))
    paths: list[Path] = []
    for i in range(len(doc)):
        pg = doc[i]
        page_width_inches = pg.rect.width / 72
        dpi = max(72, int(width / page_width_inches))
        pix = pg.get_pixmap(dpi=dpi)
        out = pdf_path.with_name(f"{pdf_path.stem}_p{i}.jpg")
        pix.save(str(out), jpg_quality=88)
        paths.append(out)
    doc.close()
    return paths


def get_page_count(pdf_path: Path) -> int:
    """Return the page count of a PDF."""
    doc = fitz.open(str(pdf_path))
    n = len(doc)
    doc.close()
    return n


def _check_page_count(pdf_path: Path, max_pages: int = 1) -> tuple[int, list[Issue]]:
    n = get_page_count(pdf_path)
    issues: list[Issue] = []
    if n == 0:
        issues.append(
            Issue(
                severity="critical",
                area="layout",
                description="PDF has 0 pages.",
                suggested_fix="Check LaTeX build output.",
            )
        )
    elif n > max_pages:
        issues.append(
            Issue(
                severity="critical",
                area="layout",
                description=f"CV is {n} page(s); must be ≤ {max_pages}.",
                suggested_fix=(
                    "Drop the lowest-relevance experience, education, or skill "
                    "items so content fits within the page limit."
                ),
            )
        )
    return n, issues


def _check_overflow(pdf_path: Path) -> list[Issue]:
    """Detect text blocks whose bbox exceeds the page rect (clipping/overflow)."""
    doc = fitz.open(str(pdf_path))
    issues: list[Issue] = []
    for page_idx, page in enumerate(doc):
        rect = page.rect
        blocks = page.get_text("dict")["blocks"]
        for bi, block in enumerate(blocks):
            if block["type"] != 0:
                continue
            bx0, by0, bx1, by1 = block["bbox"]
            overflow_x = max(0, bx1 - rect.x1) + max(0, rect.x0 - bx0)
            overflow_y = max(0, by1 - rect.y1) + max(0, rect.y0 - by1)
            if overflow_x > 1 or overflow_y > 1:
                issues.append(
                    Issue(
                        severity="major",
                        area="layout",
                        description=(
                            f"Page {page_idx + 1}: text block #{bi} overflows page "
                            f"by ({overflow_x:.1f}, {overflow_y:.1f}) pt."
                        ),
                        suggested_fix=(
                            "Reduce content length or remove low-relevance items."
                        ),
                        page=page_idx,
                    )
                )
    doc.close()
    return issues


def _check_keywords(pdf_path: Path, requirements: Requirements | None) -> list[Issue]:
    if not requirements:
        return []
    issues: list[Issue] = []
    doc = fitz.open(str(pdf_path))
    full_text = "\n".join(page.get_text("text") for page in doc).lower()
    doc.close()

    missing_required = [
        s for s in requirements.required_skills if s.lower() not in full_text
    ]
    if missing_required:
        issues.append(
            Issue(
                severity="major",
                area="relevance",
                description=(
                    f"Missing required skills in CV: {', '.join(missing_required)}"
                ),
                suggested_fix=(
                    "Rewrite an existing experience bullet to mention this skill, "
                    "or surface it under Skills."
                ),
            )
        )

    missing_keywords = [
        k for k in requirements.keywords if k.lower() not in full_text
    ]
    if len(missing_keywords) > max(1, len(requirements.keywords) // 2):
        issues.append(
            Issue(
                severity="minor",
                area="relevance",
                description=(
                    f"Many keywords absent from CV: {', '.join(missing_keywords)}"
                ),
                suggested_fix=(
                    "Weave top keywords into the profile summary and bullets."
                ),
            )
        )
    return issues


def _check_build_log(tex_dir: Path) -> list[Issue]:
    log_path = tex_dir / "cv.build.log"
    if not log_path.exists():
        return []
    text = log_path.read_text(encoding="utf-8", errors="ignore")
    issues: list[Issue] = []
    if "LaTeX failed" in text or "Fatal error" in text:
        issues.append(
            Issue(
                severity="critical",
                area="build",
                description="LaTeX log reports a fatal error.",
                suggested_fix="Inspect cv.build.log for details.",
            )
        )
    return issues


def verify_programmatic(
    pdf_path: Path,
    requirements: Requirements | None = None,
    max_pages: int = 1,
) -> VerificationResult:
    page_count, page_issues = _check_page_count(pdf_path, max_pages)
    overflow_issues = _check_overflow(pdf_path)
    keyword_issues = _check_keywords(pdf_path, requirements)
    log_issues = _check_build_log(pdf_path.parent)

    issues = page_issues + overflow_issues + keyword_issues + log_issues
    critical = any(i.severity == "critical" for i in issues)
    passed = not critical and not any(i.severity == "major" for i in issues)

    notes: list[str] = []
    if not issues:
        notes.append("Programmatic checks passed.")

    return VerificationResult(
        passed=passed,
        page_count=page_count,
        issues=issues,
        notes=notes,
    )


def verify_vision(
    pdf_path: Path,
    requirements: Requirements | None,
    llm: LLMClient,
    model: str | None = None,
    pages: list[int] | None = None,
) -> VerificationResult:
    """Run vision checks via OpenAI-compatible multimodal model.

    If pages is None or empty, screenshots all pages of the PDF.
    Each page is sent separately to the vision model, and issues are
    tagged with the page number.
    """
    # Determine which pages to screenshot
    total_pages = get_page_count(pdf_path)
    if pages is None or len(pages) == 0:
        pages_to_check = list(range(total_pages))
    else:
        pages_to_check = [p for p in pages if p < total_pages]

    if not pages_to_check:
        return VerificationResult(
            passed=True,
            page_count=total_pages,
            issues=[],
            notes=["No pages to check."],
        )

    system = (
        "You are a meticulous CV layout reviewer and technical recruiter. "
        "You inspect a CV rendering and report concrete, actionable issues. "
        "Respond ONLY with valid JSON matching the requested schema."
    )
    req_context = ""
    if requirements:
        req_context = (
            f"\nThe CV is tailored for: {requirements.role_title or '(unknown role)'}"
            f" at {requirements.company or '(unknown company)'}.\n"
            f"Required skills: {', '.join(requirements.required_skills)}\n"
            f"Keywords to look for: {', '.join(requirements.keywords)}\n"
        )

    user = f"""Inspect this CV page screenshot.{req_context}

You are reviewing this CV the way a 6-second recruiter screen goes:
- Glance at the top third: name, current title, profile summary — does it match the role?
- Scan for required skills and quantified impact in the first 2 roles
- Look for visual problems that signal carelessness: clipped text, empty bands, misaligned columns, broken icons, bad photo crops
- Check for typos or formatting inconsistencies

Report any issues you observe. For each issue, give:
- severity: "critical" (e.g. text clipped, missing photo, broken layout, typo in title) |
  "major" (e.g. large empty band, misalignment, hard-to-read section, photo crop cuts off face) |
  "minor" (e.g. inconsistent spacing, slightly off alignment, tiny color mismatch)
- area: one of "layout", "typography", "content", "relevance", "photo"
- description: a short factual sentence describing the issue specifically (not generic)
- suggested_fix: a concrete actionable change the candidate or template author can make
- page: <int, 0-indexed page number where the issue is observed>

If the CV looks clean and meets the single-page requirement, return an empty issues list.

Respond with JSON:
{{"passed": <bool>, "issues": [...]}}
"""

    all_issues: list[Issue] = []
    all_notes: list[str] = []
    overall_passed = True

    for page_idx in pages_to_check:
        try:
            image_path = screenshot_page(pdf_path, page=page_idx)
        except Exception as e:
            all_issues.append(
                Issue(
                    severity="major",
                    area="build",
                    description=f"Failed to screenshot page {page_idx}: {e}",
                    suggested_fix="Check that the PDF was built successfully.",
                    page=page_idx,
                )
            )
            overall_passed = False
            continue

        try:
            page_user = f"[Page {page_idx + 1} of {total_pages}]\n{user}"
            raw = llm.vision(system=system, user=page_user, image_path=image_path, model=model)
            data = extract_json(raw)
            issues_raw = data.get("issues", []) or []
            for it in issues_raw:
                try:
                    issue = Issue.model_validate(it)
                    issue.page = page_idx  # ensure page tag is set
                    all_issues.append(issue)
                except Exception:
                    continue
            page_passed = bool(data.get("passed", False))
            if not page_passed:
                overall_passed = False
            all_notes.append(f"Vision screenshot p{page_idx}: {image_path}")
        except Exception as e:
            all_notes.append(f"Vision check failed on page {page_idx}: {e}")
            overall_passed = False

    if not all_issues and overall_passed:
        overall_passed = True

    return VerificationResult(
        passed=overall_passed,
        page_count=total_pages,
        issues=all_issues,
        notes=all_notes,
    )


def verify(
    pdf_path: Path,
    requirements: Requirements | None,
    llm: LLMClient | None = None,
    *,
    use_vision: bool = True,
    vision_model: str | None = None,
    pages: list[int] | None = None,
    max_pages: int = 1,
) -> VerificationResult:
    """Run programmatic checks; optionally add vision checks on top."""
    result = verify_programmatic(pdf_path, requirements, max_pages=max_pages)
    if not use_vision or llm is None:
        return result
    try:
        vision_result = verify_vision(
            pdf_path, requirements, llm, model=vision_model, pages=pages
        )
    except Exception as e:
        result.notes.append(f"Vision check failed: {e}")
        return result

    merged_issues = result.issues + vision_result.issues
    critical_or_major = any(
        i.severity in {"critical", "major"} for i in merged_issues
    )
    return VerificationResult(
        passed=result.passed and vision_result.passed and not critical_or_major,
        page_count=result.page_count,
        issues=merged_issues,
        notes=result.notes + vision_result.notes,
    )
