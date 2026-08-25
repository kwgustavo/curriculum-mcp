"""CLI for the curriculum builder pipeline.

Subcommands:
    tailor   Run LLM tailoring and write pre.txt + tailored.json (gate output)
    render   Run the render → verify → iterate loop, given an approved application dir
    build    One-shot: tailor → gate → render loop
    list     List available templates (those with schema.yaml)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import pipeline
from .llm_client import LLMClient
from .profile import load_profile
from .requirements import load_or_extract
from .schemas import PipelineConfig
from .template_schema import discover_templates, load_template_schema


def cmd_list(args: argparse.Namespace) -> int:
    names = discover_templates()
    if not names:
        print("No templates with schema.yaml found under templates/")
        return 1
    print("Available templates:")
    for n in names:
        try:
            schema = load_template_schema(n)
            placeholders = ", ".join(schema.placeholder_names())
            print(f"  - {n}  (placeholders: {placeholders})")
        except Exception as e:
            print(f"  - {n}  (error: {e})")
    return 0


def cmd_tailor(args: argparse.Namespace) -> int:
    llm = LLMClient()
    profile = load_profile(args.profile)
    requirements = load_or_extract(args.requirements, llm, model=args.model)
    paths = pipeline.init_application_dir(args.output, profile, args.requirements)
    tailored = pipeline.run_tailor(
        profile, requirements, paths, llm,
        threshold=args.threshold, model=args.model,
    )
    print(f"\n✓ Tailored profile saved to: {paths.tailored_json}")
    print(f"✓ Draft saved to:           {paths.pre_text}")
    print(f"✓ Requirements saved to:    {paths.requirements_json}")
    if tailored.tailoring_notes:
        print("\nTailoring notes:")
        for n in tailored.tailoring_notes:
            print(f"  - {n}")
    print("\nNext: review pre.txt, then run:")
    print(f"  python -m curriculum_pipeline render --application {args.output} --template {args.template}")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    llm = LLMClient() if args.vision else None
    paths = pipeline.ApplicationPaths(Path(args.application))
    if not paths.tailored_json.exists():
        print(
            f"✗ No tailored.json found at {paths.tailored_json}. "
            f"Run 'tailor' first.",
            file=sys.stderr,
        )
        return 1
    if not paths.approval_file.exists() and not args.approved:
        print(
            f"✗ Application not approved. Review {paths.pre_text} and "
            f"{paths.tailored_json}, then re-run with --approved.",
            file=sys.stderr,
        )
        return 1

    tailored_data = json.loads(paths.tailored_json.read_text(encoding="utf-8"))
    from .schemas import TailoredProfile
    tailored = TailoredProfile.model_validate(tailored_data)

    requirements = None
    if paths.requirements_json.exists():
        from .schemas import Requirements
        requirements = Requirements.model_validate(
            json.loads(paths.requirements_json.read_text(encoding="utf-8"))
        )

    config = PipelineConfig(
        application_dir=args.application,
        template=args.template,
        max_iterations=args.max_iterations,
        vision_enabled=args.vision,
        vision_model=args.vision_model or "gpt-4o",
        text_model=args.model or "gpt-4o-mini",
    )
    result, iters = pipeline.run_render_loop(
        paths, config, requirements, llm, tailored,
    )
    status = "✓ passed" if result.passed else "✗ not converged"
    print(f"\n{status} after {iters} iteration(s).")
    print(f"PDF: {paths.cv_pdf}")
    return 0 if result.passed else 2


def cmd_build(args: argparse.Namespace) -> int:
    config = PipelineConfig(
        application_dir=args.output,
        template=args.template,
        max_iterations=args.max_iterations,
        vision_enabled=args.vision,
        vision_model=args.vision_model or "gpt-4o",
        text_model=args.model or "gpt-4o-mini",
    )
    pdf = pipeline.run_pipeline(
        args.profile,
        args.requirements,
        config,
        interactive=not args.no_interactive,
        auto_approve=args.auto_approve,
    )
    print(f"\n✓ Final PDF: {pdf}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="curriculum_pipeline",
        description="Generate role-tailored single-page CVs from a profile + job description.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="List available templates")
    p_list.set_defaults(func=cmd_list)

    p_tailor = sub.add_parser("tailor", help="Tailor a profile for a job and write pre.txt")
    p_tailor.add_argument("--profile", required=True, help="Path to profile.yaml/json")
    p_tailor.add_argument("--requirements", required=True, help="Path to requirements.txt")
    p_tailor.add_argument("--template", default="cv", help="Template name (default: cv)")
    p_tailor.add_argument("--output", required=True, help="Application output directory")
    p_tailor.add_argument("--threshold", type=float, default=0.3, help="Relevance threshold for inclusion")
    p_tailor.add_argument("--model", default=None, help="LLM model override")
    p_tailor.set_defaults(func=cmd_tailor)

    p_render = sub.add_parser("render", help="Run render → verify → iterate loop")
    p_render.add_argument("--application", required=True, help="Application directory")
    p_render.add_argument("--template", default="cv", help="Template name (default: cv)")
    p_render.add_argument("--max-iterations", type=int, default=5)
    p_render.add_argument("--vision", action="store_true", help="Enable vision-based checks")
    p_render.add_argument("--vision-model", default=None)
    p_render.add_argument("--model", default=None, help="LLM text model override")
    p_render.add_argument("--approved", action="store_true", help="Skip human gate (assume approved)")
    p_render.set_defaults(func=cmd_render)

    p_build = sub.add_parser("build", help="One-shot: tailor → gate → render loop")
    p_build.add_argument("--profile", required=True)
    p_build.add_argument("--requirements", required=True)
    p_build.add_argument("--template", default="cv")
    p_build.add_argument("--output", required=True)
    p_build.add_argument("--max-iterations", type=int, default=5)
    p_build.add_argument("--vision", action="store_true")
    p_build.add_argument("--vision-model", default=None)
    p_build.add_argument("--model", default=None)
    p_build.add_argument("--no-interactive", action="store_true", help="Skip human gate prompts")
    p_build.add_argument("--auto-approve", action="store_true", help="Approve without prompting (implies --no-interactive)")
    p_build.set_defaults(func=cmd_build)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
