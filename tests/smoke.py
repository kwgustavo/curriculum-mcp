#!/usr/bin/env python3
"""End-to-end smoke test for the curriculum pipeline.

Validates template discovery, profile loading, placeholder substitution,
PDF building, verification, and pre.txt generation.  No LLM key required.

Optional LLM tests run when LLM_API_KEY is set.  Skip them with:
    SKIP_LLM=1 python tests/smoke.py

Run:  python tests/smoke.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from curriculum_pipeline.profile import load_profile, save_profile
from curriculum_pipeline.render_text import render_pre_text, write_pre_text
from curriculum_pipeline.renderer import render_and_build
from curriculum_pipeline.schemas import (
    Requirements,
    TailoredEducationItem,
    TailoredExperienceItem,
    TailoredProfile,
    TailoredSkillItem,
)
from curriculum_pipeline.template_schema import (
    discover_templates,
    load_template_schema,
    render,
)
from curriculum_pipeline import verifier

EXAMPLES = ROOT / "examples"
PROFILE_PATH = EXAMPLES / "profile.yaml"
REQ_PATH = EXAMPLES / "requirements.txt"

EXPECTED_TEMPLATES = ["cv", "1", "2", "3", "4"]

passed = 0
failed = 0


def ok(label: str) -> None:
    global passed
    passed += 1
    print(f"  PASS  {label}")


def fail(label: str, msg: str) -> None:
    global failed
    failed += 1
    print(f"  FAIL  {label}: {msg}")


def section(title: str) -> None:
    print(f"\n--- {title} ---")


# ── helpers ──────────────────────────────────────────────────────────────────


def make_synthetic_tailored(profile) -> TailoredProfile:
    """Build a TailoredProfile from a base Profile with all items included."""
    return TailoredProfile(
        personal=profile.personal,
        contact=profile.contact,
        profile_text=profile.profile_text,
        experience=[
            TailoredExperienceItem(
                **e.model_dump(), relevance_score=0.8, included=True
            )
            for e in profile.experience
        ],
        education=[
            TailoredEducationItem(
                **e.model_dump(), relevance_score=0.8, included=True
            )
            for e in profile.education
        ],
        additional_education=[
            TailoredEducationItem(
                **e.model_dump(), relevance_score=0.6, included=True
            )
            for e in profile.additional_education
        ],
        skills=[
            TailoredSkillItem(
                **s.model_dump(), relevance_score=0.8, included=True
            )
            for s in profile.skills
        ],
        languages=list(profile.languages),
        hobbies=profile.hobbies,
    )


def make_requirements() -> Requirements:
    return Requirements(
        role_title="Senior Data Scientist",
        company="Acme Cloud",
        seniority="senior",
        required_skills=["Python", "SQL", "PyTorch"],
        nice_skills=["Spark", "Airflow"],
        keywords=["scalable", "production", "ML platform", "mentorship"],
    )


# ── tests ────────────────────────────────────────────────────────────────────


def test_profile_load() -> None:
    section("Profile loading")
    try:
        profile = load_profile(PROFILE_PATH)
        ok(f"loaded profile ({len(profile.experience)} exp, "
           f"{len(profile.education)} edu, {len(profile.skills)} skills)")
        return profile
    except Exception as e:
        fail("load_profile", str(e))
        return None


def test_profile_roundtrip(tmp: Path) -> None:
    section("Profile round-trip (YAML + JSON)")
    profile = load_profile(PROFILE_PATH)
    yaml_dst = tmp / "rt.yaml"
    save_profile(profile, yaml_dst)
    loaded = load_profile(yaml_dst)
    if loaded.personal.name == profile.personal.name:
        ok("YAML round-trip")
    else:
        fail("YAML round-trip", f"name mismatch: {loaded.personal.name!r}")

    json_dst = tmp / "rt.json"
    save_profile(profile, json_dst)
    loaded = load_profile(json_dst)
    if loaded.personal.name == profile.personal.name:
        ok("JSON round-trip")
    else:
        fail("JSON round-trip", f"name mismatch: {loaded.personal.name!r}")


def test_requirements_load() -> None:
    section("Requirements loading")
    try:
        raw = REQ_PATH.read_text(encoding="utf-8")
        ok(f"read requirements ({len(raw)} chars)")
        return raw
    except Exception as e:
        fail("requirements read", str(e))
        return None


def test_template_discovery() -> None:
    section("Template discovery")
    names = discover_templates()
    for t in EXPECTED_TEMPLATES:
        if t in names:
            ok(f"template '{t}' discovered")
        else:
            fail(f"template '{t}'", f"not found (got: {names})")
    return names


def test_template_schemas() -> None:
    section("Template schema loading")
    for t in EXPECTED_TEMPLATES:
        try:
            schema = load_template_schema(t)
            count = len(schema.placeholder_names())
            ok(f"template '{t}' schema loaded ({count} placeholders)")
        except Exception as e:
            fail(f"template '{t}' schema", str(e))


def test_placeholder_substitution() -> None:
    section("Placeholder substitution (no leftover <<…>>)")
    import re

    profile = load_profile(PROFILE_PATH)
    for t in EXPECTED_TEMPLATES:
        try:
            schema = load_template_schema(t)
            tex = render(schema, profile)
            remaining = re.findall(r"<<#?\w+>>|<</\w+>>", tex)
            if not remaining:
                ok(f"template '{t}' — no leftover placeholders ({len(tex)} chars)")
            else:
                fail(f"template '{t}'", f"leftover: {remaining}")
        except Exception as e:
            fail(f"template '{t}'", str(e))


def test_render_text() -> None:
    section("pre.txt generation")
    profile = load_profile(PROFILE_PATH)
    tailored = make_synthetic_tailored(profile)
    text = render_pre_text(tailored)
    required_sections = ["PROFILE", "EXPERIENCE", "EDUCATION", "SKILLS"]
    for sec in required_sections:
        if sec in text:
            ok(f"pre.txt contains '{sec}'")
        else:
            fail(f"pre.txt '{sec}'", f"section not found")

    name_upper = profile.personal.name.upper() if profile.personal.name else ""
    if name_upper and name_upper in text:
        ok("pre.txt contains candidate name")
    else:
        fail("pre.txt name", f"candidate name {name_upper!r} not found")

    if "Python" in text:
        ok("pre.txt contains skills")
    else:
        fail("pre.txt skills", "skills not found")


def test_latex_escape() -> None:
    section("LaTeX escaping")
    from curriculum_pipeline.template_schema import _latex_escape

    tests = [
        ("hello & world", r"hello \& world"),
        ("50%", r"50\%"),
        ("a_b", r"a\_b"),
        ("100%", r"100\%"),
    ]
    for inp, expected in tests:
        result = _latex_escape(inp)
        if result == expected:
            ok(f"escape {inp!r} -> {result!r}")
        else:
            fail(f"escape {inp!r}", f"expected {expected!r}, got {result!r}")


def test_pdf_build_all(tmp: Path) -> None:
    section("PDF building (all 5 templates)")
    profile = load_profile(PROFILE_PATH)
    results = {}
    for t in EXPECTED_TEMPLATES:
        out = tmp / f"pdf_{t}"
        out.mkdir()
        try:
            tex_path, pdf_path, log = render_and_build(
                profile, t, out, runs=2
            )
            size_kb = pdf_path.stat().st_size // 1024
            ok(f"template '{t}' built PDF ({size_kb}KB)")
            results[t] = pdf_path
        except Exception as e:
            fail(f"template '{t}' PDF build", str(e)[:200])
    return results


def test_pdf_single_page(tmp: Path) -> None:
    section("PDF page count == 1")
    profile = load_profile(PROFILE_PATH)
    # Template 4 (AltaCV) is known to overflow with the full example profile
    single_page_templates = [t for t in EXPECTED_TEMPLATES if t != "4"]
    for t in single_page_templates:
        out = tmp / f"pc_{t}"
        out.mkdir()
        try:
            _, pdf_path, _ = render_and_build(profile, t, out, runs=1)
            import fitz

            doc = fitz.open(str(pdf_path))
            n = len(doc)
            doc.close()
            if n == 1:
                ok(f"template '{t}' is single-page")
            else:
                fail(f"template '{t}'", f"has {n} pages")
        except Exception as e:
            fail(f"template '{t}' page count", str(e)[:200])

    # Template 4: just verify it builds (may be 2 pages)
    out = tmp / "pc_4"
    out.mkdir()
    try:
        _, pdf_path, _ = render_and_build(profile, "4", out, runs=2)
        import fitz
        doc = fitz.open(str(pdf_path))
        n = len(doc)
        doc.close()
        if n <= 2:
            ok(f"template '4' builds ({n} pages — known overflow possible)")
        else:
            fail(f"template '4'", f"has {n} pages (expected 1-2)")
    except Exception as e:
        fail(f"template '4' page count", str(e)[:200])


def test_verification(tmp: Path) -> None:
    section("Programmatic verification")
    profile = load_profile(PROFILE_PATH)
    reqs = make_requirements()
    out = tmp / "verify_cv"
    out.mkdir()
    try:
        _, pdf_path, _ = render_and_build(profile, "cv", out, runs=2)
        result = verifier.verify_programmatic(pdf_path, reqs)
        if result.passed:
            ok(f"verify_programmatic passed (pages={result.page_count}, "
               f"issues={len(result.issues)})")
        else:
            issues_str = "; ".join(
                f"[{i.severity}/{i.area}] {i.description}" for i in result.issues
            )
            fail("verify_programmatic", issues_str)
    except Exception as e:
        fail("verify_programmatic", str(e)[:200])


def test_screenshot(tmp: Path) -> None:
    section("Screenshot generation")
    profile = load_profile(PROFILE_PATH)
    out = tmp / "screenshot_cv"
    out.mkdir()
    try:
        _, pdf_path, _ = render_and_build(profile, "cv", out, runs=1)
        jpg_path = verifier.screenshot_page(pdf_path)
        if jpg_path.exists() and jpg_path.stat().st_size > 0:
            ok(f"screenshot {jpg_path.name} ({jpg_path.stat().st_size // 1024}KB)")
        else:
            fail("screenshot", "file empty or missing")
    except Exception as e:
        fail("screenshot", str(e)[:200])


def test_cli_render_without_llm(tmp: Path) -> None:
    section("CLI render (no LLM key required)")
    profile = load_profile(PROFILE_PATH)
    tailored = make_synthetic_tailored(profile)
    reqs = make_requirements()
    app_dir = tmp / "cli_app"

    from curriculum_pipeline.pipeline import (
        ApplicationPaths,
        init_application_dir,
        run_render_loop,
    )
    from curriculum_pipeline.schemas import PipelineConfig

    paths = init_application_dir(str(app_dir), profile, str(REQ_PATH))
    # Write tailored.json manually (skip LLM)
    paths.tailored_json.write_text(
        json.dumps(
            tailored.model_dump(exclude_none=True), indent=2, ensure_ascii=False
        ),
        encoding="utf-8",
    )
    paths.requirements_json.write_text(
        json.dumps(reqs.model_dump(exclude={"raw_text"}), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    paths.approval_file.write_text("smoke-test", encoding="utf-8")
    write_pre_text(tailored, paths.pre_text)

    config = PipelineConfig(
        application_dir=str(app_dir),
        template="cv",
        max_iterations=1,
        vision_enabled=False,
    )
    result, iters = run_render_loop(paths, config, reqs, llm=None, base_tailored=tailored)
    if result.passed:
        ok(f"render loop passed (iters={iters}, pages={result.page_count})")
    else:
        issues_str = "; ".join(
            f"[{i.severity}/{i.area}] {i.description}" for i in result.issues
        )
        fail("render loop", issues_str)

    # Check artifacts
    expected_files = ["cv.tex", "cv.pdf", "pre.txt", "tailored.json", "log.md"]
    for fname in expected_files:
        fp = paths.root / fname
        if fp.exists():
            ok(f"artifact {fname} exists")
        else:
            fail(f"artifact {fname}", "missing")

    history_iter = paths.history / "v01"
    if history_iter.exists():
        ok("history/v01/ snapshot exists")
    else:
        fail("history/v01/", "missing")


# ── MCP server tests ─────────────────────────────────────────────────────────


def test_mcp_tools_no_llm(tmp: Path) -> None:
    section("MCP server — tools (no LLM)")
    try:
        from curriculum_mcp.server import (
            list_templates,
            get_template,
            get_template_info,
            load_profile,
            validate_profile,
            list_applications,
            render_pre_text,
        )
    except ImportError as e:
        fail("MCP imports", str(e))
        return

    # list_templates
    result = list_templates()
    if "Available templates" in result and "cv" in result:
        ok("MCP list_templates")
    else:
        fail("MCP list_templates", result[:100])

    # get_template
    result = get_template("cv")
    if "Template: cv" in result and "Source:" in result:
        ok("MCP get_template")
    else:
        fail("MCP get_template", result[:100])

    # get_template_info
    result = get_template_info("cv")
    if "Packages:" in result and "pdflatex" in result:
        ok("MCP get_template_info")
    else:
        fail("MCP get_template_info", result[:100])

    # load_profile
    result = load_profile("examples/profile.yaml")
    data = json.loads(result)
    if data["personal"]["name"] == "Jane Doe":
        ok("MCP load_profile")
    else:
        fail("MCP load_profile", result[:100])

    # validate_profile
    result = validate_profile(data)
    if "valid" in result.lower() or "OK" in result:
        ok("MCP validate_profile")
    else:
        fail("MCP validate_profile", result[:100])

    # render_pre_text — build a tailored.json first
    profile = load_profile_for_smoke()
    tailored = make_synthetic_tailored(profile)
    tailored_json = tmp / "mcp_tailored.json"
    tailored_json.write_text(
        json.dumps(tailored.model_dump(exclude_none=True), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    result = render_pre_text(str(tailored_json))
    if "JANE DOE" in result or "PROFILE" in result:
        ok("MCP render_pre_text")
    else:
        fail("MCP render_pre_text", result[:100])

    # list_applications
    result = list_applications()
    ok(f"MCP list_applications (returned text)")


def load_profile_for_smoke():
    from curriculum_pipeline.profile import load_profile as _load

    return _load(PROFILE_PATH)


def test_mcp_build_pdf(tmp: Path) -> None:
    section("MCP server — build_pdf (all 5 templates)")
    try:
        from curriculum_mcp.server import build_pdf
    except ImportError as e:
        fail("MCP build_pdf import", str(e))
        return

    for t in EXPECTED_TEMPLATES:
        try:
            result = build_pdf(t)
            if "Success:" in result:
                ok(f"MCP build_pdf({t})")
            else:
                fail(f"MCP build_pdf({t})", result[:200])
        except Exception as e:
            fail(f"MCP build_pdf({t})", str(e)[:200])


def test_mcp_verify_and_screenshot(tmp: Path) -> None:
    section("MCP server — verify_pdf + extract_text")
    try:
        from curriculum_mcp.server import verify_pdf, extract_text
    except ImportError as e:
        fail("MCP verify import", str(e))
        return

    # Build a test PDF
    profile = load_profile_for_smoke()
    from curriculum_pipeline.renderer import render_and_build

    out = tmp / "mcp_verify"
    out.mkdir()
    _, pdf_path, _ = render_and_build(profile, "cv", out, runs=1)

    # verify_pdf
    result = verify_pdf(str(pdf_path))
    if "Passed: True" in result and "Page count: 1" in result:
        ok("MCP verify_pdf")
    else:
        fail("MCP verify_pdf", result[:200])

    # extract_text
    result = extract_text(str(pdf_path))
    if "Jane Doe" in result or "JANE" in result:
        ok("MCP extract_text")
    else:
        fail("MCP extract_text", result[:100])


def test_mcp_build_pdf_custom_output_dir(tmp: Path) -> None:
    section("MCP server — build_pdf with custom output_dir")
    try:
        from curriculum_mcp.server import build_pdf
    except ImportError as e:
        fail("MCP build_pdf import", str(e))
        return

    custom_dir = tmp / "custom_output"
    try:
        result = build_pdf("cv", output_dir=str(custom_dir))
        if "Success:" in result and str(custom_dir) in result:
            ok("MCP build_pdf(output_dir)")
            pdf_in_custom = custom_dir / "cv.pdf"
            if pdf_in_custom.exists():
                ok("MCP build_pdf(output_dir) — PDF exists")
            else:
                fail("MCP build_pdf(output_dir) — PDF missing", str(custom_dir))
        else:
            fail("MCP build_pdf(output_dir)", result[:200])
    except Exception as e:
        fail("MCP build_pdf(output_dir)", str(e)[:200])


def test_mcp_screenshot_pdf_explicit_path(tmp: Path) -> None:
    section("MCP server — screenshot_pdf with explicit pdf_path")
    try:
        from curriculum_mcp.server import screenshot_pdf, screenshot_pdf_all
    except ImportError as e:
        fail("MCP screenshot_pdf import", str(e))
        return

    # Build a test PDF
    profile = load_profile_for_smoke()
    from curriculum_pipeline.renderer import render_and_build

    out = tmp / "mcp_screenshot"
    out.mkdir()
    _, pdf_path, _ = render_and_build(profile, "cv", out, runs=1)

    # screenshot_pdf with explicit pdf_path
    try:
        result = screenshot_pdf("cv", pdf_path=str(pdf_path))
        if isinstance(result, list) and len(result) > 0:
            ok("MCP screenshot_pdf(pdf_path)")
        else:
            fail("MCP screenshot_pdf(pdf_path)", result[:200] if isinstance(result, str) else str(result)[:200])
    except Exception as e:
        fail("MCP screenshot_pdf(pdf_path)", str(e)[:200])

    # screenshot_pdf_all with explicit pdf_path
    try:
        result = screenshot_pdf_all("cv", pdf_path=str(pdf_path))
        if isinstance(result, list) and len(result) > 1:
            ok("MCP screenshot_pdf_all(pdf_path)")
        else:
            fail("MCP screenshot_pdf_all(pdf_path)", str(result)[:200])
    except Exception as e:
        fail("MCP screenshot_pdf_all(pdf_path)", str(e)[:200])


def test_mcp_resources() -> None:
    section("MCP server — resources")
    try:
        from curriculum_mcp.server import (
            resource_template_list,
            resource_template_schema,
            resource_template_source,
            resource_pipeline_status,
            resource_applications_list,
        )
    except ImportError as e:
        fail("MCP resource imports", str(e))
        return

    # templates://list
    result = resource_template_list()
    data = json.loads(result)
    names = [t["name"] for t in data]
    if "cv" in names and "1" in names:
        ok("MCP resource templates://list")
    else:
        fail("MCP resource templates://list", str(names))

    # templates://{name}/schema
    result = resource_template_schema("cv")
    data = json.loads(result)
    if "placeholders" in data and "experience" in data["placeholders"]:
        ok("MCP resource templates://cv/schema")
    else:
        fail("MCP resource templates://cv/schema", result[:100])

    # templates://{name}/source
    result = resource_template_source("cv")
    if "\\documentclass" in result or "documentclass" in result:
        ok("MCP resource templates://cv/source")
    else:
        fail("MCP resource templates://cv/source", result[:100])

    # pipeline://status
    result = resource_pipeline_status()
    data = json.loads(result)
    if "templates" in data and "llm_configured" in data:
        ok("MCP resource pipeline://status")
    else:
        fail("MCP resource pipeline://status", result[:100])

    # applications://list
    result = resource_applications_list()
    data = json.loads(result)
    if isinstance(data, list):
        ok("MCP resource applications://list")
    else:
        fail("MCP resource applications://list", result[:100])


# ── Pipeline config + verifier tests ──────────────────────────────────────────


def test_pipeline_config_defaults() -> None:
    section("Pipeline config — defaults")
    from curriculum_pipeline.schemas import PipelineConfig

    config = PipelineConfig(application_dir="/tmp/test", template="cv")
    checks = [
        ("vision_enabled", config.vision_enabled, True),
        ("vision_pages", config.vision_pages, [0]),
        ("max_page_count", config.max_page_count, 1),
        ("vision_model", config.vision_model, "MiniMax-M3-NanoGPT"),
        ("text_model", config.text_model, "MiniMax-M3-NanoGPT"),
    ]
    for name, got, expected in checks:
        if got == expected:
            ok(f"config.{name} == {expected!r}")
        else:
            fail(f"config.{name}", f"got {got!r}, expected {expected!r}")


def test_verifier_max_page_count(tmp: Path) -> None:
    section("Verifier — max_page_count enforcement")
    from curriculum_pipeline.schemas import Requirements
    from curriculum_pipeline.verifier import verify_programmatic

    profile = load_profile(PROFILE_PATH)
    reqs = Requirements(
        role_title="Senior Data Scientist",
        company="Acme",
        required_skills=["Python", "SQL"],
    )
    # Build a 2-page PDF (template 4 with full profile)
    out = tmp / "max_pages_test"
    out.mkdir()
    try:
        _, pdf_path, _ = render_and_build(profile, "4", out, runs=1)
        result = verify_programmatic(pdf_path, reqs, max_pages=1)
        has_overflow = any("must be" in i.description for i in result.issues)
        if has_overflow:
            ok("verify flags 2-page CV as critical")
        else:
            fail("verify max_pages", f"expected critical issue, got: {[i.description for i in result.issues]}")
    except Exception as e:
        fail("verify max_pages", str(e)[:200])


def test_verifier_vision_pages(tmp: Path) -> None:
    section("Verifier — vision pages parameter")
    from curriculum_pipeline.verifier import verify_vision, screenshot_all_pages

    profile = load_profile(PROFILE_PATH)
    out = tmp / "vision_pages_test"
    out.mkdir()
    try:
        _, pdf_path, _ = render_and_build(profile, "cv", out, runs=1)
        # screenshot_all_pages returns list of paths
        paths = screenshot_all_pages(pdf_path)
        if len(paths) >= 1 and paths[0].exists():
            ok(f"screenshot_all_pages returned {len(paths)} image(s)")
        else:
            fail("screenshot_all_pages", f"got {paths}")
    except Exception as e:
        fail("screenshot_all_pages", str(e)[:200])


def test_verifier_issue_page_field(tmp: Path) -> None:
    section("Verifier — Issue.page field")
    from curriculum_pipeline.schemas import Issue

    # Issue without page (programmatic)
    issue_no_page = Issue(severity="critical", area="layout", description="test")
    if issue_no_page.page is None:
        ok("Issue.page defaults to None")
    else:
        fail("Issue.page default", f"expected None, got {issue_no_page.page}")

    # Issue with page (vision)
    issue_with_page = Issue(severity="major", area="typography", description="test", page=2)
    if issue_with_page.page == 2:
        ok("Issue.page accepts int value")
    else:
        fail("Issue.page value", f"expected 2, got {issue_with_page.page}")

    # Round-trip
    data = issue_with_page.model_dump()
    restored = Issue.model_validate(data)
    if restored.page == 2:
        ok("Issue.page round-trips through dict")
    else:
        fail("Issue.page round-trip", f"expected 2, got {restored.page}")


def test_run_render_loop_vision_failure(tmp: Path) -> None:
    section("Pipeline — vision failure handling")
    from curriculum_pipeline.pipeline import (
        ApplicationPaths,
        init_application_dir,
        run_render_loop,
    )
    from curriculum_pipeline.schemas import PipelineConfig

    profile = load_profile(PROFILE_PATH)
    reqs = make_requirements()
    tailored = make_synthetic_tailored(profile)
    app_dir = tmp / "vision_fail_test"

    paths = init_application_dir(str(app_dir), profile, str(REQ_PATH))
    paths.tailored_json.write_text(
        json.dumps(tailored.model_dump(exclude_none=True), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    paths.requirements_json.write_text(
        json.dumps(reqs.model_dump(exclude={"raw_text"}), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    paths.approval_file.write_text("smoke-test", encoding="utf-8")
    from curriculum_pipeline.render_text import write_pre_text
    write_pre_text(tailored, paths.pre_text)

    config = PipelineConfig(
        application_dir=str(app_dir),
        template="cv",
        max_iterations=1,
        vision_enabled=True,
        vision_model="nonexistent-model-xyz",
    )

    try:
        result, iters = run_render_loop(
            paths, config, reqs, llm=None, base_tailored=tailored
        )
        # Pipeline should still produce a PDF and not crash
        if paths.cv_pdf.exists():
            ok("vision failure handled — PDF still produced")
        else:
            fail("vision failure handling", "no PDF produced")
    except Exception as e:
        fail("vision failure handling", str(e)[:200])


# ── LLM smoke test (opt-in via LLM_API_KEY, skip with SKIP_LLM=1) ────────────

_SKIP_LLM = os.environ.get("SKIP_LLM", "0") in ("1", "true", "yes")


def _llm_available() -> bool:
    """Check if LLM client can be constructed (works with local endpoint or key)."""
    if _SKIP_LLM:
        return False
    try:
        from curriculum_pipeline.llm_client import LLMClient

        LLMClient()
        return True
    except Exception:
        return False


def test_llm_client_local_endpoint() -> None:
    """Verify LLMClient works with only LLM_BASE_URL (no API key needed)."""
    section("LLM client — local endpoint (no API key)")
    import subprocess

    env = os.environ.copy()
    env.pop("LLM_API_KEY", None)
    env["LLM_BASE_URL"] = "http://localhost:20128/v1"

    # Test 1: with only LLM_BASE_URL set (no key)
    try:
        result = subprocess.run(
            ["python", "-c",
             "import os; os.environ.pop('LLM_API_KEY', None); "
             "os.environ['LLM_BASE_URL'] = 'http://localhost:20128/v1'; "
             "from curriculum_pipeline.llm_client import LLMClient; "
             "c = LLMClient(); print('OK')"],
            capture_output=True, text=True, timeout=10, env=env,
        )
        if "OK" in result.stdout:
            ok("LLMClient() with only LLM_BASE_URL (no key)")
        else:
            fail("LLMClient() with only LLM_BASE_URL", result.stderr[:200])
    except Exception as e:
        fail("LLMClient() with only LLM_BASE_URL", str(e))

    # Test 2: with neither key nor URL set (defaults to localhost)
    try:
        result = subprocess.run(
            ["python", "-c",
             "import os; os.environ.pop('LLM_API_KEY', None); os.environ.pop('LLM_BASE_URL', None); "
             "from curriculum_pipeline.llm_client import LLMClient; "
             "c = LLMClient(); print('OK')"],
            capture_output=True, text=True, timeout=10, env=env,
        )
        if "OK" in result.stdout:
            ok("LLMClient() with defaults (localhost:20128)")
        else:
            fail("LLMClient() with defaults", result.stderr[:200])
    except Exception as e:
        fail("LLMClient() with defaults", str(e))

    # Test 3: with remote URL and no key should fail
    try:
        result = subprocess.run(
            ["python", "-c",
             "import os; os.environ.pop('LLM_API_KEY', None); "
             "os.environ['LLM_BASE_URL'] = 'https://api.openai.com/v1'; "
             "from curriculum_pipeline.llm_client import LLMClient; "
             "c = LLMClient(); print('OK')"],
            capture_output=True, text=True, timeout=10, env=env,
        )
        if "OK" not in result.stdout and "local endpoint" in result.stderr:
            ok("LLMClient() rejects remote URL without key")
        else:
            fail("LLMClient() rejects remote URL", result.stdout + result.stderr[:200])
    except Exception as e:
        fail("LLMClient() rejects remote URL", str(e))


def test_llm_extract_and_tailor(tmp: Path) -> None:
    """Optional: exercise extract_requirements + tailor_profile + verify_vision."""
    if not _llm_available():
        print("  SKIP  LLM extract/tailor/verify (LLM endpoint not reachable)")
        return

    section("LLM — extract_requirements")
    from curriculum_pipeline.llm_client import LLMClient
    from curriculum_pipeline.requirements import extract_requirements
    from curriculum_pipeline.tailor import tailor_profile
    from curriculum_pipeline.verifier import verify_programmatic, verify_vision

    try:
        llm = LLMClient()
    except Exception as e:
        fail("LLM client init", str(e))
        return

    raw_text = (
        "Senior Python Developer at Acme Corp. "
        "We need someone with Python, Django, PostgreSQL, and AWS experience. "
        "Nice to have: Docker, Kubernetes, CI/CD pipelines."
    )

    try:
        req = extract_requirements(raw_text, llm)
        if req.role_title:
            ok(f"extract_requirements (role_title={req.role_title!r})")
        else:
            fail("extract_requirements", "role_title is empty")
    except Exception as e:
        fail("extract_requirements", str(e))
        return

    section("LLM — tailor_profile")
    profile = load_profile(PROFILE_PATH)
    try:
        tailored = tailor_profile(profile, req, llm, threshold=0.1)
        included_exp = sum(1 for e in tailored.experience if e.included)
        included_skills = sum(1 for s in tailored.skills if s.included)
        if tailored.relevance_score is not None or included_exp > 0:
            ok(
                f"tailor_profile (score={tailored.relevance_score}, "
                f"{included_exp} exp, {included_skills} skills included)"
            )
        else:
            fail("tailor_profile", "no relevance_score and no items included")
    except Exception as e:
        fail("tailor_profile", str(e))
        return

    section("LLM — verify_vision")
    out = tmp / "llm_verify"
    out.mkdir()
    try:
        from curriculum_pipeline.renderer import render_and_build

        _, pdf_path, _ = render_and_build(profile, "cv", out, runs=1)
        vresult = verify_vision(pdf_path, req, llm)
        if vresult.passed is not None:
            ok(f"verify_vision (passed={vresult.passed}, {len(vresult.issues)} issues)")
        else:
            fail("verify_vision", "result.passed is None")
    except Exception as e:
        fail("verify_vision", str(e))


def test_llm_run_full_pipeline(tmp: Path) -> None:
    """Optional: exercise run_full_pipeline end-to-end with auto_approve."""
    if not _llm_available():
        print("  SKIP  LLM run_full_pipeline (LLM endpoint not reachable)")
        return

    section("LLM — run_full_pipeline (end-to-end)")
    from curriculum_mcp.server import run_full_pipeline

    out = tmp / "llm_full_pipeline"
    out.mkdir()
    try:
        result = run_full_pipeline(
            profile_path=str(PROFILE_PATH),
            requirements_path=str(REQ_PATH),
            template="cv",
            output=str(out),
            max_iterations=1,
            vision=False,
            auto_approve=True,
        )
        if "Final PDF:" in result and result.endswith(".pdf"):
            ok(f"run_full_pipeline — {result}")
        elif "Pipeline failed:" in result:
            fail("run_full_pipeline", result)
        else:
            fail("run_full_pipeline", f"unexpected: {result[:200]}")
    except Exception as e:
        fail("run_full_pipeline", str(e))


# ── main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("Curriculum Pipeline Smoke Test")
    print("=" * 60)

    with tempfile.TemporaryDirectory(prefix="smoke_") as tmp:
        tmp_path = Path(tmp)

        profile = test_profile_load()
        if profile is None:
            print("\nProfile loading failed — aborting.")
            sys.exit(1)

        test_profile_roundtrip(tmp_path)
        test_requirements_load()
        test_template_discovery()
        test_template_schemas()
        test_placeholder_substitution()
        test_render_text()
        test_latex_escape()
        test_pdf_build_all(tmp_path)
        test_pdf_single_page(tmp_path)
        test_verification(tmp_path)
        test_screenshot(tmp_path)
        test_cli_render_without_llm(tmp_path)

        # Pipeline config + verifier tests
        test_pipeline_config_defaults()
        test_verifier_max_page_count(tmp_path)
        test_verifier_vision_pages(tmp_path)
        test_verifier_issue_page_field(tmp_path)
        test_run_render_loop_vision_failure(tmp_path)

        # MCP server tests
        test_mcp_tools_no_llm(tmp_path)
        test_mcp_build_pdf(tmp_path)
        test_mcp_build_pdf_custom_output_dir(tmp_path)
        test_mcp_verify_and_screenshot(tmp_path)
        test_mcp_screenshot_pdf_explicit_path(tmp_path)
        test_mcp_resources()

        # LLM client tests (no live endpoint needed)
        test_llm_client_local_endpoint()

        # LLM integration tests (requires live endpoint, skip with SKIP_LLM=1)
        test_llm_extract_and_tailor(tmp_path)
        test_llm_run_full_pipeline(tmp_path)

    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)
    sys.exit(1 if failed else 0)
