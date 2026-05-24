#!/usr/bin/env python3
"""
tools/resume_to_profile.py — Generate a JobRadar profile YAML from a resume PDF.

Usage:
    python tools/resume_to_profile.py <resume.pdf> <output_profile.yaml>
"""

import os
import sys
from pathlib import Path
import pdfplumber
import yaml
from dotenv import load_dotenv
from groq import Groq

# Load environment variables
load_dotenv()

MODEL = "llama-3.3-70b-versatile"
TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "profiles" / "template.yaml"


def extract_text_from_pdf(pdf_path: str) -> str:
    """Extract text from PDF using pdfplumber.
    
    Exits with a clear error if the PDF yields no text (e.g. scanned/image-only
    PDF where pdfplumber cannot extract characters).
    """
    if not os.path.exists(pdf_path):
        print(f"Error: PDF file '{pdf_path}' not found.")
        sys.exit(1)
        
    try:
        with pdfplumber.open(pdf_path) as pdf:
            text = []
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text.append(page_text)
            result = "\n".join(text).strip()
    except Exception as e:
        print(f"Error reading PDF with pdfplumber: {e}")
        sys.exit(1)

    if not result:
        print(
            "Error: No text could be extracted from the PDF.\n"
            "This usually means the PDF is scanned/image-based and has no embedded text layer.\n"
            "Please use a text-selectable PDF or run OCR first (e.g. Adobe Acrobat, pdfocr)."
        )
        sys.exit(1)

    return result


def load_template_schema() -> str:
    """Load the template.yaml at runtime to embed in prompt."""
    if not TEMPLATE_PATH.exists():
        print(f"Error: Template schema not found at '{TEMPLATE_PATH}'.")
        sys.exit(1)
    try:
        return TEMPLATE_PATH.read_text(encoding="utf-8")
    except Exception as e:
        print(f"Error reading template schema: {e}")
        sys.exit(1)


def clean_llm_yaml(text: str) -> str:
    """Cleans markdown code blocks (```yaml ... ```) if present in the LLM response."""
    text = text.strip()
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 3:
            inner = parts[1].strip()
            if inner.startswith("yaml"):
                inner = inner[4:].strip()
            elif inner.startswith("yml"):
                inner = inner[3:].strip()
            text = inner
    return text


def call_groq(prompt: str, system_prompt: str = "") -> str:
    """Make API call to Groq using llama-3.3-70b-versatile."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        print("Error: GROQ_API_KEY is not set in .env")
        sys.exit(1)

    try:
        client = Groq(api_key=api_key)
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            temperature=0.1,  # Low temperature for highly deterministic structure
            max_tokens=4096,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"Groq API Call failed: {e}")
        sys.exit(1)


def build_prompt(resume_text: str, template_yaml: str) -> str:
    """Constructs the prompt embedding the dynamic template.yaml schema."""
    return f"""You are an expert recruiter and parser. You are given a candidate's resume (in plain text) and a YAML template profile.
Your task is to parse the resume and populate the YAML template profile exactly according to the structure and fields in the template.

RULES:
1. You MUST return ONLY the final populated YAML. Do not wrap it in markdown code fences like ```yaml ... ```, and do not write any introductory or explanatory text. Return strictly the YAML.
2. Preserve all comments and structural indentation from the template.
3. For fields you cannot determine from the resume, keep the default placeholder value as-is.
4. db_path: Replace the '<username>' in 'data/<username>.db' with the candidate's first name in lowercase. E.g. if the candidate's name is 'Rohit Kumar Roy', set it to 'data/rohit.db'.
5. telegram_chat_id: Leave this as "" (it cannot be determined from the resume).
6. Under the 'candidate' section:
   - name: Full name of the candidate.
   - email: Email address of the candidate.
   - roles.primary: List the target roles or roles the candidate is seeking/qualified for based on their education and skills (e.g. "Backend Engineering Intern", "Software Engineering Intern"). Replace the placeholder list elements (do not leave empty string elements in lists).
   - roles.secondary: List any alternative roles (e.g. "Full Stack Intern").
   - skills.strong: List specific tech stack languages/tools they have strong/proven projects in (e.g. "Go", "TypeScript", "Python").
   - skills.learning: List skills they are learning or have basic familiarity with (e.g. "Kubernetes").
   - projects: Extract key projects from the resume. For each project, populate:
     - name: Project name
     - description: 1-2 sentence description of what was built and its impact.
     - relevance_signal: comma-separated list of keywords, tech, domain patterns used in the project.
   - education:
     - degree: e.g. "B.Tech Computer Science"
     - institution: e.g. "IIT Delhi"
     - graduation: e.g. "May 2026"
   - location.base: e.g. "Kolkata, India" or "Bangalore, India".
   - industries.high_priority: e.g. "Fintech", "SaaS", "Developer tools" if projects/experience align.
   - salary.min_stipend_inr: monthly stipend floor (integer, e.g. 10000 or keep 0 if unknown).
   - salary.min_ctc_lpa: minimum annual CTC in LPA (float, e.g. 6.0 or keep 0.0 if unknown).
7. Do not touch or modify the global config settings like sources toggles (except serper_max_calls which should remain 10), hard_reject keywords, company_blacklist, role_blacklist, or scoring_weights. Keep them exactly as they are in the template.

Here is the YAML template schema to fill in:
{template_yaml}

Here is the candidate's Resume text:
{resume_text}

Provide the filled YAML now:"""


def print_summary(parsed: dict, template: dict):
    """Print summary comparing populated fields against template defaults."""
    print("\n" + "=" * 65)
    print("  PROFILE GENERATION SUMMARY")
    print("=" * 65)
    print(f"{'Field Path':<35} | {'Status':<12} | {'Value'}")
    print("-" * 65)

    def check_status(val, t_val) -> str:
        # None means the field was absent or explicitly nulled out — always Default.
        if val is None:
            return "Default"
        if val == t_val:
            return "Default"
        if isinstance(val, str):
            if val.strip() == "" or "TODO" in val or "<" in val:
                return "Default"
        if isinstance(val, list):
            # Guard against non-string items (e.g. null elements from YAML `- null`).
            non_empty = [
                item for item in val
                if item is not None
                and isinstance(item, str)
                and item.strip() != ""
                and "TODO" not in item
            ]
            if not non_empty:
                return "Default"
        if isinstance(val, dict):
            if val == t_val:
                return "Default"
        return "Populated"

    key_fields = [
        ("db_path", ["db_path"]),
        ("telegram_chat_id", ["telegram_chat_id"]),
        ("candidate.name", ["candidate", "name"]),
        ("candidate.email", ["candidate", "email"]),
        ("candidate.roles.primary", ["candidate", "roles", "primary"]),
        ("candidate.roles.secondary", ["candidate", "roles", "secondary"]),
        ("candidate.skills.strong", ["candidate", "skills", "strong"]),
        ("candidate.skills.learning", ["candidate", "skills", "learning"]),
        ("candidate.education.degree", ["candidate", "education", "degree"]),
        ("candidate.education.institution", ["candidate", "education", "institution"]),
        ("candidate.education.graduation", ["candidate", "education", "graduation"]),
        ("candidate.location.base", ["candidate", "location", "base"]),
        ("candidate.salary.min_stipend_inr", ["candidate", "salary", "min_stipend_inr"]),
        ("candidate.salary.min_ctc_lpa", ["candidate", "salary", "min_ctc_lpa"]),
    ]

    for label, path in key_fields:
        val = parsed
        t_val = template
        for key in path:
            val = val.get(key) if isinstance(val, dict) else None
            t_val = t_val.get(key) if isinstance(t_val, dict) else None

        status = check_status(val, t_val)

        if label == "telegram_chat_id":
            status = "MANUAL REQ"
            val_str = "(Must fill in manually)"
        else:
            if isinstance(val, list):
                val_str = str([v for v in val if v])
            else:
                val_str = str(val) if val is not None else ""

        print(f"{label:<35} | {status:<12} | {val_str}")

    # Process projects specifically
    projects = parsed.get("candidate", {}).get("projects", [])
    t_projects = template.get("candidate", {}).get("projects", [])
    proj_status = "Default"
    if projects and projects != t_projects:
        if projects[0].get("name") and projects[0].get("name") != "":
            proj_status = "Populated"

    proj_val = f"{len(projects)} projects extracted" if proj_status == "Populated" else "None"
    print(f"{'candidate.projects':<35} | {proj_status:<12} | {proj_val}")
    print("=" * 65)


def main():
    if len(sys.argv) != 3:
        print("Usage: python tools/resume_to_profile.py <resume.pdf> <output_profile.yaml>")
        sys.exit(1)

    pdf_path = sys.argv[1]
    output_path = sys.argv[2]

    print("Step 1: Extracting text from PDF using pdfplumber...")
    resume_text = extract_text_from_pdf(pdf_path)
    
    print("Step 2: Loading dynamic schema from profiles/template.yaml...")
    template_yaml = load_template_schema()
    template_dict = yaml.safe_load(template_yaml)

    print("Step 3: Querying Groq with llama-3.3-70b-versatile...")
    prompt = build_prompt(resume_text, template_yaml)
    raw_output = call_groq(prompt, "You are a precise data extractor returning clean valid YAML.")
    
    cleaned_yaml = clean_llm_yaml(raw_output)

    # Validation and retry logic
    print("Step 4: Validating YAML output...")
    try:
        parsed_dict = yaml.safe_load(cleaned_yaml)
        if not parsed_dict:
            raise yaml.YAMLError("Parsed output is empty or invalid structure.")
    except yaml.YAMLError as e:
        print(f"YAML Validation failed on first attempt: {e}")
        print("Retrying once with error correction prompt...")
        
        retry_prompt = f"This YAML is invalid: {e}. Fix and return only valid YAML.\n\nRaw input:\n{cleaned_yaml}"
        raw_output = call_groq(retry_prompt, "You must fix the provided invalid YAML and return only clean valid YAML without any surrounding markdown or explanation.")
        cleaned_yaml = clean_llm_yaml(raw_output)
        
        try:
            parsed_dict = yaml.safe_load(cleaned_yaml)
            if not parsed_dict:
                raise yaml.YAMLError("Parsed output is empty or invalid structure.")
        except yaml.YAMLError as e2:
            print("YAML Validation failed on second attempt.")
            print("=" * 65)
            print("RAW MODEL OUTPUT:")
            print("=" * 65)
            print(raw_output)
            print("=" * 65)
            print(f"Error: {e2}")
            sys.exit(1)

    # Success: Save and output summary
    print(f"Step 5: YAML validated successfully! Writing profile to {output_path}...")
    try:
        out_dir = Path(output_path).parent
        if out_dir:
            out_dir.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(cleaned_yaml, encoding="utf-8")
    except Exception as e:
        print(f"Error writing profile to '{output_path}': {e}")
        sys.exit(1)

    # Summary comparison
    print_summary(parsed_dict, template_dict)
    
    print("\nNext Steps:")
    print(f"1. Open '{output_path}' and fill in 'telegram_chat_id'.")
    print(f"2. Run a dry run to verify the configuration:")
    print(f"   python main.py {output_path} --dry-run")
    print("Successfully finished.")


if __name__ == "__main__":
    main()
