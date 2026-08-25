"""Substitute TailoredProfile into a LaTeX template and build the PDF.

Uses `template_schema.load_template_schema` to load the per-template mapping
and `template_schema.render` to substitute placeholders. Builds with
`pdflatex` (or detected engine) directly, no dependency on the MCP server.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from .schemas import TailoredProfile
from .template_schema import TemplateSchema, _TEMPLATES_DIR, load_template_schema, render


def _detect_engine(tex_source: str) -> str:
    if re.search(r"%\s*!TeX\s+program\s*=\s*lualatex", tex_source):
        return "lualatex"
    if re.search(r"%\s*!TeX\s+program\s*=\s*xelatex", tex_source):
        return "xelatex"
    return "pdflatex"


def _copy_template_assets(template: TemplateSchema, dest_dir: Path) -> None:
    """Copy any non-.tex assets from the template dir (images, fonts).

    Also checks the cv/ template dir as a fallback for shared assets like photos.
    Preserves subdirectory structure (e.g. IMG/soft/office.png).
    """
    copied: set[str] = set()
    skip_suffixes = {".aux", ".log", ".out", ".bbl", ".bcf", ".blg", ".run.xml"}
    for search_dir in [template.tex_path.parent, _TEMPLATES_DIR / "cv"]:
        if not search_dir.exists():
            continue
        for p in search_dir.rglob("*"):
            if p.is_file() and p.suffix.lower() not in skip_suffixes:
                rel = p.relative_to(search_dir)
                if str(rel) not in copied:
                    target = dest_dir / rel
                    if not target.exists():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(p, target)
                    copied.add(str(rel))


def render_and_build(
    profile: TailoredProfile,
    template_name: str,
    output_dir: str | Path,
    *,
    engine: str | None = None,
    runs: int = 2,
    templates_dir: Path | None = None,
) -> tuple[Path, Path, str]:
    """Render tailored .tex, build to PDF, return (tex_path, pdf_path, log).

    Writes cv.tex, copies template assets, then runs the LaTeX engine N times.
    If the template requires_biber, runs biber between the first and second pass.
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    template = load_template_schema(template_name, templates_dir=templates_dir)
    tex_source = render(template, profile)
    if engine is None:
        engine = _detect_engine(tex_source)

    _copy_template_assets(template, out_dir)

    tex_path = out_dir / "cv.tex"
    tex_path.write_text(tex_source, encoding="utf-8")

    log_chunks: list[str] = []
    for run in range(1, runs + 1):
        log_chunks.append(f"--- Run {run}/{runs} ---")
        proc = subprocess.run(
            [
                engine,
                "-interaction=nonstopmode",
                "-halt-on-error",
                tex_path.name,
            ],
            capture_output=True,
            text=True,
            cwd=str(out_dir),
            timeout=180,
        )
        if proc.returncode != 0:
            log_chunks.append(f"LaTeX failed (exit {proc.returncode})")
            log_chunks.append(proc.stdout[-2000:] if proc.stdout else "")
            log_chunks.append(proc.stderr[-2000:] if proc.stderr else "")
            log_text = "\n".join(log_chunks)
            log_path = out_dir / "cv.build.log"
            log_path.write_text(log_text, encoding="utf-8")
            raise RuntimeError(f"LaTeX build failed:\n{log_text}")

        # Run biber after the first pass if needed
        if template.requires_biber and run == 1:
            log_chunks.append("--- Running biber ---")
            biber_proc = subprocess.run(
                ["biber", tex_path.stem],
                capture_output=True,
                text=True,
                cwd=str(out_dir),
                timeout=120,
            )
            if biber_proc.returncode != 0:
                log_chunks.append(f"biber failed (exit {biber_proc.returncode})")
                log_chunks.append(biber_proc.stdout[-2000:] if biber_proc.stdout else "")
                log_chunks.append(biber_proc.stderr[-2000:] if biber_proc.stderr else "")
                log_text = "\n".join(log_chunks)
                log_path = out_dir / "cv.build.log"
                log_path.write_text(log_text, encoding="utf-8")
                raise RuntimeError(f"biber build failed:\n{log_text}")

    pdf_path = out_dir / "cv.pdf"
    if not pdf_path.exists():
        raise RuntimeError(f"LaTeX produced no PDF at {pdf_path}")

    log_text = "\n".join(log_chunks) + "\nOK"
    log_path = out_dir / "cv.build.log"
    log_path.write_text(log_text, encoding="utf-8")

    return tex_path, pdf_path, log_text
