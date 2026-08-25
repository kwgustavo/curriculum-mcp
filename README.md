# Curriculum Builder

LaTeX CV template system with a Python pipeline that generates **single-page, role-tailored CVs** from a hand-authored profile and a raw job description, using an OpenAI-compatible LLM and multimodal verification.

```
profile.yaml ─┐
              ├─► tailor (LLM) ─► tailored.json ─► pre.txt ─[USER GATE]─► render loop ─► cv.pdf
requirements ─┘                                          ▲                              │
                                                     user validates    ┌─ programmatic checks
                                                                      └─ vision checks (ON by default) │
                                                                               iterate ≤ 5
```

## Repo layout

```
curriculum/
├── curriculum_pipeline/           # role-tailored CV pipeline (standalone package)
│   ├── __init__.py
│   ├── schemas.py                 # Pydantic models: Profile, Requirements, TailoredProfile
│   ├── profile.py                 # load/save Profile (JSON/YAML)
│   ├── requirements.py            # load + LLM-extract Requirements from raw job ad
│   ├── tailor.py                  # LLM scoring + light rewriting of bullets
│   ├── template_schema.py         # per-template field-to-placeholder mapping + formatters
│   ├── render_text.py             # template-agnostic pre.txt pretty-printer
│   ├── renderer.py                # substitute TailoredProfile into .tex, build PDF
│   ├── verifier.py                # programmatic checks (PyMuPDF) + optional vision checks
│   ├── pipeline.py                # orchestrator: tailor → gate → render loop
│   ├── llm_client.py              # OpenAI-compatible chat/JSON/vision wrapper
│   ├── cli.py                     # `python -m curriculum_pipeline ...`
│   └── requirements.txt
├── templates/
│   ├── cv/                            # LuxSleek-CV variant (default)
│   │   ├── cv.tex
│   │   └── schema.yaml
│   ├── 1/pipeline/                    # Custom template (temp.cls)
│   │   ├── main.tex
│   │   ├── schema.yaml
│   │   ├── temp.cls
│   │   └── IMG/
│   ├── 2/pipeline/                    # Oval photo template
│   │   ├── main.tex
│   │   └── schema.yaml
│   ├── 3/pipeline/                    # Section-based template
│   │   ├── main.tex
│   │   └── schema.yaml
│   └── 4/pipeline/                    # AltaCV (wheelchart, publications)
│       ├── main.tex
│       ├── schema.yaml
│       ├── altacv.cls
│       ├── sample.bib
│       ├── pubs-num.tex
│       └── pubs-authoryear.tex
├── curriculum_mcp/                # MCP server (exposes pipeline as MCP tools)
│   ├── server.py                  #   tools + resources for AI clients
│   └── requirements.txt           #   mcp, PyMuPDF, pydantic, openai
├── examples/
│   ├── profile.yaml               # hand-authored base profile
│   ├── requirements.txt           # raw job description
│   └── oval-transparent.png       # sample photo
├── tests/
│   └── smoke.py                   # end-to-end smoke test (85 checks, no LLM needed)
├── build.py                       # legacy CLI for ad-hoc LaTeX builds
└── README.md
```

## Quick start

### Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r curriculum_pipeline/requirements.txt
```

### Configure the LLM (OpenAI-compatible)

The pipeline uses any endpoint that follows the OpenAI Chat Completions API
shape. Configure via environment variables:

### Local endpoint (default, no key needed)

```bash
export LLM_BASE_URL=http://localhost:20128/v1
export LLM_TEXT_MODEL=MiniMax-M3-NanoGPT       # or your model name
export LLM_VISION_MODEL=MiniMax-M3-NanoGPT     # for vision verification
```

### Cloud provider (requires API key)

```bash
export LLM_API_KEY=sk-...
export LLM_BASE_URL=https://api.openai.com/v1
export LLM_TEXT_MODEL=gpt-4o-mini
export LLM_VISION_MODEL=gpt-4o
```

No `LLM_API_KEY` is needed for local endpoints. The client defaults to
`http://localhost:20128/v1` if `LLM_BASE_URL` is not set.

### One-shot build (tailor → gate → render → iterate)

```bash
python -m curriculum_pipeline build \
    --profile examples/profile.yaml \
    --requirements examples/requirements.txt \
    --template cv \
    --output applications/acme_senior_ds \
    --max-iterations 5
```

The pipeline will:
1. Extract structured `Requirements` from `requirements.txt` (LLM call, cached).
2. Score your `Profile` against those requirements (LLM call, cached).
3. Write `pre.txt` + `tailored.json` + `requirements.json` to the application dir.
4. **Pause** for human review of `pre.txt`.
5. Render `.tex`, build PDF, run programmatic + (optional) vision checks.
6. On overflow / layout issues, drop the lowest-relevance item and retry (up to `--max-iterations`).
7. Save each iteration under `history/v1/`, `v2/`, ...

### Step-by-step

```bash
# Step 1: tailor only — produces pre.txt + tailored.json, then exits
python -m curriculum_pipeline tailor \
    --profile examples/profile.yaml \
    --requirements examples/requirements.txt \
    --template cv \
    --output applications/acme_senior_ds

# Step 2 (after reviewing pre.txt and editing tailored.json if desired):
touch applications/acme_senior_ds/.approved
python -m curriculum_pipeline render \
    --application applications/acme_senior_ds \
    --template cv \
    --max-iterations 5 \
    --vision            # enable OpenAI-compatible vision verification
```

### List templates

```bash
python -m curriculum_pipeline list
```

## Application directory layout

```
applications/<company>_<role>/
├── inputs/
│   ├── profile.yaml
│   ├── requirements.txt
│   └── requirements.json          # LLM-extracted, cached
├── tailored.json                  # current TailoredProfile
├── pre.txt                        # human-readable draft (template-agnostic)
├── cv.tex                         # current rendered LaTeX
├── cv.pdf                         # current rendered PDF
├── cv_p0.jpg                      # current screenshot
├── cv.build.log                   # LaTeX build log
├── log.md                         # iteration log
└── history/
    ├── v01/{cv.tex, cv.pdf, cv_p0.jpg, tailored.json}
    ├── v02/...
```

## How a template is wired in

Each template directory contains:

- `*.tex` — LaTeX source with `<<name>>` (text) and `<<#name>>…<</name>>` (list) placeholders
- `schema.yaml` — declares how each placeholder maps to a `TailoredProfile` field

Example `templates/cv/schema.yaml`:

```yaml
placeholders:
  first_name:       {field: personal.first_name, type: text}
  last_name:        {field: personal.last_name,  type: text}
  profile_text:     {field: profile_text,        type: text, max_chars: 600}
  experience:       {field: experience,          type: list, filter: included,
                     formatter: format_experience_entry}
  skills:           {field: skills,              type: list, filter: included,
                     formatter: format_skill_bullet}
  languages_inline: {field: languages,           type: inline,
                     formatter: format_language_inline}
```

Built-in formatters live in `curriculum_pipeline/template_schema.py`. Add custom
formatters by registering them in the `FORMATTERS` dict.

## Verification

**Programmatic (always run):**
- Page count == 1
- No text block bounding box exceeds the page rect (overflow detection)
- All required skills / keywords appear in extracted text
- Build log clean

**Vision (opt-in via `--vision`):**
- Screenshot page 1 at 840 px width
- Send to the configured vision model with a structured checklist
- Returns Issues by area (`layout`, `typography`, `content`, `relevance`, `photo`)

The render loop acts on these issues by dropping the lowest-relevance included
items from experience / education / skills when the layout is overflowing.

## Pipeline tools vs MCP server

The repo contains two surfaces:

| Surface | Use case | LLM required |
|---|---|---|
| `curriculum_pipeline` (CLI) | End-to-end CV generation, role-tailored, iterative | Yes for tailor/build |
| `curriculum_mcp` (MCP server) | AI-client-driven: all pipeline features as MCP tools | Only for tailoring/vision |

The MCP server imports from `curriculum_pipeline` — no code duplication.
Non-LLM MCP tools (templates, build, verify, applications) work without
`LLM_API_KEY`.

## Legacy: standalone LaTeX build

```bash
python build.py templates/cv/cv.tex            # auto-detect engine
python build.py templates/cv/cv.tex --engine lualatex --runs 2
```

## MCP server

```bash
python curriculum_mcp/server.py                  # stdio (for AI clients)
python curriculum_mcp/server.py --transport sse --port 8000  # remote
```

See the [MCP server section](#mcp-server) above for the full tool list.

## Templates

| Name | Dir | Engine | Biber | Notes |
|------|-----|--------|-------|-------|
| `cv` | `templates/cv/` | pdflatex | No | Default template. LuxSleek-CV variant. |
| `1` | `templates/1/pipeline/` | pdflatex | No | Custom template with `temp.cls`, logo support |
| `2` | `templates/2/pipeline/` | pdflatex | No | Oval photo template |
| `3` | `templates/3/pipeline/` | pdflatex | No | Section-based template |
| `4` | `templates/4/pipeline/` | pdflatex | Yes | AltaCV with wheelchart, publications, referees |

All 5 templates render clean with the example profile. Templates 1–4 live in `pipeline/`
subfolders to keep the original templates untouched.

## Adding a new template

1. Create `templates/<name>/<file>.tex` with `<<name>>` / `<<#name>>…<</name>>` placeholders.
2. Add `templates/<name>/schema.yaml` mapping each placeholder to a `Profile` / `TailoredProfile` field.
3. Use `% !TeX program = pdflatex` (or `lualatex`/`xelatex`) at the top.
4. `python -m curriculum_pipeline list` will pick it up.

## How the LLM fills data into the final CV

The LLM does **not** invent profile facts. It tailors what you already provided.

| Artifact | What the LLM does | Source |
|---|---|---|
| `requirements.json` | Extracts role, company, seniority, required/nice skills, keywords | from `requirements.txt` |
| `tailored.json` | Scores each item for relevance, picks included/excluded, rewrites top bullets, rewrites summary | from `profile.yaml` + `requirements.json` |
| `pre.txt` | Deterministic render of `tailored.json` — no separate LLM call | from `tailored.json` |
| `cv.pdf` | Rendered from `tailored.json` into LaTeX template | from `tailored.json` + template |
| `cv_p0.jpg` | Screenshot for vision model review | from `cv.pdf` |

**LLM rewrites are optional.** If the LLM returns no rewritten bullets, the
original bullets are used verbatim. The LLM is explicitly instructed not to
add facts that aren't in the source profile.

### Data flow summary

```
profile.yaml ──────────────────────────────────────────────┐
                                                          │
requirements.txt ──► LLM extract ──► requirements.json     │
                                                          │
profile.yaml + requirements.json ──► LLM tailor ──►       │
    │                                                     │
    ├─► tailored.json  (scores, inclusion, rewrites)      │
    ├─► pre.txt        (human review draft)                │
    │                                                     │
    └───► render tailoring into .tex template ──► cv.pdf  │
                                                          │
```

The `render` command reads only `tailored.json` — it makes no LLM calls.
The `tailor` command is the only stage that calls the LLM (for scoring
and rewriting). Vision verification is an optional second LLM call on
`cv_p0.jpg`.

### Without an LLM key

`render` works without `LLM_API_KEY` (no `--vision` flag needed).
`tailor` and `build` require the key.

## MCP server

The MCP server (`curriculum_mcp/server.py`) exposes the full pipeline
as MCP tools for AI clients. It imports everything from `curriculum_pipeline`
and provides lazy LLM initialization — non-LLM tools work without `LLM_API_KEY`.

### Start the server

```bash
# stdio transport (for AI clients)
python curriculum_mcp/server.py

# SSE transport (for remote access)
python curriculum_mcp/server.py --transport sse --port 8000
```

### LLM configuration

Set these env vars so the MCP server can call the LLM for tailoring,
requirements extraction, and vision verification:

```bash
export LLM_API_KEY=sk-...
export LLM_BASE_URL=http://localhost:20128/v1
export LLM_TEXT_MODEL=MiniMax-M3-NanoGPT
export LLM_VISION_MODEL=gpt-4o
```

### Available tools

**Template management** (no LLM):

| Tool | Description |
|---|---|
| `list_templates` | List all templates with placeholders and metadata |
| `get_template` | Read full .tex source and image dependencies |
| `get_template_info` | Packages, commands, placeholders |
| `build_pdf` | Build a template to PDF (uses pipeline renderer) |
| `create_curriculum` | Copy template with field substitutions |
| `screenshot_pdf` | Screenshot a PDF page + extract text |
| `screenshot_pdf_all` | Screenshot all pages |

**Profile management** (no LLM):

| Tool | Description |
|---|---|
| `load_profile` | Load profile from YAML/JSON |
| `save_profile` | Save profile to YAML/JSON |
| `validate_profile` | Validate profile data against schema |

**LLM-powered** (requires `LLM_API_KEY`):

| Tool | Description |
|---|---|
| `extract_requirements` | Extract structured job reqs from raw text |
| `load_requirements_cached` | Load or extract reqs (caches to .requirements.json) |
| `tailor_profile` | LLM scoring + bullet rewriting → tailored.json |
| `render_pre_text` | Generate human-readable draft from tailored.json |

**Pipeline orchestration**:

| Tool | Description |
|---|---|
| `init_application` | Create application dir with inputs staged |
| `run_render_loop` | Render → verify → iterate (requires .approved) |
| `run_full_pipeline` | One-shot: tailor → gate → render → PDF |

**Verification**:

| Tool | Description |
|---|---|
| `verify_pdf` | Programmatic checks (page count, overflow, keywords) |
| `verify_pdf_vision` | LLM vision check on rendered screenshot |
| `extract_text` | Extract text from a PDF page |

**Application management**:

| Tool | Description |
|---|---|
| `list_applications` | List all application directories |
| `read_application_artifacts` | Read tailored.json, pre.txt, log.md |

### Resources

| URI | Content |
|---|---|
| `templates://list` | All templates as JSON |
| `templates://{name}/schema` | Schema.yaml for a template |
| `templates://{name}/source` | Raw .tex source |
| `pipeline://status` | LLM config, templates, applications |
| `applications://list` | All applications as JSON |

## Smoke tests

```bash
python tests/smoke.py    # 67 checks, no LLM key required
```

Validates: profile loading, template discovery, placeholder substitution,
PDF building (all 5 templates), page count, verification, screenshots,
the CLI render path, and all MCP server tools and resources — all without
an LLM API key.

## Notes on LaTeX escaping

The pipeline escapes LaTeX-special characters in user-provided text
(`& % $ # _ { } ~ ^ \`). Multi-paragraph text uses blank lines in YAML/JSON;
the pipeline converts them to `\par` separators.
