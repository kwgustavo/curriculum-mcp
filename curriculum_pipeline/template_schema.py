"""Per-template field-to-placeholder mapping.

Each template directory may contain a `schema.yaml` (or `.json`) that describes
how a generic TailoredProfile maps onto that template's LaTeX placeholders.

Example:
    placeholders:
      name:        {field: personal.name, type: text}
      email:       {field: contact.email, type: text}
      profile:     {field: profile_text,  type: text, max_chars: 400}
      experience:  {field: experience,    type: list, max_items: 8,
                    formatter: format_experience_entry}
      skills:      {field: skills,        type: list, max_items: 12,
                    filter: included}
      languages:   {field: languages,     type: inline,
                    formatter: format_languages_inline}
      hobbies:     {field: hobbies,       type: list, formatter: format_hobbies}

A placeholder block in the .tex file is rendered as `{{{name}}}`.
For list-typed placeholders, the entire `{{{#experience}}}` block
(starts with `{{{#name}}}` and ends with `{{{/name}}}`) is rendered once per
item, with each item formatted by the named `formatter` callable.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

from .schemas import TailoredProfile


@dataclass
class PlaceholderSpec:
    name: str
    field: str  # dotted path into tailored profile
    type: str = "text"  # text | list | inline
    max_chars: int | None = None
    max_items: int | None = None
    filter: str | None = None  # e.g. "included" — only items where .included is True
    formatter: str | None = None  # name of formatter registered in FormatterRegistry


@dataclass
class TemplateSchema:
    name: str
    tex_path: Path
    raw_source: str
    placeholders: dict[str, PlaceholderSpec] = field(default_factory=dict)
    requires_biber: bool = False

    def placeholder_names(self) -> list[str]:
        return list(self.placeholders.keys())


# ── Built-in formatters ──────────────────────────────────────────────────────


def _latex_escape(s: str) -> str:
    if not isinstance(s, str):
        s = str(s)
    return (
        s.replace("\\", r"\textbackslash{}")
        .replace("&", r"\&")
        .replace("%", r"\%")
        .replace("$", r"\$")
        .replace("#", r"\#")
        .replace("_", r"\_")
        .replace("{", r"\{")
        .replace("}", r"\}")
        .replace("~", r"\textasciitilde{}")
        .replace("^", r"\textasciicircum{}")
    )


def _paragraphs_to_latex(s: str) -> str:
    """Convert real newlines to LaTeX-aware form. Blank line = \\par, single = space."""
    parts = [p.replace("\n", " ").strip() for p in s.split("\n\n")]
    parts = [p for p in parts if p]
    return " \\par ".join(parts)


def format_experience_entry(item: dict[str, Any]) -> str:
    """Render one TailoredExperienceItem as the body of one Experience entry.

    Output is the LaTeX fragment emitted once per item inside the
    {{#experience}}…{{/experience}} block. The wrapper (\\headright, \\is)
    is the template's responsibility.
    """
    title = _latex_escape(item.get("title", ""))
    company = _latex_escape(item.get("company", ""))
    location = item.get("location")
    location_part = f" ({_latex_escape(location)})" if location else ""
    start = _latex_escape(item.get("start", ""))
    end = _latex_escape(item.get("end", ""))
    bullets = item.get("active_bullets") or item.get("bullets") or []

    lines = [
        f"\\textsc{{{title}}} at \\textit{{{company}{location_part}.}}  \\dates{{{start}--{end}}} \\\\"
    ]
    for i, b in enumerate(bullets):
        suffix = " \\\\" if i < len(bullets) - 1 else ""
        lines.append(f"\\smaller{{{_latex_escape(b)}}}{suffix}")
    return "\n".join(lines)


def format_education_entry(item: dict[str, Any]) -> str:
    degree = _latex_escape(item.get("degree", ""))
    field_ = item.get("field")
    field_text = _latex_escape(field_) if field_ else ""
    institution = _latex_escape(item.get("institution", ""))
    start = _latex_escape(item.get("start", ""))
    end = _latex_escape(item.get("end", ""))
    bullets = item.get("bullets") or []

    if field_text:
        head = f"\\textsc{{{degree}.}} {field_text}. \\textit{{{institution}}}. \\dates{{{start}--{end}}} \\\\"
    else:
        head = f"\\textsc{{{degree}.}} \\textit{{{institution}}}. \\dates{{{start}--{end}}} \\\\"
    lines = [head]
    for i, b in enumerate(bullets):
        suffix = " \\\\" if i < len(bullets) - 1 else ""
        lines.append(f"\\smaller{{{_latex_escape(b)}}}{suffix}")
    return "\n".join(lines)


def format_skill_bullet(item: dict[str, Any]) -> str:
    name = _latex_escape(item.get("name", ""))
    category = item.get("category")
    if category:
        return f"\\item \\textbf{{{_latex_escape(str(category))}:}} {name}\n"
    return f"\\item {name}\n"


def format_language_inline(item: dict[str, Any]) -> str:
    name = _latex_escape(item.get("name", ""))
    level = _latex_escape(item.get("level", ""))
    return f"\\textbf{{{name}}}~({level})"


def format_language_inline_item(item: dict[str, Any]) -> str:
    """Format a single language as a table row: \\textbf{name}~(level) \\\\."""
    name = _latex_escape(item.get("name", ""))
    level = _latex_escape(item.get("level", ""))
    return f"\\textbf{{{name}}}~({level}) \\\\"


def format_hobby_paragraph(item: str) -> str:
    safe = _latex_escape(str(item))
    return f"\\textit{{{safe}}}\n\n"


def format_github_href(github: str) -> str:
    """Format a `contact.github` string as a `\\href{url}{label}` fragment."""
    if not github:
        return ""
    s = str(github).strip()
    if not s:
        return ""
    if s.startswith("http://") or s.startswith("https://"):
        url = s
        label = s.split("://", 1)[1].rstrip("/")
    elif "/" in s:
        # already a path like "github.com/user" or "org/repo"
        if s.startswith("github.com/"):
            url = "https://" + s
            label = s
        else:
            url = "https://github.com/" + s
            label = "github.com/" + s
    else:
        url = "https://github.com/" + s
        label = "github.com/" + s
    return f"\\href{{{_latex_escape(url)}}}{{{_latex_escape(label)}}}"


FORMATTERS: dict[str, Callable[[Any], str]] = {
    "format_experience_entry": format_experience_entry,
    "format_education_entry": format_education_entry,
    "format_skill_bullet": format_skill_bullet,
    "format_language_inline": format_language_inline,
    "format_language_inline_item": format_language_inline_item,
    "format_hobby_item": format_hobby_paragraph,
    "format_github_href": format_github_href,
}


# ── Template discovery and loading ──────────────────────────────────────────


_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


def discover_templates(templates_dir: Path | None = None) -> list[str]:
    """Return template names that have a `schema.yaml` or `schema.json`.

    Scans two levels:
      - templates/<name>/schema.yaml      (legacy, e.g. cv/)
      - templates/<name>/pipeline/schema.yaml  (pipeline-friendly child folder)
    """
    base = Path(templates_dir) if templates_dir else _TEMPLATES_DIR
    names: list[str] = []
    if not base.exists():
        return names
    # Scan top-level schema files (legacy)
    for schema_path in sorted(base.glob("*/schema.y*")):
        names.append(schema_path.parent.name)
    # Scan pipeline subfolder schema files (skip duplicates)
    for schema_path in sorted(base.glob("*/pipeline/schema.y*")):
        tpl_name = schema_path.parent.parent.name
        if tpl_name not in names:
            names.append(tpl_name)
    return names


def load_template_schema(
    template_name: str,
    templates_dir: Path | None = None,
) -> TemplateSchema:
    """Load a template's `schema.yaml/json` + matching `.tex` file.

    Checks for a pipeline subfolder first (templates/<name>/pipeline/),
    then falls back to the top-level folder (templates/<name>/).
    """
    base = Path(templates_dir) if templates_dir else _TEMPLATES_DIR
    tpl_dir = base / template_name
    if not tpl_dir.exists():
        raise FileNotFoundError(f"Template not found: {tpl_dir}")

    # Prefer pipeline subfolder
    pipeline_dir = tpl_dir / "pipeline"
    search_dirs = [pipeline_dir, tpl_dir] if pipeline_dir.exists() else [tpl_dir]

    schema_data = None
    tex_path = None
    requires_biber = False

    for d in search_dirs:
        schema_yaml = d / "schema.yaml"
        schema_json = d / "schema.json"
        if schema_yaml.exists():
            schema_data = yaml.safe_load(schema_yaml.read_text(encoding="utf-8"))
            break
        elif schema_json.exists():
            schema_data = json.loads(schema_json.read_text(encoding="utf-8"))
            break

    if schema_data is None:
        raise FileNotFoundError(
            f"Template '{template_name}' has no schema.yaml or schema.json"
        )

    # Determine which dir the schema came from
    schema_dir = pipeline_dir if pipeline_dir.exists() and (pipeline_dir / "schema.yaml").exists() or (pipeline_dir / "schema.json").exists() else tpl_dir

    tex_files = list(schema_dir.glob("*.tex"))
    if not tex_files:
        raise FileNotFoundError(f"No .tex file in template '{template_name}' at {schema_dir}")
    # Prefer main.tex if it exists
    main_tex = [f for f in tex_files if f.name == "main.tex"]
    tex_path = main_tex[0] if main_tex else tex_files[0]
    raw_source = tex_path.read_text(encoding="utf-8")

    requires_biber = schema_data.get("requires_biber", False)

    placeholders: dict[str, PlaceholderSpec] = {}
    for name, spec in (schema_data.get("placeholders") or {}).items():
        placeholders[name] = PlaceholderSpec(
            name=name,
            field=spec["field"],
            type=spec.get("type", "text"),
            max_chars=spec.get("max_chars"),
            max_items=spec.get("max_items"),
            filter=spec.get("filter"),
            formatter=spec.get("formatter"),
        )

    return TemplateSchema(
        name=template_name,
        tex_path=tex_path,
        raw_source=raw_source,
        placeholders=placeholders,
        requires_biber=requires_biber,
    )


# ── Substitution ────────────────────────────────────────────────────────────


_INLINE_TOKEN = re.compile(r"<<(\w+)>>")
_BLOCK_OPEN = re.compile(r"<<#(\w+)>>")
_BLOCK_CLOSE = re.compile(r"<</(\w+)>>")


def _get_dotted(obj: Any, dotted: str) -> Any:
    cur = obj
    for part in dotted.split("."):
        if cur is None:
            return None
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            cur = getattr(cur, part, None)
    return cur


def _render_text_placeholder(spec: PlaceholderSpec, profile: TailoredProfile) -> str:
    value = _get_dotted(profile, spec.field)
    if value is None:
        return ""
    raw = str(value)
    if spec.max_chars is not None and len(raw) > spec.max_chars:
        raw = raw[: spec.max_chars - 1].rstrip() + "…"
    parts = [p.replace("\n", " ").strip() for p in raw.split("\n\n")]
    parts = [p for p in parts if p]
    escaped = [_latex_escape(p) for p in parts]
    return r" \par ".join(escaped)


def _inline_substitute_block(template: str, item: dict | Any) -> str:
    """Replace <<field>> tokens in a block template with item values."""
    if isinstance(item, dict):
        data = item
    elif hasattr(item, "model_dump"):
        data = item.model_dump()
    else:
        return str(item)

    def _replace(m: re.Match) -> str:
        key = m.group(1)
        val = data.get(key)
        if val is None:
            return ""
        if isinstance(val, list):
            # Join list items (e.g. bullets) as space-separated escaped strings
            return " ".join(_latex_escape(str(v)) for v in val)
        return _latex_escape(str(val))

    return _INLINE_TOKEN.sub(_replace, template)


def _render_list_placeholder(
    spec: PlaceholderSpec,
    profile: TailoredProfile,
    block_template: str,
) -> str:
    items = _get_dotted(profile, spec.field)
    if not items:
        return ""
    if spec.filter == "included":
        items = [it for it in items if (it.get("included") if isinstance(it, dict) else getattr(it, "included", True))]

    # Order: keep input order (templates assume sorted-by-relevance order from tailor)

    if spec.max_items is not None:
        items = items[: spec.max_items]

    formatter = FORMATTERS.get(spec.formatter or "", None)

    rendered_items: list[str] = []
    for item in items:
        if formatter:
            # Use the registered formatter
            if isinstance(item, str):
                rendered_items.append(formatter(item))
            elif isinstance(item, dict):
                d = dict(item)
                d.setdefault("active_bullets", d.get("bullets"))
                rendered_items.append(formatter(d))
            elif hasattr(item, "model_dump"):
                d = item.model_dump()
                d.setdefault(
                    "active_bullets",
                    getattr(item, "active_bullets", lambda: d.get("bullets"))(),
                )
                rendered_items.append(formatter(d))
            else:
                rendered_items.append(formatter(item))
        else:
            # No formatter — do inline <<field>> substitution in block template
            # Ensure active_bullets is available for TailoredExperienceItem
            if isinstance(item, dict):
                d = dict(item)
                d.setdefault("active_bullets", d.get("bullets"))
            elif hasattr(item, "model_dump"):
                d = item.model_dump()
                d.setdefault(
                    "active_bullets",
                    getattr(item, "active_bullets", lambda: d.get("bullets"))(),
                )
            else:
                d = item
            rendered_items.append(_inline_substitute_block(block_template, d))

    body = "\n".join(rendered_items)
    if formatter:
        # Formatter produces the complete output — don't include block template
        return body + "\n"
    else:
        # No formatter — body already contains per-item inline-substituted content
        return body + "\n"


def _render_inline_placeholder(
    spec: PlaceholderSpec,
    profile: TailoredProfile,
) -> str:
    value = _get_dotted(profile, spec.field)
    if value is None or value == "":
        return ""
    formatter = FORMATTERS.get(spec.formatter or "", lambda x: str(x))
    if isinstance(value, str):
        return formatter(value)
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                parts.append(formatter(item))
            elif hasattr(item, "model_dump"):
                parts.append(formatter(item.model_dump()))
            else:
                parts.append(formatter(item))
        return ", ".join(parts)
    return formatter(value)


def render(template: TemplateSchema, profile: TailoredProfile) -> str:
    """Apply all placeholder substitutions to the template's .tex source."""
    source = template.raw_source

    # First pass: list blocks ({{#name}}…{{/name}})
    def _block_sub(m: re.Match) -> str:
        name = m.group(1)
        spec = template.placeholders.get(name)
        if not spec or spec.type != "list":
            return m.group(0)
        return _render_list_placeholder(spec, profile, m.group(0))

    def _consume_blocks(src: str) -> str:
        """Process block tags innermost-first, iterating until done."""
        for _iteration in range(20):  # safety limit
            best = None
            for m in _BLOCK_OPEN.finditer(src):
                tag_name = m.group(1)
                close_pattern = re.compile(rf"<</{re.escape(tag_name)}>>")
                close = close_pattern.search(src, m.end())
                if not close:
                    continue
                inner = src[m.end() : close.start()]
                if "<<#" not in inner:
                    best = (m, close, tag_name)
                    break
            if best is None:
                break
            m, close, tag_name = best
            # Re-extract inner from current src (may have changed from nested renders)
            inner = src[m.end() : close.start()]
            spec = template.placeholders.get(tag_name)
            if spec and spec.type == "list":
                rendered = _render_list_placeholder(spec, profile, inner)
                src = src[: m.start()] + rendered + src[close.end() :]
            else:
                # Unknown block — strip the tags, keep inner content
                src = src[: m.start()] + inner + src[close.end() :]
        return src

    # First pass: list blocks (<<#name>…<</name>>)
    source = _consume_blocks(source)

    # Second pass: inline tokens ({{name}}) and text placeholders
    def _inline_sub(m: re.Match) -> str:
        name = m.group(1)
        spec = template.placeholders.get(name)
        if not spec:
            return m.group(0)
        if spec.type == "inline":
            return _render_inline_placeholder(spec, profile)
        return _render_text_placeholder(spec, profile)

    source = _INLINE_TOKEN.sub(_inline_sub, source)
    return source
