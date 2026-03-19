import os
import re
import csv
import uuid
import shutil
import subprocess
import tempfile
import traceback
import zipfile

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from latex_utils import build_latex_document

app = FastAPI(title="Ayaan Paper Generator")


# ─────────────────────────────────────────────
# Request schema
# ─────────────────────────────────────────────
class GenerateRequest(BaseModel):
    sheet_link: str
    email: str | None = None


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
def extract_sheet_id(url: str) -> tuple[str | None, str]:
    """
    Extract the Google Sheets spreadsheet ID and optionally the gid (tab ID)
    from any common URL format. Defaults to gid=0 if not found.
    """
    sheet_id = None
    gid = "0"

    # Spreadsheets ID
    id_match = re.search(r'/spreadsheets/d/([a-zA-Z0-9_-]+)', url)
    if id_match:
        sheet_id = id_match.group(1)

    # GID (Tab ID)
    gid_match = re.search(r'[#&?]gid=([0-9]+)', url)
    if gid_match:
        gid = gid_match.group(1)

    return sheet_id, gid


def get_sheet_title(sheet_id: str) -> str:
    """
    Try to get the human-readable title of a public Google Sheet.
    Falls back to the sheet ID if it cannot be determined.
    """
    try:
        # Google Sheets HTML page contains the title in <title>
        url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        resp = requests.get(url, headers=headers, timeout=10, allow_redirects=True)
        match = re.search(r'<title>(.*?) - Google Sheets</title>', resp.text)
        if match:
            raw = match.group(1).strip()
            # Sanitise for use as a filename
            safe = re.sub(r'[<>:"/\\|?*]', '_', raw)
            return safe or sheet_id
    except Exception:
        pass
    return sheet_id


def fetch_sheet_as_csv(sheet_id: str, gid: str) -> list[dict]:
    """
    Exports the sheet of a public Google Spreadsheet as CSV.
    Tries dual-endpoint approach: 
    1. Google Visualization API (often more resilient for public sheets).
    2. Standard Export URL (fallback).
    """
    gviz_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&gid={gid}"
    export_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    resp = None
    error_details = []

    # Attempt 1: GVIZ
    try:
        r = requests.get(gviz_url, headers=headers, timeout=20)
        if r.status_code == 200:
            resp = r
        else:
            error_details.append(f"GVIZ API failed (HTTP {r.status_code})")
    except Exception as e:
        error_details.append(f"GVIZ API error: {str(e)}")

    # Attempt 2: Standard Export (if GVIZ failed)
    if not resp:
        try:
            r = requests.get(export_url, headers=headers, timeout=20)
            if r.status_code == 200:
                resp = r
            else:
                error_details.append(f"Export URL failed (HTTP {r.status_code})")
        except Exception as e:
            error_details.append(f"Export URL error: {str(e)}")

    if not resp:
        # If both fail, raise a detailed error
        detail_msg = (
            "Could not fetch sheet data. "
            "Please ensure the sheet is set to 'Anyone with the link can view' AND "
            "that 'Viewers can download' is enabled in Share -> Settings. "
            f"Diagnostics: {'; '.join(error_details)}"
        )
        raise HTTPException(status_code=400, detail=detail_msg)

    # Decode and parse
    text = resp.content.decode('utf-8-sig')
    reader = csv.DictReader(text.splitlines())
    
    # Normalization Map: maps various header formats to our expected keys
    # Keys in the code: Question_Text, Option_A, Option_B, Option_C, Option_D, Correct_Answer
    key_map = {
        'SR_NO': ['SR_NO', 'SR No', 'Serial Number', 'SR_No', 'Sr. No', 'Sr No', 'S.No', 'S.No.', 'sl no', 'SL NO'],
        'Question_Text': ['Question_Text', 'Question Text', 'Question'],
        'Option_A': ['Option_A', 'Option A', 'A'],
        'Option_B': ['Option_B', 'Option B', 'B'],
        'Option_C': ['Option_C', 'Option C', 'C'],
        'Option_D': ['Option_D', 'Option D', 'D'],
        'Correct_Answer': ['Correct_Answer', 'Correct Answer', 'Answer'],
    }

    rows = []
    # Identify which columns in the CSV map to our internal keys
    fieldnames = reader.fieldnames or []
    mapping = {}
    for internal_key, variations in key_map.items():
        for field in fieldnames:
            if field.strip() in variations:
                mapping[field] = internal_key
                break

    for row in reader:
        # Normalize the row based on the mapping
        normalized_row = {mapping.get(k, k): v for k, v in row.items()}
        
        # Skip completely empty rows
        if not any(v.strip() for v in normalized_row.values()):
            continue
        # Skip repeated header rows
        if normalized_row.get('Question_Text', '').strip() == 'Question_Text':
            continue
        rows.append(normalized_row)
    return rows


def compile_latex(tex_content: str, output_name: str) -> tuple[str, str]:
    """
    Writes the .tex content to a temp directory, runs xelatex twice
    (second pass stabilises page numbers / TOC), generates a DOCX via
    pandoc, bundles both into a ZIP, and returns the path to the ZIP
    and the work directory.
    """
    work_dir = tempfile.mkdtemp(prefix="paper_")
    tex_path = os.path.join(work_dir, f"{output_name}.tex")
    pdf_path = os.path.join(work_dir, f"{output_name}.pdf")
    docx_path = os.path.join(work_dir, f"{output_name}.docx")
    zip_path = os.path.join(work_dir, f"{output_name}.zip")

    with open(tex_path, 'w', encoding='utf-8') as f:
        f.write(tex_content)

    # ── XeLaTeX compilation (PDF) ─────────────────────────────
    xelatex_cmd = [
        'xelatex',
        '-interaction=nonstopmode',
        '-halt-on-error',
        f'-output-directory={work_dir}',
        tex_path,
    ]

    for run in range(2):  # two passes
        result = subprocess.run(
            xelatex_cmd,
            capture_output=True,
            text=True,
            cwd=work_dir,
        )
        if result.returncode != 0 and run == 1:
            log_path = os.path.join(work_dir, f"{output_name}.log")
            log_excerpt = ""
            if os.path.exists(log_path):
                with open(log_path, 'r', encoding='utf-8', errors='replace') as lf:
                    lines = lf.readlines()
                # Find error lines
                err_lines = [l for l in lines if l.startswith('!')]
                log_excerpt = ''.join(err_lines[-20:])
            raise HTTPException(
                status_code=500,
                detail=f"XeLaTeX compilation failed.\n{log_excerpt}"
            )

    if not os.path.exists(pdf_path):
        raise HTTPException(status_code=500, detail="PDF not found after compilation.")

    # ── Pandoc conversion (DOCX) ──────────────────────────────
    pandoc_cmd = [
        'pandoc',
        tex_path,
        '-o', docx_path,
    ]
    pandoc_result = subprocess.run(
        pandoc_cmd,
        capture_output=True,
        text=True,
        cwd=work_dir,
    )
    if pandoc_result.returncode != 0:
        print(f"Pandoc warning/error (non-fatal): {pandoc_result.stderr}")
        # Don't fail the whole request — PDF is still available

    # ── Bundle into ZIP ───────────────────────────────────────
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        if os.path.exists(pdf_path):
            zf.write(pdf_path, f"{output_name}.pdf")
        if os.path.exists(docx_path):
            zf.write(docx_path, f"{output_name}.docx")

    return zip_path, work_dir


# ─────────────────────────────────────────────
# Endpoint
# ─────────────────────────────────────────────
@app.post("/generate")
async def generate_paper(req: GenerateRequest):
    """
    Accepts a Google Sheets link, fetches question data, generates a
    formatted PDF with:
      - Numbered questions with A/B/C/D options
      - Inline LaTeX inside $...$ and block LaTeX inside $$...$$
      - Answer key on the final page
    Returns the PDF as a downloadable file.
    """
    sheet_id, gid = extract_sheet_id(req.sheet_link)
    if not sheet_id:
        raise HTTPException(
            status_code=400,
            detail="Could not parse a Google Sheets ID from the provided link."
        )

    # Fetch title and data
    title = get_sheet_title(sheet_id)
    rows  = fetch_sheet_as_csv(sheet_id, gid)

    if not rows:
        raise HTTPException(status_code=400, detail="No question rows found in the sheet.")

    # Build LaTeX
    tex_content = build_latex_document(rows, title)

    # Compile PDF + generate DOCX + bundle ZIP
    safe_name = re.sub(r'[^\w\-]', '_', title)[:80] or "paper"
    zip_path, work_dir = compile_latex(tex_content, safe_name)

    # Stream back the ZIP
    response = FileResponse(
        path=zip_path,
        media_type='application/zip',
        filename=f"{safe_name}.zip",
        background=None,
    )

    # We can't delete work_dir while streaming; Railway's ephemeral FS
    # will clean up; but on long-running servers schedule a cleanup.
    # For now, leave in /tmp — OS will reclaim eventually.

    return response


@app.get("/health")
def health():
    return {"status": "ok"}
