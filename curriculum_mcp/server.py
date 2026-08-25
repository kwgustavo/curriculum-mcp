#!/usr/bin/env python3
"""MCP server for the curriculum pipeline.

Exposes tools for the full CV lifecycle: template management, profile loading,
LLM tailoring, PDF rendering, verification, and application management.

All heavy lifting is delegated to curriculum_pipeline modules.
LLM tools are lazy — they only instantiate an LLMClient when invoked.

Usage:
    python curriculum_mcp/server.py                  # stdio transport
    python curriculum_mcp/server.py --transport sse  # SSE transport
    python curriculum_mcp/server.py --transport sse --port 9000
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP, Image

# ── Resolve paths ────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = REPO_ROOT / "templates"
APPLICATIONS_DIR = REPO_ROOT / "applications"

# ── Add curriculum_pipeline to sys.path ──────────────────────────────────────

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# ── FastMCP server ───────────────────────────────────────────────────────────

mcp = FastMCP(
    "curriculum-builder",
    instructions=(
        "MCP server for the curriculum pipeline. "
        "Provides tools for template management, profile loading, "
        "LLM-based tailoring, PDF rendering, verification, "
        "and application lifecycle management."
    ),
)

# ── Lazy LLM client ──────────────────────────────────────────────────────────

_llm = None


def _get_llm():
    """Return a cached LLMClient or None if LLM_API_KEY is not set."""
    global _llm
    if _llm is not None:
        return _llm
    try:
        from curriculum_pipeline.llm_client import LLMClient

        _llm = LLMClient()
        return _llm
    except RuntimeError:
        return None


def _require_llm():
    """Return LLMClient or raise a clear error."""
    llm = _get_llm()
    if llm is None:
        raise RuntimeError(
            "LLM client failed to initialize. For local endpoints, no API key "
            "is needed — just set:\n"
            "  export LLM_BASE_URL=http://localhost:20128/v1\n\n"
            "For cloud providers, set:\n"
            "  export LLM_API_KEY=sk-...\n"
            "  export LLM_BASE_URL=https://api.openai.com/v1"
        )
    return llm


# ── Template helpers ─────────────────────────────────────────────────────────


def _schema_templates() -> list[dict]:
    """List templates discovered by the pipeline schema system."""
    from curriculum_pipeline.template_schema import discover_templates

    names = discover_templates()
    result = []
    for name in names:
        try:
            from curriculum_pipeline.template_schema import load_template_schema

            schema = load_template_schema(name)
            tex_path = schema.tex_path
            result.append(
                {
                    "name": name,
                    "tex": str(tex_path),
                    "engine": _detect_engine(tex_path),
                    "requires_biber": schema.requires_biber,
                    "placeholders": schema.placeholder_names(),
                }
            )
        except Exception as e:
            result.append({"name": name, "error": str(e)})
    return result


def _detect_engine(tex_path: Path) -> str:
    try:
        content = tex_path.read_text(errors="ignore")
    except Exception:
        return "pdflatex"
    if re.search(r"%\s*!TeX\s+program\s*=\s*lualatex", content):
        return "lualatex"
    if re.search(r"%\s*!TeX\s+program\s*=\s*xelatex", content):
        return "xelatex"
    if r"\directlua" in content or r"\usepackage{luacode}" in content:
        return "lualatex"
    return "pdflatex"


def _raw_templates() -> list[dict]:
    """Legacy template scan by globbing .tex files (keeps old tool behavior)."""
    templates = []
    if not TEMPLATES_DIR.exists():
        return templates
    for tex_file in sorted(TEMPLATES_DIR.rglob("*.tex")):
        rel = tex_file.relative_to(TEMPLATES_DIR)
        engine = _detect_engine(tex_file)
        description = ""
        for line in tex_file.read_text(errors="ignore").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("%"):
                description = stripped[:120]
                break
        templates.append(
            {
                "name": rel.stem,
                "path": str(rel),
                "engine": engine,
                "description": description,
            }
        )
    return templates


def _find_template(name: str) -> dict | None:
    """Find a template by name from the schema system first, then raw scan."""
    for t in _schema_templates():
        if t["name"] == name:
            return t
    for t in _raw_templates():
        if t["name"] == name:
            return t
    return None


def _available_names() -> list[str]:
    return [t["name"] for t in _schema_templates()]


# ══════════════════════════════════════════════════════════════════════════════
# TOOLS — Template management
# ══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
def list_templates() -> str:
    """List all available curriculum templates with metadata.

    Uses the pipeline schema system which discovers templates in
    templates/*/ and templates/*/pipeline/.
    """
    templates = _schema_templates()
    if not templates:
        return "No templates with schema.yaml found under templates/."

    lines = ["Available templates:\n"]
    for t in templates:
        error = t.get("error")
        if error:
            lines.append(f"- **{t['name']}** (error: {error})")
            continue
        ph = ", ".join(t.get("placeholders", []))
        lines.append(f"- **{t['name']}** ({t.get('engine', '?')})")
        lines.append(f"  Requires biber: {t.get('requires_biber', False)}")
        lines.append(f"  Placeholders: {ph}")
        lines.append("")
    return "\n".join(lines)


@mcp.tool()
def get_template(name: str) -> str:
    """Read a template's full source code and dependencies.

    Args:
        name: Template name (e.g. 'cv', '1', '2', '3', '4')
    """
    t = _find_template(name)
    if not t:
        return f"Template '{name}' not found. Available: {', '.join(_available_names())}"

    tex_path = Path(t.get("tex") or "")
    if not tex_path.exists():
        return f"Template tex file not found at {tex_path}"

    source = tex_path.read_text(errors="ignore")

    # Check image dependencies
    images = re.findall(r"\\includegraphics(?:\[.*?\])?\{(.+?)\}", source)
    image_info = ""
    if images:
        image_paths = []
        for img in images:
            img_path = tex_path.parent / img
            if img_path.exists():
                image_paths.append(f"  - {img} (found)")
            else:
                image_paths.append(f"  - {img} (MISSING)")
        image_info = "\n\nImage dependencies:\n" + "\n".join(image_paths)

    schema_info = ""
    if "placeholders" in t:
        schema_info = f"\n\nPlaceholders: {', '.join(t['placeholders'])}"
        schema_info += f"\nRequires biber: {t.get('requires_biber', False)}"

    return (
        f"Template: {name}\nEngine: {t.get('engine', '?')}\n"
        f"Source: {tex_path}{image_info}{schema_info}\n\n"
        f"Source:\n```latex\n{source}\n```"
    )


@mcp.tool()
def get_template_info(template: str) -> str:
    """Get detailed metadata about a template including packages, commands, and structure.

    Args:
        template: Template name (e.g. 'cv', '1', '2', '3', '4')
    """
    t = _find_template(template)
    if not t:
        return f"Template '{template}' not found. Available: {', '.join(_available_names())}"

    tex_path = Path(t.get("tex") or "")
    if not tex_path.exists():
        return f"Template tex file not found at {tex_path}"

    content = tex_path.read_text(errors="ignore")
    packages = re.findall(r"\\usepackage(?:\[.*?\])?\{(.+?)\}", content)
    commands = re.findall(r"\\newcommand\{\\(\w+)\}", content)

    lines = [
        f"Template: {template}",
        f"Engine: {t.get('engine', '?')}",
        f"Source: {tex_path}",
        f"Size: {tex_path.stat().st_size} bytes",
        f"Requires biber: {t.get('requires_biber', False)}",
        "",
        "Packages:",
    ]
    lines.extend(f"  - {p}" for p in packages)
    if commands:
        lines.extend(["", "Custom commands:"])
        lines.extend(f"  - \\{c}" for c in commands)

    if "placeholders" in t:
        lines.extend(["", "Placeholders:"])
        lines.extend(f"  - {p}" for p in t["placeholders"])

    return "\n".join(lines)


@mcp.tool()
def create_curriculum(
    name: str, template: str = "cv", data: dict | None = None
) -> str:
    """Create a new curriculum .tex file from a template.

    Args:
        name: Name for the new curriculum (used as filename stem)
        template: Base template to derive from (default: cv)
        data: Optional dict with fields to replace (e.g. {"name": "John Doe"})
    """
    t = _find_template(template)
    if not t:
        return f"Template '{template}' not found. Available: {', '.join(_available_names())}"

    src_path = Path(t.get("tex") or "")
    if not src_path.exists():
        return f"Template source not found at {src_path}"

    dest_dir = TEMPLATES_DIR / name
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / f"{name}.tex"

    content = src_path.read_text(errors="ignore")
    if data:
        for key, value in data.items():
            content = content.replace(f"<<{key}>>", str(value))

    # Copy image dependencies
    images = re.findall(r"\\includegraphics(?:\[.*?\])?\{(.+?)\}", content)
    for img in images:
        src_img = src_path.parent / img
        dest_img = dest_dir / img
        if src_img.exists() and not dest_img.exists():
            import shutil

            shutil.copy2(src_img, dest_img)

    dest_path.write_text(content)
    files = [f.name for f in dest_dir.iterdir()]
    return (
        f"Created: {dest_path}\n"
        f"Template: {template}\n"
        f"Directory: {dest_dir}\n"
        f"Files: {', '.join(files)}"
    )


@mcp.tool()
def build_pdf(
    template: str,
    engine: str | None = None,
    runs: int = 2,
    output_dir: str | None = None,
) -> str:
    """Compile a template to PDF using the pipeline renderer.

    Uses renderer.render_and_build which handles placeholder substitution,
    biber, correct cwd, and asset copying. Builds with a synthetic profile
    so all <<placeholders>> are filled with demo values.

    Args:
        template: Template name (e.g. 'cv', '1', '2', '3', '4')
        engine: LaTeX engine override (pdflatex, lualatex, xelatex). Auto-detected if omitted.
        runs: Number of compilation passes (default: 2)
        output_dir: Output directory for built files. Defaults to applications/mcp_build_<template>/
    """
    from curriculum_pipeline.profile import load_profile as _load_profile
    from curriculum_pipeline.renderer import render_and_build
    from curriculum_pipeline.schemas import Profile

    t = _find_template(template)
    if not t:
        return f"Template '{template}' not found. Available: {', '.join(_available_names())}"

    example_profile = REPO_ROOT / "examples" / "profile.yaml"
    if example_profile.exists():
        profile = _load_profile(example_profile)
    else:
        profile = Profile(
            personal={"name": "Demo User", "first_name": "Demo", "last_name": "User"},
        )

    if output_dir is not None:
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
    else:
        out_dir = APPLICATIONS_DIR / f"mcp_build_{template}"
        out_dir.mkdir(parents=True, exist_ok=True)

    try:
        tex_path, pdf_path, log = render_and_build(
            profile, template, out_dir, runs=runs
        )
        size_kb = pdf_path.stat().st_size / 1024
        return f"Success: {pdf_path.name} ({size_kb:.1f} KB)\nOutput: {out_dir}"
    except Exception as e:
        return f"Build failed: {e}"


@mcp.tool()
def screenshot_pdf(
    template: str,
    page: int = 0,
    width: int = 840,
    pdf_path: str | None = None,
) -> str:
    """Take a screenshot of a PDF page and save as JPEG for AI quality verification.

    Returns the image path and extracted text content so the AI can verify
    element placement, typography, spacing, and content correctness.

    Args:
        template: Template name (e.g. 'cv', '1', '2', '3', '4')
        page: Page number to screenshot (0-indexed, default: 0 = first page)
        width: Output width in pixels (default: 840)
        pdf_path: Explicit path to the PDF file (optional). If omitted, searches
                  applications/mcp_build_<template>/cv.pdf then template dir.
    """
    import fitz

    t = _find_template(template)
    if not t:
        return f"Template '{template}' not found. Available: {', '.join(_available_names())}"

    tex_path = Path(t.get("tex") or "")

    if pdf_path:
        resolved_pdf = Path(pdf_path)
    else:
        candidates = [
            APPLICATIONS_DIR / f"mcp_build_{template}" / "cv.pdf",
            tex_path.parent / "cv.pdf",
            tex_path.parent.parent / "cv.pdf",
        ]
        resolved_pdf = None
        for c in candidates:
            if c.exists():
                resolved_pdf = c
                break
        if resolved_pdf is None:
            return (
                f"PDF not found for template '{template}'. Searched:\n"
                + "\n".join(f"  {c}" for c in candidates)
                + "\nRun build_pdf first, or pass pdf_path explicitly."
            )

    if not resolved_pdf.exists():
        return f"PDF not found at {resolved_pdf}. Run build_pdf first."

    doc = fitz.open(str(resolved_pdf))
    if page >= len(doc):
        doc.close()
        return f"Page {page} does not exist. PDF has {len(doc)} page(s)."

    page_rect = doc[page].rect
    page_width_inches = page_rect.width / 72
    dpi = int(width / page_width_inches)
    pix = doc[page].get_pixmap(dpi=dpi)

    out_path = resolved_pdf.parent / f"cv_p{page}.jpg"
    pix.save(str(out_path), jpg_quality=90)

    text = doc[page].get_text("text")
    blocks = doc[page].get_text("dict")["blocks"]
    font_summary: dict[str, int] = {}
    for block in blocks:
        if block["type"] == 0:
            for line in block["lines"]:
                for span in line["spans"]:
                    font = span["font"]
                    size = round(span["size"], 1)
                    key = f"{font} {size}pt"
                    font_summary[key] = font_summary.get(key, 0) + len(span["text"])

    total_pages = len(doc)
    doc.close()

    size_kb = out_path.stat().st_size / 1024
    top_fonts = sorted(font_summary.items(), key=lambda x: -x[1])[:8]
    font_display = ", ".join(f"{f} ({c} chars)" for f, c in top_fonts)

    text_report = (
        f"Screenshot: {out_path.name} ({pix.width}x{pix.height}, {size_kb:.1f} KB)\n"
        f"Page: {page + 1}/{total_pages}\n"
        f"Rendered at: {int(dpi)} DPI, {width}px wide\n\n"
        f"--- Extracted Text ---\n{text.strip()}\n\n"
        f"--- Font Summary ---\n{font_display}\n\n"
        f"--- Verification Checklist ---\n"
        f"- [ ] Fonts match intended design\n"
        f"- [ ] Spacing is consistent\n"
        f"- [ ] Alignment is correct\n"
        f"- [ ] No overflow or clipping\n"
        f"- [ ] All elements are visible\n"
        f"- [ ] Colors and contrast are appropriate\n"
    )

    return [Image(path=str(out_path)), text_report]


@mcp.tool()
def screenshot_pdf_all(
    template: str, width: int = 840, pdf_path: str | None = None
) -> str:
    """Take screenshots of all pages in a PDF for AI quality verification.

    Args:
        template: Template name (e.g. 'cv', '1', '2', '3', '4')
        width: Output width in pixels (default: 840)
        pdf_path: Explicit path to the PDF file (optional). If omitted, searches
                  applications/mcp_build_<template>/cv.pdf then template dir.
    """
    import fitz

    t = _find_template(template)
    if not t:
        return f"Template '{template}' not found. Available: {', '.join(_available_names())}"

    tex_path = Path(t.get("tex") or "")

    if pdf_path:
        resolved_pdf = Path(pdf_path)
    else:
        candidates = [
            APPLICATIONS_DIR / f"mcp_build_{template}" / "cv.pdf",
            tex_path.parent / "cv.pdf",
            tex_path.parent.parent / "cv.pdf",
        ]
        resolved_pdf = None
        for c in candidates:
            if c.exists():
                resolved_pdf = c
                break
        if resolved_pdf is None:
            return (
                f"PDF not found for template '{template}'. Searched:\n"
                + "\n".join(f"  {c}" for c in candidates)
                + "\nRun build_pdf first, or pass pdf_path explicitly."
            )

    if not resolved_pdf.exists():
        return f"PDF not found at {resolved_pdf}. Run build_pdf first."

    doc = fitz.open(str(resolved_pdf))
    total = len(doc)
    results = []
    images = []

    for i in range(total):
        page_rect = doc[i].rect
        page_width_inches = page_rect.width / 72
        dpi = int(width / page_width_inches)
        pix = doc[i].get_pixmap(dpi=dpi)
        out_path = resolved_pdf.parent / f"cv_p{i}.jpg"
        pix.save(str(out_path), jpg_quality=90)
        images.append(Image(path=str(out_path)))

        text = doc[i].get_text("text").strip()
        text_preview = text[:150] + "..." if len(text) > 150 else text
        size_kb = out_path.stat().st_size / 1024
        results.append(
            f"Page {i + 1}: {out_path.name} ({pix.width}x{pix.height}, {size_kb:.1f} KB)\n"
            f"  Text preview: {text_preview}"
        )

    doc.close()
    summary = f"Screenshots for {total} page(s):\n\n" + "\n\n".join(results)
    return images + [summary]


# ══════════════════════════════════════════════════════════════════════════════
# TOOLS — Profile management (no LLM)
# ══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
def load_profile(path: str) -> str:
    """Load a profile from a YAML or JSON file and return its fields.

    Args:
        path: Path to profile.yaml or profile.json
    """
    from curriculum_pipeline.profile import load_profile as _load

    try:
        profile = _load(path)
    except Exception as e:
        return f"Error loading profile: {e}"

    return json.dumps(profile.model_dump(exclude_none=True), indent=2, ensure_ascii=False)


@mcp.tool()
def validate_profile(data: dict) -> str:
    """Validate profile data against the Profile schema. Returns errors or OK.

    Args:
        data: Profile fields as a dict (personal, contact, experience, etc.)
    """
    from curriculum_pipeline.schemas import Profile

    try:
        Profile.model_validate(data)
        return "OK — profile data is valid."
    except Exception as e:
        return f"Validation error: {e}"


@mcp.tool()
def save_profile(data: dict, path: str) -> str:
    """Save profile data to a YAML or JSON file.

    Args:
        data: Profile fields as a dict
        path: Destination path (.yaml or .json)
    """
    from curriculum_pipeline.profile import save_profile as _save
    from curriculum_pipeline.schemas import Profile

    try:
        profile = Profile.model_validate(data)
        _save(profile, path)
        return f"Profile saved to {path}"
    except Exception as e:
        return f"Error: {e}"


# ══════════════════════════════════════════════════════════════════════════════
# TOOLS — Requirements extraction (LLM)
# ══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
def extract_requirements(raw_text: str, model: str | None = None) -> str:
    """Extract structured job requirements from raw text using the LLM.

    Args:
        raw_text: The raw job description text
        model: LLM model override (optional)
    """
    from curriculum_pipeline.requirements import extract_requirements as _extract

    llm = _require_llm()
    try:
        req = _extract(raw_text, llm, model=model)
    except Exception as e:
        return f"LLM extraction failed: {e}"
    return json.dumps(req.model_dump(exclude={"raw_text"}), indent=2, ensure_ascii=False)


@mcp.tool()
def load_requirements_cached(raw_text_path: str, model: str | None = None) -> str:
    """Load or extract job requirements. Caches to .requirements.json beside the source.

    Args:
        raw_text_path: Path to the raw job description .txt file
        model: LLM model override (optional)
    """
    from curriculum_pipeline.requirements import load_or_extract

    llm = _require_llm()
    try:
        req = load_or_extract(raw_text_path, llm, model=model)
    except Exception as e:
        return f"Failed: {e}"
    return json.dumps(req.model_dump(exclude={"raw_text"}), indent=2, ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════════════════
# TOOLS — Tailoring (LLM)
# ══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
def tailor_profile(
    profile_path: str,
    requirements_path: str,
    threshold: float = 0.3,
    model: str | None = None,
) -> str:
    """Tailor a profile for a specific job using the LLM. Writes pre.txt + tailored.json.

    Args:
        profile_path: Path to profile.yaml/json
        requirements_path: Path to requirements.txt or requirements.json
        threshold: Relevance threshold for inclusion (0.0-1.0, default 0.3)
        model: LLM model override (optional)
    """
    from curriculum_pipeline.profile import load_profile
    from curriculum_pipeline.requirements import load_or_extract
    from curriculum_pipeline.tailor import tailor_profile as _tailor

    llm = _require_llm()
    try:
        profile = load_profile(profile_path)
        requirements = load_or_extract(requirements_path, llm, model=model)
        tailored = _tailor(profile, requirements, llm, threshold=threshold, model=model)
    except Exception as e:
        return f"Tailoring failed: {e}"

    result = {
        "tailored_profile": tailored.model_dump(exclude_none=True),
        "tailoring_notes": tailored.tailoring_notes,
        "included_experience": len(tailored.included_experience()),
        "included_education": len(tailored.included_education()),
        "included_skills": len(tailored.included_skills()),
    }
    return json.dumps(result, indent=2, ensure_ascii=False)


@mcp.tool()
def render_pre_text(tailored_json_path: str) -> str:
    """Generate the human-readable pre.txt from a tailored.json file.

    Args:
        tailored_json_path: Path to tailored.json
    """
    import json as _json

    from curriculum_pipeline.render_text import render_pre_text as _render
    from curriculum_pipeline.schemas import TailoredProfile

    try:
        data = _json.loads(Path(tailored_json_path).read_text(encoding="utf-8"))
        tailored = TailoredProfile.model_validate(data)
    except Exception as e:
        return f"Error loading tailored profile: {e}"

    return _render(tailored)


# ══════════════════════════════════════════════════════════════════════════════
# TOOLS — Pipeline orchestration
# ══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
def init_application(
    name: str,
    profile_path: str,
    requirements_path: str,
) -> str:
    """Create an application directory with inputs staged.

    Args:
        name: Application name (used as directory under applications/)
        profile_path: Path to profile.yaml/json
        requirements_path: Path to requirements.txt
    """
    from curriculum_pipeline.pipeline import init_application_dir
    from curriculum_pipeline.profile import load_profile

    try:
        profile = load_profile(profile_path)
        paths = init_application_dir(
            APPLICATIONS_DIR / name, profile, requirements_path
        )
    except Exception as e:
        return f"Error: {e}"

    return (
        f"Application directory created: {paths.root}\n"
        f"Inputs: {paths.inputs}\n"
        f"Profile: {paths.profile_path}\n"
        f"Requirements: {paths.requirements_raw}"
    )


@mcp.tool()
def run_render_loop(
    application_dir: str,
    template: str = "cv",
    max_iterations: int = 5,
    vision: bool = True,
    vision_model: str | None = None,
    text_model: str | None = None,
    vision_pages: list[int] | None = None,
    max_page_count: int = 1,
) -> str:
    """Run the render → verify → iterate loop on an approved application.

    The application must have tailored.json and .approved file.

    Args:
        application_dir: Path to the application directory
        template: Template name (default: cv)
        max_iterations: Maximum number of render-verify iterations (default: 5)
        vision: Enable vision-based verification (default: True)
        vision_model: Vision model override (optional)
        text_model: Text model override (optional)
        vision_pages: Pages to screenshot for vision check (0-indexed; None = all pages)
        max_page_count: Maximum allowed page count (default: 1)
    """
    import json as _json

    from curriculum_pipeline.pipeline import (
        ApplicationPaths,
        run_render_loop as _run,
    )
    from curriculum_pipeline.schemas import PipelineConfig, Requirements, TailoredProfile

    app_dir = Path(application_dir)
    paths = ApplicationPaths(app_dir)

    if not paths.tailored_json.exists():
        return f"Error: No tailored.json at {paths.tailored_json}. Run tailor first."
    if not paths.approval_file.exists():
        return (
            f"Error: Application not approved. Create {paths.approval_file} "
            f"after reviewing pre.txt."
        )

    try:
        tailored_data = _json.loads(
            paths.tailored_json.read_text(encoding="utf-8")
        )
        tailored = TailoredProfile.model_validate(tailored_data)
    except Exception as e:
        return f"Error loading tailored.json: {e}"

    requirements = None
    if paths.requirements_json.exists():
        try:
            requirements = Requirements.model_validate(
                _json.loads(paths.requirements_json.read_text(encoding="utf-8"))
            )
        except Exception:
            pass

    llm = _get_llm() if vision else None
    config = PipelineConfig(
        application_dir=str(app_dir),
        template=template,
        max_iterations=max_iterations,
        vision_enabled=vision,
        vision_pages=vision_pages or [0],
        vision_model=vision_model or "MiniMax-M3-NanoGPT",
        text_model=text_model or "MiniMax-M3-NanoGPT",
        max_page_count=max_page_count,
    )

    try:
        result, iters = _run(paths, config, requirements, llm=llm, base_tailored=tailored)
    except Exception as e:
        return f"Render loop failed: {e}"

    status = "passed" if result.passed else "not converged"
    issues_summary = "; ".join(
        f"[{i.severity}/{i.area}] {i.description}" for i in result.issues
    ) or "none"
    return (
        f"{status} after {iters} iteration(s).\n"
        f"PDF: {paths.cv_pdf}\n"
        f"Page count: {result.page_count}\n"
        f"Issues: {issues_summary}"
    )


@mcp.tool()
def run_full_pipeline(
    profile_path: str,
    requirements_path: str,
    template: str = "cv",
    output: str | None = None,
    max_iterations: int = 5,
    vision: bool = True,
    auto_approve: bool = False,
    vision_pages: list[int] | None = None,
    max_page_count: int = 1,
) -> str:
    """One-shot: tailor → gate → render loop → PDF.

    Args:
        profile_path: Path to profile.yaml/json
        requirements_path: Path to requirements.txt
        template: Template name (default: cv)
        output: Application output directory (default: applications/<company>_<role>)
        max_iterations: Maximum render-verify iterations (default: 5)
        vision: Enable vision verification (default: True)
        auto_approve: Skip human gate, auto-approve tailored output
        vision_pages: Pages to screenshot for vision check (0-indexed; None = all pages)
        max_page_count: Maximum allowed page count (default: 1)
    """
    from curriculum_pipeline.pipeline import run_pipeline
    from curriculum_pipeline.schemas import PipelineConfig

    if output is None:
        output = str(APPLICATIONS_DIR / "auto")

    config = PipelineConfig(
        application_dir=output,
        template=template,
        max_iterations=max_iterations,
        vision_enabled=vision,
        vision_pages=vision_pages or [0],
        max_page_count=max_page_count,
    )

    try:
        pdf = run_pipeline(
            profile_path,
            requirements_path,
            config,
            interactive=False,
            auto_approve=auto_approve,
        )
    except KeyboardInterrupt:
        return "Pipeline cancelled (user quit at gate)."
    except Exception as e:
        return f"Pipeline failed: {e}"

    return f"Final PDF: {pdf}"


# ══════════════════════════════════════════════════════════════════════════════
# TOOLS — Verification
# ══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
def verify_pdf(
    pdf_path: str,
    requirements_path: str | None = None,
    max_pages: int = 1,
) -> str:
    """Run programmatic verification on a PDF (page count, overflow, keywords, build log).

    Args:
        pdf_path: Path to the PDF file
        requirements_path: Optional path to requirements.json for keyword checks
        max_pages: Maximum allowed page count (default: 1)
    """
    import json as _json

    from curriculum_pipeline.schemas import Requirements
    from curriculum_pipeline.verifier import verify_programmatic

    requirements = None
    if requirements_path:
        try:
            data = _json.loads(Path(requirements_path).read_text(encoding="utf-8"))
            requirements = Requirements.model_validate(data)
        except Exception as e:
            return f"Error loading requirements: {e}"

    try:
        result = verify_programmatic(Path(pdf_path), requirements, max_pages=max_pages)
    except Exception as e:
        return f"Verification failed: {e}"

    issues_summary = "; ".join(
        f"[{i.severity}/{i.area}] {i.description}" for i in result.issues
    ) or "none"
    return (
        f"Passed: {result.passed}\n"
        f"Page count: {result.page_count}\n"
        f"Issues: {issues_summary}"
    )


@mcp.tool()
def verify_pdf_vision(
    pdf_path: str,
    requirements_path: str | None = None,
    model: str | None = None,
    pages: list[int] | None = None,
) -> str:
    """Run vision-based verification on a PDF using the LLM.

    Screenshots all pages of the PDF and asks the vision model to inspect
    layout, typography, content, and relevance.

    Args:
        pdf_path: Path to the PDF file
        requirements_path: Optional path to requirements.json
        model: Vision model override (optional)
        pages: Pages to screenshot (0-indexed; None = all pages)
    """
    import json as _json

    from curriculum_pipeline.schemas import Requirements
    from curriculum_pipeline.verifier import verify_vision

    llm = _require_llm()
    requirements = None
    if requirements_path:
        try:
            data = _json.loads(Path(requirements_path).read_text(encoding="utf-8"))
            requirements = Requirements.model_validate(data)
        except Exception as e:
            return f"Error loading requirements: {e}"

    try:
        result = verify_vision(Path(pdf_path), requirements, llm, model=model, pages=pages)
    except Exception as e:
        return f"Vision verification failed: {e}"

    issues_summary = "; ".join(
        f"[{i.severity}/{i.area}] {i.description}" for i in result.issues
    ) or "none"
    return (
        f"Passed: {result.passed}\n"
        f"Page count: {result.page_count}\n"
        f"Issues: {issues_summary}\n"
        f"Notes: {'; '.join(result.notes)}"
    )


@mcp.tool()
def extract_text(pdf_path: str, page: int = 0) -> str:
    """Extract text content from a PDF page.

    Args:
        pdf_path: Path to the PDF file
        page: Page number (0-indexed, default: 0)
    """
    import fitz

    doc = fitz.open(str(pdf_path))
    if page >= len(doc):
        doc.close()
        return f"Page {page} does not exist. PDF has {len(doc)} page(s)."

    text = doc[page].get_text("text")
    total = len(doc)
    doc.close()
    return f"Page {page + 1}/{total}:\n\n{text}"


# ══════════════════════════════════════════════════════════════════════════════
# TOOLS — Application management
# ══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
def list_applications() -> str:
    """List all application directories under applications/."""
    if not APPLICATIONS_DIR.exists():
        return "No applications/ directory found."

    apps = []
    for d in sorted(APPLICATIONS_DIR.iterdir()):
        if not d.is_dir():
            continue
        files = [f.name for f in d.iterdir() if f.is_file()]
        has_tailored = "tailored.json" in files
        has_pdf = "cv.pdf" in files
        approved = (d / ".approved").exists()
        status = "approved" if approved else "pending"
        apps.append(
            f"- **{d.name}** — {status}"
            f"{' | tailored' if has_tailored else ''}"
            f"{' | PDF ready' if has_pdf else ''}"
        )

    if not apps:
        return "No applications found."
    return "Applications:\n" + "\n".join(apps)


@mcp.tool()
def read_application_artifacts(application_dir: str) -> str:
    """Read the key artifacts from an application directory.

    Returns contents of tailored.json, pre.txt, and log.md if they exist.

    Args:
        application_dir: Path to the application directory
    """
    app = Path(application_dir)
    if not app.exists():
        return f"Application directory not found: {app}"

    lines = []
    for fname in ["tailored.json", "pre.txt", "log.md"]:
        fp = app / fname
        if fp.exists():
            content = fp.read_text(encoding="utf-8")
            if fname == "tailored.json":
                # Pretty-print JSON
                try:
                    data = json.loads(content)
                    content = json.dumps(data, indent=2, ensure_ascii=False)
                except Exception:
                    pass
            lines.append(f"--- {fname} ---\n{content}")
        else:
            lines.append(f"--- {fname} --- (not found)")

    if not lines:
        return f"No artifacts found in {app}"

    return "\n\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# RESOURCES
# ══════════════════════════════════════════════════════════════════════════════


@mcp.resource("templates://list")
def resource_template_list() -> str:
    """All templates as JSON (discovered by the pipeline schema system)."""
    return json.dumps(_schema_templates(), indent=2, ensure_ascii=False)


@mcp.resource("templates://{name}/schema")
def resource_template_schema(name: str) -> str:
    """The schema.yaml for a specific template as JSON."""
    from curriculum_pipeline.template_schema import load_template_schema

    try:
        schema = load_template_schema(name)
    except Exception as e:
        return json.dumps({"error": str(e)})

    # Serialize the schema as a dict
    result = {
        "placeholders": {},
        "requires_biber": schema.requires_biber,
        "tex_path": str(schema.tex_path),
    }
    for pname, spec in schema.placeholders.items():
        result["placeholders"][pname] = {
            "field": spec.field,
            "type": spec.type,
            "max_chars": spec.max_chars,
            "max_items": spec.max_items,
            "filter": spec.filter,
            "formatter": spec.formatter,
        }
    return json.dumps(result, indent=2, ensure_ascii=False)


@mcp.resource("templates://{name}/source")
def resource_template_source(name: str) -> str:
    """The raw .tex source for a specific template."""
    t = _find_template(name)
    if not t:
        return json.dumps({"error": f"Template '{name}' not found"})
    tex_path = Path(t.get("tex") or "")
    if not tex_path.exists():
        return json.dumps({"error": f"Source not found at {tex_path}"})
    return tex_path.read_text(errors="ignore")


@mcp.resource("pipeline://status")
def resource_pipeline_status() -> str:
    """Pipeline status: available templates, LLM config, recent applications."""
    import os

    templates = _schema_templates()
    apps = []
    if APPLICATIONS_DIR.exists():
        for d in sorted(APPLICATIONS_DIR.iterdir()):
            if d.is_dir():
                apps.append(d.name)

    status = {
        "templates": [t["name"] for t in templates],
        "llm_configured": _get_llm() is not None,
        "llm_base_url": os.environ.get("LLM_BASE_URL", "not set"),
        "llm_text_model": os.environ.get("LLM_TEXT_MODEL", "not set"),
        "llm_vision_model": os.environ.get("LLM_VISION_MODEL", "not set"),
        "applications": apps,
    }
    return json.dumps(status, indent=2, ensure_ascii=False)


@mcp.resource("applications://list")
def resource_applications_list() -> str:
    """List of application directories as JSON."""
    apps = []
    if APPLICATIONS_DIR.exists():
        for d in sorted(APPLICATIONS_DIR.iterdir()):
            if d.is_dir():
                files = [f.name for f in d.iterdir() if f.is_file()]
                apps.append(
                    {
                        "name": d.name,
                        "path": str(d),
                        "has_tailored": "tailored.json" in files,
                        "has_pdf": "cv.pdf" in files,
                        "approved": (d / ".approved").exists(),
                    }
                )
    return json.dumps(apps, indent=2, ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Curriculum Builder MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="Transport type (default: stdio)",
    )
    parser.add_argument(
        "--port", type=int, default=8000, help="Port for SSE transport"
    )
    args = parser.parse_args()

    if args.transport == "sse":
        mcp.run(transport="sse", port=args.port)
    else:
        mcp.run(transport="stdio")
