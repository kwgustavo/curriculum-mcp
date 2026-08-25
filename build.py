#!/usr/bin/env python3
"""Build LaTeX templates to PDF.

Usage:
    python build.py <template.tex> [--engine pdflatex|lualatex] [--outdir <dir>]

Detects engine from .tex file directives or content, compiles, and reports errors.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path


def detect_engine(tex_path: Path) -> str:
    """Detect LaTeX engine from file directives or content."""
    content = tex_path.read_text(errors="ignore")

    # Check magic comments first
    if re.search(r"%\s*!TeX\s+program\s*=\s*lualatex", content):
        return "lualatex"
    if re.search(r"%\s*!TeX\s+program\s*=\s*xelatex", content):
        return "xelatex"
    if re.search(r"%\s*!TeX\s+program\s*=\s*pdflatex", content):
        return "pdflatex"

    # Check for LuaLaTeX-specific packages/commands
    if r"\directlua" in content or r"\usepackage{luacode}" in content:
        return "lualatex"

    return "pdflatex"


def parse_log_errors(log_path: Path) -> list[str]:
    """Extract meaningful errors from a LaTeX .log file."""
    if not log_path.exists():
        return ["No log file found"]

    text = log_path.read_text(errors="ignore")
    errors = []
    lines = text.splitlines()

    i = 0
    while i < len(lines):
        line = lines[i]

        # LaTeX error pattern
        if line.startswith("! ") or "LaTeX Error" in line or "Fatal error" in line:
            # Collect the error and a few context lines
            block = [line.rstrip()]
            for j in range(1, min(4, len(lines) - i)):
                next_line = lines[i + j].rstrip()
                if next_line and not next_line.startswith("!"):
                    block.append(next_line)
            errors.append("\n".join(block))
            i += len(block)
            continue

        # Undefined control sequence
        if "Undefined control sequence" in line:
            errors.append(line.rstrip())
            i += 1
            continue

        # Missing file
        if "File `" in line and "' not found" in line:
            errors.append(line.rstrip())
            i += 1
            continue

        i += 1

    return errors if errors else ["Compilation failed (no specific error found in log)"]


def build(
    tex_path: Path,
    engine: str | None = None,
    outdir: Path | None = None,
    runs: int = 2,
) -> bool:
    """Compile a .tex file to PDF. Returns True on success."""
    tex_path = tex_path.resolve()
    if not tex_path.exists():
        print(f"Error: {tex_path} not found", file=sys.stderr)
        return False

    if engine is None:
        engine = detect_engine(tex_path)

    if outdir is None:
        outdir = tex_path.parent
    outdir.mkdir(parents=True, exist_ok=True)

    print(f"Engine: {engine}")
    print(f"Source: {tex_path}")
    print(f"Output: {outdir}/")
    print()

    for run in range(1, runs + 1):
        print(f"--- Run {run}/{runs} ---")
        result = subprocess.run(
            [
                engine,
                "-interaction=nonstopmode",
                f"-output-directory={outdir}",
                str(tex_path),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode != 0:
            log_path = outdir / (tex_path.stem + ".log")
            print(f" Compilation failed (exit code {result.returncode})", file=sys.stderr)
            errors = parse_log_errors(log_path)
            print("\nErrors:", file=sys.stderr)
            for err in errors:
                print(f"  {err}", file=sys.stderr)
            return False

        print(" OK")

    # Report output
    pdf_path = outdir / (tex_path.stem + ".pdf")
    if pdf_path.exists():
        size_kb = pdf_path.stat().st_size / 1024
        print(f"\nSuccess: {pdf_path} ({size_kb:.1f} KB)")
    else:
        print(f"\nWarning: PDF not found at {pdf_path}")

    return True


def main():
    parser = argparse.ArgumentParser(description="Build LaTeX templates to PDF")
    parser.add_argument("template", help="Path to .tex file")
    parser.add_argument(
        "--engine", choices=["pdflatex", "lualatex", "xelatex"], help="LaTeX engine (auto-detected if omitted)"
    )
    parser.add_argument("--outdir", help="Output directory (default: same as source)")
    parser.add_argument("--runs", type=int, default=2, help="Number of compilation passes (default: 2)")
    args = parser.parse_args()

    tex_path = Path(args.template)
    outdir = Path(args.outdir) if args.outdir else None

    success = build(tex_path, engine=args.engine, outdir=outdir, runs=args.runs)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
