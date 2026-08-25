# Curriculum Builder — MCP Prompts

All tools are available via the `curriculum-builder` MCP server.

**No API key needed for local endpoints.** The client defaults to `http://localhost:20128/v1`.

**Vision verification is ON by default.** The pipeline screenshots each PDF page and sends it to the vision model for layout/typography checks. Set `vision=False` to skip vision.

**1-page CVs enforced.** Multi-page CVs trigger a critical issue and aggressive auto-trim (drops low-relevance items) until the CV fits on one page.

---

## Quick start: build a demo PDF (no LLM needed)

```
Use the curriculum-builder MCP tools:
1. Call list_templates
2. Call build_pdf with template="cv"
3. Call screenshot_pdf with template="cv"
```

---

## Full pipeline: tailor + build for a job posting

```
Use the curriculum-builder MCP tools:
1. Call load_profile with path="examples/profile.yaml"
2. Create a file examples/my_job.txt with the job description text:
   "Senior Python Developer at Acme Corp. 5+ years experience required.
    Must know Python, Django, PostgreSQL, AWS. Nice to have: Docker, K8s."
3. Call run_full_pipeline with:
   - profile_path="examples/profile.yaml"
   - requirements_path="examples/my_job.txt"
   - template="cv"
   - auto_approve=true
   - max_iterations=3
4. Call screenshot_pdf with template="cv"
```

---

## Step-by-step: manual control over each stage

### Step 1: See available templates
```
Call list_templates
```

### Step 2: Load your profile
```
Call load_profile with path="examples/profile.yaml"
```

### Step 3: Extract requirements from a job ad
```
Call extract_requirements with raw_text="Paste the full job description here..."
```

### Step 4: Tailor the profile for the job
```
Call tailor_profile with:
- profile_path="examples/profile.yaml"
- requirements_path="examples/requirements.txt"
- threshold=0.3
```

### Step 5: Build the PDF
```
Call build_pdf with template="cv"
```

### Step 6: Verify the PDF
```
Call verify_pdf with pdf_path="<path returned by build_pdf>"
```

### Step 7: Take a screenshot for visual QA
```
Call screenshot_pdf with template="cv"
```

---

## Try a different template

Available templates: `cv`, `1`, `2`, `3`, `4`

```
Call build_pdf with template="3"
Call screenshot_pdf with template="3"
```

---

## Build to a custom directory

```
Call build_pdf with template="cv" and output_dir="/home/zed/pro/curriculum/applications/my_apply"
```

---

## Screenshot with explicit PDF path

```
Call screenshot_pdf with template="cv" and pdf_path="/home/zed/pro/curriculum/applications/mcp_build_cv/cv.pdf"
```

---

## Create a new curriculum from a template

```
Call create_curriculum with:
- name="john_doe"
- template="cv"
- data={"name": "John Doe", "email": "john@example.com", "role": "Software Engineer"}
```

---

## Multi-page check

```
Call build_pdf with template="4"
Call screenshot_pdf_all with template="4"
Call verify_pdf with pdf_path="applications/mcp_build_4/cv.pdf"  # auto-fails if >1 page
```
