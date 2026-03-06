import os
import re
import csv
import uuid
import shutil
import subprocess
import tempfile
import traceback

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
def extract_sheet_id(url: str) -> str | None:
    """Extract the Google Sheets spreadsheet ID from any common URL format."""
    match = re.search(r'/spreadsheets/d/([a-zA-Z0-9_-]+)', url)
    return match.group(1) if match else None


def get_sheet_title(sheet_id: str) -> str:
    """
    Try to get the human-readable title of a public Google Sheet.
    Falls back to the sheet ID if it cannot be determined.
    """
    try:
        # Google Sheets HTML page contains the title in <title>
        url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"
        resp = requests.get(url, timeout=10, allow_redirects=True)
        match = re.search(r'<title>(.*?) - Google Sheets</title>', resp.text)
        if match:
            raw = match.group(1).strip()
            # Sanitise for use as a filename
            safe = re.sub(r'[<>:"/\\|?*]', '_', raw)
            return safe or sheet_id
    except Exception:
        pass
    return sheet_id


def fetch_sheet_as_csv(sheet_id: str) -> list[dict]:
    """
    Exports the first sheet of a public Google Spreadsheet as CSV and
    returns a list of row dicts keyed by the header row.
    """
    export_url = (
        f"https://docs.google.com/spreadsheets/d/{sheet_id}"
        f"/export?format=csv&gid=0"
    )
    resp = requests.get(export_url, timeout=30)
    if resp.status_code != 200:
        raise HTTPException(
            status_code=400,
            detail=f"Could not fetch sheet (HTTP {resp.status_code}). "
                   "Make sure the sheet is publicly accessible (Anyone with the link)."
        )

    # Decode and parse
    text = resp.content.decode('utf-8-sig')  # handles BOM if present
    reader = csv.DictReader(text.splitlines())
    rows = []
    for row in reader:
        # Skip completely empty rows
        if not any(v.strip() for v in row.values()):
            continue
        # Skip repeated header rows embedded in the data
        if row.get('Question_Text', '').strip() == 'Question_Text':
            continue
        rows.append(row)
    return rows


def compile_latex(tex_content: str, output_name: str) -> str:
    """
    Writes the .tex content to a temp directory, runs xelatex twice
    (second pass stabilises page numbers / TOC), and returns the path
    to the generated PDF.
    """
    work_dir = tempfile.mkdtemp(prefix="paper_")
    tex_path = os.path.join(work_dir, f"{output_name}.tex")
    pdf_path = os.path.join(work_dir, f"{output_name}.pdf")

    with open(tex_path, 'w', encoding='utf-8') as f:
        f.write(tex_content)

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

    return pdf_path, work_dir


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
    sheet_id = extract_sheet_id(req.sheet_link)
    if not sheet_id:
        raise HTTPException(
            status_code=400,
            detail="Could not parse a Google Sheets ID from the provided link."
        )

    # Fetch title and data
    title = get_sheet_title(sheet_id)
    rows  = fetch_sheet_as_csv(sheet_id)

    if not rows:
        raise HTTPException(status_code=400, detail="No question rows found in the sheet.")

    # Build LaTeX
    tex_content = build_latex_document(rows, title)

    # Compile
    safe_name = re.sub(r'[^\w\-]', '_', title)[:80] or "paper"
    pdf_path, work_dir = compile_latex(tex_content, safe_name)

    # Stream back and schedule cleanup
    response = FileResponse(
        path=pdf_path,
        media_type='application/pdf',
        filename=f"{safe_name}.pdf",
        background=None,
    )

    # We can't delete work_dir while streaming; Railway's ephemeral FS
    # will clean up; but on long-running servers schedule a cleanup.
    # For now, leave in /tmp — OS will reclaim eventually.

    return response


@app.get("/health")
def health():
    return {"status": "ok"}
