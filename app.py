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
from pydantic import BaseModel, Field, AliasChoices, model_validator
import random

class GenerateRequest(BaseModel):
    sheet_link: str = Field(..., alias="sheet link", validation_alias=AliasChoices("sheet link", "sheet_link"))
    email: str | None = None
    title_name: str | None = Field(None, alias="Title Name ", validation_alias=AliasChoices("Title Name ", "Title Name", "title_name"))
    difficulty: list[str] | None = Field(None, alias="DIFFICULTY", validation_alias=AliasChoices("DIFFICULTY", "difficulty"))
    question_amount: str | int | None = Field(None, alias="QUESTION AMOUNT ", validation_alias=AliasChoices("QUESTION AMOUNT ", "QUESTION AMOUNT", "question_amount"))

    @model_validator(mode='before')
    @classmethod
    def normalize_keys(cls, data: any) -> any:
        if not isinstance(data, dict):
            return data
        
        mapping = {
            "sheet_link": ["sheet link", "sheet_link"],
            "title_name": ["Title Name ", "Title Name", "title_name", "title name"],
            "difficulty": ["DIFFICULTY", "difficulty", "DIFFICULTY ", "difficulty "],
            "question_amount": ["QUESTION AMOUNT ", "QUESTION AMOUNT", "question_amount", "question amount"]
        }
        
        for canonical, variants in mapping.items():
            for variant in variants:
                if variant in data and data[variant] is not None:
                    data[canonical] = data[variant]
                    break
        return data

    class Config:
        populate_by_name = True


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
        'DIFFICULTY': ['DIFFICULTY', 'Difficulty', 'difficulty', 'DIFFICULTY LEVEL', 'Difficulty Level', 'difficulty level'],
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
    
    print(f"DEBUG: CSV Mapping -> {mapping}")

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
    
    if rows:
        print(f"DEBUG: Sample Row Keys -> {list(rows[0].keys())}")
    
    return rows


def compile_latex(pdf_tex: str, docx_tex: str, output_name: str) -> tuple[str, str]:
    """
    Writes the .tex content to a temp directory, runs xelatex twice
    for the PDF, and uses the docx_tex with pandoc for the DOCX.
    Bundles both into a ZIP and returns the path.
    """
    work_dir = tempfile.mkdtemp(prefix="paper_")
    pdf_tex_path = os.path.join(work_dir, f"{output_name}_pdf.tex")
    docx_tex_path = os.path.join(work_dir, f"{output_name}_docx.tex")
    pdf_output_path = os.path.join(work_dir, f"{output_name}_pdf.pdf")
    docx_path = os.path.join(work_dir, f"{output_name}.docx")
    zip_path = os.path.join(work_dir, f"{output_name}.zip")

    with open(pdf_tex_path, 'w', encoding='utf-8') as f:
        f.write(pdf_tex)
    with open(docx_tex_path, 'w', encoding='utf-8') as f:
        f.write(docx_tex)

    # ── XeLaTeX compilation (PDF) ─────────────────────────────
    xelatex_cmd = [
        'xelatex',
        '-interaction=nonstopmode',
        '-halt-on-error',
        f'-output-directory={work_dir}',
        pdf_tex_path,
    ]

    for run in range(2):  # two passes
        result = subprocess.run(
            xelatex_cmd,
            capture_output=True,
            text=True,
            cwd=work_dir,
        )
        if result.returncode != 0 and run == 1:
            log_path = os.path.join(work_dir, f"{output_name}_pdf.log")
            log_excerpt = "No log found."
            if os.path.exists(log_path):
                with open(log_path, 'r', encoding='utf-8', errors='replace') as lf:
                    lines = lf.readlines()
                # Find error lines (typically start with !) 
                # or just give the last 50 lines for context
                err_lines = [l for l in lines if l.startswith('!')]
                if err_lines:
                    log_excerpt = ''.join(err_lines[-10:]) + "\n...\n" + ''.join(lines[-20:])
                else:
                    log_excerpt = ''.join(lines[-40:])
            
            raise HTTPException(
                status_code=500,
                detail=f"XeLaTeX compilation failed. Log excerpt:\n{log_excerpt}"
            )

    if not os.path.exists(pdf_output_path):
        raise HTTPException(status_code=500, detail="PDF not found after compilation.")

    # ── Pandoc conversion (DOCX) ──────────────────────────────
    # Using the optimized docx_tex_path for Word
    pandoc_cmd = [
        'pandoc',
        docx_tex_path,
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
        if os.path.exists(pdf_output_path):
            zf.write(pdf_output_path, f"{output_name}.pdf")
        if os.path.exists(docx_path):
            zf.write(docx_path, f"{output_name}.docx")

    return zip_path, work_dir


# ─────────────────────────────────────────────
# Endpoint
# ─────────────────────────────────────────────
from fastapi import Request

@app.post("/generate")
async def generate_paper(data: any, request: Request):
    """
    Accepts a Google Sheets link, fetches question data, filters by difficulty,
    randomizes order, and generates a formatted PDF + DOCX in a ZIP.
    """
    # 1. Log Raw JSON to see exactly what keys are being sent
    try:
        raw_json = await request.json()
        if isinstance(raw_json, list) and raw_json:
            print(f"DEBUG: Raw JSON Keys (first item): {list(raw_json[0].keys())}")
        elif isinstance(raw_json, dict):
            print(f"DEBUG: Raw JSON Keys: {list(raw_json.keys())}")
    except Exception as e:
        print(f"DEBUG: Could not parse raw JSON for logging: {e}")

    # 2. Extract GenerateRequest object
    req = None
    try:
        if isinstance(data, list) and data:
            item = data[0]
            req = GenerateRequest(**item) if isinstance(item, dict) else item
        elif isinstance(data, dict):
            req = GenerateRequest(**data)
        else:
            req = data
    except Exception as e:
        print(f"DEBUG: Pydantic parsing failed: {e}")
        raise HTTPException(status_code=400, detail=f"Request parsing failed: {e}")

    if not req:
        raise HTTPException(status_code=400, detail="Invalid request data.")

    print(f"DEBUG: Parsed Request -> Title: '{req.title_name}', Diff: {req.difficulty}, Amount: {req.question_amount}")

    sheet_id, gid = extract_sheet_id(req.sheet_link)
    if not sheet_id:
        raise HTTPException(
            status_code=400,
            detail="Could not parse a Google Sheets ID from the provided link."
        )

    # 2. Fetch data from Google Sheets
    title = get_sheet_title(sheet_id)
    rows  = fetch_sheet_as_csv(sheet_id, gid)

    if not rows:
        raise HTTPException(status_code=400, detail="No question rows found in the sheet.")
    
    print(f"DEBUG: Fetched {len(rows)} raw rows from sheet.")

    # 3. Filter by Difficulty
    if req.difficulty:
        req_diffs = [d.strip() for d in req.difficulty if d.strip()]
        if req_diffs:
            # Primary pool: matching difficulties
            pool = [r for r in rows if str(r.get('DIFFICULTY', '')).strip() in req_diffs]
            print(f"DEBUG: Found {len(pool)} matching rows for {req_diffs}")
            
            # Fill with fallback if not enough questions matching criteria
            limit = None
            if req.question_amount:
                try:
                    limit = int(req.question_amount)
                except (ValueError, TypeError):
                    pass
            
            if limit and len(pool) < limit:
                others = [r for r in rows if r not in pool]
                random.shuffle(others)
                pool.extend(others)
                print(f"DEBUG: Fallback applied. Total pool is now {len(pool)} rows.")
            
            rows = pool

    # 4. Global Randomization
    random.shuffle(rows)

    # 5. Question Amount Limiting
    if req.question_amount:
        try:
            limit = int(req.question_amount)
            print(f"DEBUG: Limiting document to {limit} questions.")
            rows = rows[:limit]
        except (ValueError, TypeError):
            print(f"DEBUG: Failed to parse limit: {req.question_amount}")
            pass

    print(f"DEBUG: Final row count for paper: {len(rows)}")

    if not rows:
        raise HTTPException(status_code=400, detail="No questions matched the request after filtering.")

    # 6. Build LaTeX
    test_title = req.title_name or title
    pdf_tex_content  = build_latex_document(rows, title, for_docx=False, test_title=test_title)
    docx_tex_content = build_latex_document(rows, title, for_docx=True, test_title=test_title)

    # 7. Compilation and ZIP creation
    safe_name = re.sub(r'[^\w\-]', '_', test_title)[:80] or "paper"
    zip_path, work_dir = compile_latex(pdf_tex_content, docx_tex_content, safe_name)
    
    # 8. Return response
    return FileResponse(
        path=zip_path,
        media_type='application/zip',
        filename=f"{safe_name}.zip",
    )


@app.get("/health")
def health():
    return {"status": "ok"}
