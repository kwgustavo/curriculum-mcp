"""Pipeline orchestrator.

End-to-end flow:
  1. tailor_profile() — LLM scores + lightly rewrites for the role
  2. write pre.txt + tailored.json + requirements.json to <application_dir>/
  3. HUMAN GATE: prompt the user (or read the cached approval) before rendering
  4. iterate: render → verify → drop lowest-relevance items on overflow →
     re-render, up to max_iterations
  5. copy final cv.pdf + cv.tex + screenshot into <application_dir>/
  6. write log.md with per-iteration results
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import verifier
from .llm_client import LLMClient
from .profile import save_profile
from .render_text import write_pre_text
from .renderer import render_and_build
from .schemas import (
    PipelineConfig,
    Profile,
    Requirements,
    TailoredProfile,
    VerificationResult,
)
from .tailor import tailor_profile


# ── Application directory layout ─────────────────────────────────────────────


@dataclass
class ApplicationPaths:
    root: Path

    @property
    def inputs(self) -> Path:
        return self.root / "inputs"

    @property
    def profile_path(self) -> Path:
        return self.inputs / "profile.yaml"

    @property
    def requirements_raw(self) -> Path:
        return self.inputs / "requirements.txt"

    @property
    def requirements_json(self) -> Path:
        return self.inputs / "requirements.json"

    @property
    def tailored_json(self) -> Path:
        return self.root / "tailored.json"

    @property
    def pre_text(self) -> Path:
        return self.root / "pre.txt"

    @property
    def approval_file(self) -> Path:
        return self.root / ".approved"

    @property
    def cv_tex(self) -> Path:
        return self.root / "cv.tex"

    @property
    def cv_pdf(self) -> Path:
        return self.root / "cv.pdf"

    @property
    def cv_jpg(self) -> Path:
        return self.root / "cv_p0.jpg"

    @property
    def build_log(self) -> Path:
        return self.root / "cv.build.log"

    @property
    def log_md(self) -> Path:
        return self.root / "log.md"

    @property
    def history(self) -> Path:
        return self.root / "history"


def init_application_dir(
    application_dir: str | Path,
    profile: Profile,
    requirements_raw_path: str | Path,
) -> ApplicationPaths:
    """Create the application dir, copy inputs, return paths helper."""
    paths = ApplicationPaths(Path(application_dir))
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.inputs.mkdir(parents=True, exist_ok=True)

    profile_dst = paths.profile_path
    if not profile_dst.exists():
        save_profile(profile, profile_dst)

    req_dst = paths.requirements_raw
    if not req_dst.exists():
        shutil.copy2(Path(requirements_raw_path), req_dst)

    return paths


def stage_tailored_artifacts(
    paths: ApplicationPaths,
    tailored: TailoredProfile,
    requirements: Requirements,
) -> None:
    paths.tailored_json.write_text(
        json.dumps(tailored.model_dump(exclude_none=True), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    paths.requirements_json.write_text(
        json.dumps(requirements.model_dump(exclude={"raw_text"}), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    write_pre_text(tailored, paths.pre_text)


# ── Iteration loop ──────────────────────────────────────────────────────────


def _save_iter_snapshot(paths: ApplicationPaths, iter_no: int) -> Path:
    iter_dir = paths.history / f"v{iter_no:02d}"
    iter_dir.mkdir(parents=True, exist_ok=True)
    if paths.cv_tex.exists():
        shutil.copy2(paths.cv_tex, iter_dir / "cv.tex")
    if paths.cv_pdf.exists():
        shutil.copy2(paths.cv_pdf, iter_dir / "cv.pdf")
    if paths.cv_jpg.exists():
        shutil.copy2(paths.cv_jpg, iter_dir / "cv_p0.jpg")
    if paths.tailored_json.exists():
        shutil.copy2(paths.tailored_json, iter_dir / "tailored.json")
    return iter_dir


def _append_log(paths: ApplicationPaths, lines: list[str]) -> None:
    with paths.log_md.open("a", encoding="utf-8") as f:
        for line in lines:
            f.write(line.rstrip("\n") + "\n")


def run_render_loop(
    paths: ApplicationPaths,
    config: PipelineConfig,
    requirements: Requirements,
    llm: LLMClient | None,
    base_tailored: TailoredProfile,
) -> tuple[VerificationResult, int]:
    """Iterate render → verify → trim-by-relevance → repeat."""
    tailored = base_tailored.model_copy(deep=True)
    if paths.log_md.exists():
        paths.log_md.unlink()

    _append_log(
        paths,
        [
            f"# CV iteration log",
            "",
            f"- Template: `{config.template}`",
            f"- Max iterations: {config.max_iterations}",
            f"- Vision enabled: {config.vision_enabled}",
            f"- Vision pages: {config.vision_pages}",
            f"- Max page count: {config.max_page_count}",
            f"- Model (text): `{config.text_model}`",
            f"- Model (vision): `{config.vision_model}`",
            "",
        ],
    )

    last_result: VerificationResult | None = None
    last_iter = 0
    for i in range(1, config.max_iterations + 1):
        last_iter = i
        _append_log(paths, [f"## Iteration {i}", ""])
        try:
            tex_path, pdf_path, _log = render_and_build(
                tailored,
                config.template,
                paths.root,
                templates_dir=None,
            )
        except Exception as e:
            err = f"LaTeX build failed: {e}"
            _append_log(paths, [f"- ❌ {err}", ""])
            last_result = VerificationResult(
                passed=False,
                page_count=0,
                issues=[],
                notes=[err],
            )
            break

        result = verifier.verify(
            pdf_path,
            requirements,
            llm=llm,
            use_vision=config.vision_enabled,
            vision_model=config.vision_model,
            pages=config.vision_pages,
            max_pages=config.max_page_count,
        )

        # Log vision failures as warnings
        for note in result.notes:
            if "Vision check failed" in note:
                _append_log(paths, [f"⚠ {note}"])

        issue_lines = [
            f"- page_count={result.page_count}, passed={result.passed}"
        ]
        for iss in result.issues:
            page_tag = f" p{iss.page}" if iss.page is not None else ""
            issue_lines.append(
                f"  - [{iss.severity}/{iss.area}{page_tag}] {iss.description}"
            )
            if iss.suggested_fix:
                issue_lines.append(f"      fix: {iss.suggested_fix}")
        _append_log(paths, issue_lines + [""])

        _save_iter_snapshot(paths, i)

        last_result = result
        if result.passed:
            _append_log(paths, [f"✅ Passed on iteration {i}", ""])
            break

        if i < config.max_iterations:
            layout_issues = sum(1 for x in result.issues if x.area == "layout")
            page_overflow = 1 if result.page_count > config.max_page_count else 0
            drops = max(1, layout_issues + page_overflow * 2)
            drops = min(drops, 3)

            if result.page_count > config.max_page_count:
                _append_log(
                    paths,
                    [f"⚠ Over page limit ({result.page_count} > {config.max_page_count}); "
                     f"dropping {drops} items and retrying"],
                )

            before = sum(x.included for x in tailored.experience + tailored.skills + tailored.education)
            tailored.drop_least_relevant(k=drops)
            after = sum(x.included for x in tailored.experience + tailored.skills + tailored.education)
            _append_log(
                paths,
                [f"- dropped {before - after} lowest-relevance item(s); retrying", ""],
            )
            paths.tailored_json.write_text(
                json.dumps(
                    tailored.model_dump(exclude_none=True), indent=2, ensure_ascii=False
                ),
                encoding="utf-8",
            )

    return last_result or VerificationResult(passed=False, page_count=0), last_iter


# ── Public entry points ─────────────────────────────────────────────────────


def run_tailor(
    profile: Profile,
    requirements: Requirements,
    paths: ApplicationPaths,
    llm: LLMClient,
    *,
    threshold: float = 0.3,
    model: str | None = None,
) -> TailoredProfile:
    tailored = tailor_profile(
        profile,
        requirements,
        llm,
        threshold=threshold,
        model=model,
    )
    stage_tailored_artifacts(paths, tailored, requirements)
    return tailored


def run_pipeline(
    profile_path: str | Path,
    requirements_path: str | Path,
    config: PipelineConfig,
    *,
    interactive: bool = True,
    auto_approve: bool = False,
    llm: LLMClient | None = None,
) -> Path:
    """End-to-end: load → tailor → gate → render loop → finalize.

    Returns the final cv.pdf path.
    """
    from .profile import load_profile
    from .requirements import load_or_extract

    llm = llm or LLMClient()

    profile = load_profile(profile_path)
    requirements = load_or_extract(requirements_path, llm)

    paths = init_application_dir(
        config.application_dir, profile, requirements_path
    )

    tailored = run_tailor(
        profile, requirements, paths, llm,
        threshold=0.3, model=config.text_model,
    )

    if interactive and not auto_approve:
        approve = _human_gate(paths)
        if approve == "quit":
            raise SystemExit(0)
        if approve == "regen":
            _append_log(paths, ["User requested re-tailor; aborting."])
            raise SystemExit(0)
    else:
        paths.approval_file.write_text("auto-approved", encoding="utf-8")

    result, iters = run_render_loop(
        paths, config, requirements, llm, tailored
    )

    final_pdf = paths.cv_pdf
    _append_log(
        paths,
        [
            "## Summary",
            "",
            f"- iterations: {iters}",
            f"- final pdf: `{final_pdf}`",
            f"- passed: {result.passed}",
            f"- issues: {len(result.issues)}",
            "",
        ],
    )
    return final_pdf


def _human_gate(paths: ApplicationPaths) -> str:
    print()
    print("─" * 60)
    print("Tailored draft written:")
    print(f"  - {paths.pre_text}")
    print(f"  - {paths.tailored_json}")
    print(f"  - {paths.requirements_json}")
    print()
    print("Please review pre.txt and tailored.json, then choose:")
    print("  [a] accept and proceed to PDF rendering")
    print("  [e] edit tailored.json then re-run with --approved flag")
    print("  [q] quit (run later: python -m curriculum_pipeline render ...)")
    print("─" * 60)
    while True:
        try:
            choice = input("Your choice [a/e/q]: ").strip().lower()
        except EOFError:
            return "quit"
        if choice in {"a", "accept"}:
            paths.approval_file.write_text("accepted", encoding="utf-8")
            return "accept"
        if choice in {"e", "edit"}:
            print("Edit tailored.json, then re-run with --approved.")
            return "quit"
        if choice in {"q", "quit"}:
            return "quit"
        print("Please enter a, e, or q.")
