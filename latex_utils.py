import re
import os
import hashlib
import requests

ASSETS_DIR = "/tmp/paper_assets"
os.makedirs(ASSETS_DIR, exist_ok=True)


def escape_latex_text(text: str) -> str:
    """
    Escapes special LaTeX characters in plain text mode.
    Backslash MUST be replaced first so we don't double-escape later replacements.
    """
    text = text.replace('\\', r'\textbackslash{}')
    replacements = {
        '&':  r'\&',
        '%':  r'\%',
        '$':  r'\$',
        '#':  r'\#',
        '_':  r'\_',
        '{':  r'\{',
        '}':  r'\}',
        '~':  r'\textasciitilde{}',
        '^':  r'\textasciicircum{}',
    }
    for char, replacement in replacements.items():
        text = text.replace(char, replacement)
    return text


def normalize_unicode(text: str) -> str:
    """Replace common unicode typographic characters with ASCII equivalents."""
    text = text.replace('\u2212', '-')          # unicode minus → hyphen
    text = text.replace('\u2018', "'").replace('\u2019', "'")   # smart single quotes
    text = text.replace('\u201c', '"').replace('\u201d', '"')   # smart double quotes
    text = text.replace('\u2013', '--')         # en-dash
    text = text.replace('\u2014', '---')        # em-dash
    return text


def download_image(url: str) -> str | None:
    """Downloads an image to ASSETS_DIR and returns the local path (or None on failure)."""
    if not isinstance(url, str) or not url.startswith('http'):
        return None

    try:
        # Transform Google Drive view links to direct download URLs
        if 'drive.google.com' in url and '/view' in url:
            match = re.search(r'/file/d/([^/]+)', url)
            if match:
                file_id = match.group(1)
                url = f'https://drive.google.com/uc?export=download&id={file_id}'

        filename = hashlib.md5(url.encode()).hexdigest() + '.jpg'
        path = os.path.join(ASSETS_DIR, filename)

        if os.path.exists(path):
            return path

        print(f"Downloading image: {url}…")
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200:
            with open(path, 'wb') as f:
                f.write(resp.content)
            return path
    except Exception as e:
        print(f"Image download failed ({url}): {e}")

    return None


def process_content(content: str) -> str:
    """
    Parses mixed content:
      - $$...$$  → raw LaTeX block math (passed through unchanged)
      - $...$    → raw LaTeX inline math (passed through unchanged)
      - plain text → fully escaped for LaTeX

    Uses re.DOTALL so math can span multiple lines inside the delimiters.
    """
    if not content:
        return ''

    content = normalize_unicode(content)

    # Split on $$...$$ first, then $...$
    # Using re.split with capturing groups so we keep the delimiters in the list
    pattern = r'(\$\$.*?\$\$|\$.*?\$)'
    parts = re.split(pattern, content, flags=re.DOTALL)

    result = ''
    for part in parts:
        if part is None:
            continue
        if part.startswith('$$') and part.endswith('$$'):
            # Block math — strip internal newlines to prevent LaTeX blank-line errors
            inner = part[2:-2].replace('\n', ' ').replace('\r', ' ').strip()
            result += f'$${inner}$$'
        elif part.startswith('$') and part.endswith('$') and len(part) >= 2:
            # Inline math — strip internal newlines
            inner = part[1:-1].replace('\n', ' ').replace('\r', ' ').strip()
            result += f'${inner}$'
        else:
            # Plain text — escape it
            if part:
                result += escape_latex_text(part)

    return result


def build_latex_document(rows: list[dict], title: str) -> str:
    """
    Builds a complete XeLaTeX document string.

    rows: list of dicts with keys:
        Question_Text, Option_A, Option_B, Option_C, Option_D, Correct_Answer

    The document contains:
      1. Numbered questions with (a)(b)(c)(d) options
      2. An Answer Key section at the very end
    """

    preamble = r"""\documentclass[12pt,a4paper]{article}
\usepackage{geometry}
\geometry{top=1in, bottom=1in, left=1in, right=1in}
\usepackage{amsmath}
\usepackage{amsfonts}
\usepackage{amssymb}
\usepackage{graphicx}
\usepackage[export]{adjustbox}
\usepackage{enumitem}
\usepackage{parskip}
\usepackage{setspace}
\onehalfspacing
\usepackage{xltxtra}
\usepackage{fontspec}
\setmainfont{Latin Modern Roman}

\begin{document}
"""

    # Title block
    escaped_title = escape_latex_text(title)
    header = (
        r'\begin{center}' + '\n'
        r'{\LARGE\textbf{' + escaped_title + r'}}\\[0.4em]' + '\n'
        r'\end{center}' + '\n'
        r'\noindent\rule{\linewidth}{0.4pt}' + '\n'
        r'\vspace{0.5em}' + '\n\n'
    )

    # Questions section
    questions_body = r'\section*{Questions}' + '\n'
    questions_body += r'\begin{enumerate}[leftmargin=*, label=\textbf{\arabic*.}]' + '\n'

    answer_lines = []

    for i, row in enumerate(rows, start=1):
        q_text = process_content(str(row.get('Question_Text', '')).strip())
        opt_a  = process_content(str(row.get('Option_A',  '')).strip())
        opt_b  = process_content(str(row.get('Option_B',  '')).strip())
        opt_c  = process_content(str(row.get('Option_C',  '')).strip())
        opt_d  = process_content(str(row.get('Option_D',  '')).strip())
        answer = process_content(str(row.get('Correct_Answer', '')).strip())

        # Question text
        questions_body += f'\\item {q_text}\n'

        # Options — only render non-empty options
        opts = [opt_a, opt_b, opt_c, opt_d]
        if any(opts):
            questions_body += r'    \begin{enumerate}[label=(\alph*), topsep=2pt, itemsep=0pt]' + '\n'
            for opt in opts:
                if opt:
                    questions_body += f'        \\item {opt}\n'
            questions_body += r'    \end{enumerate}' + '\n'

        questions_body += r'    \vspace{0.6em}' + '\n'

        # Collect answer for key
        answer_lines.append((i, answer))

    questions_body += r'\end{enumerate}' + '\n\n'

    # Answer Key section
    answer_key_body = r'\newpage' + '\n'
    answer_key_body += r'\section*{Answer Key}' + '\n'
    answer_key_body += r'\noindent\rule{\linewidth}{0.4pt}' + '\n'
    answer_key_body += r'\vspace{0.5em}' + '\n\n'
    answer_key_body += r'\begin{enumerate}[leftmargin=*, label=\textbf{\arabic*.}]' + '\n'
    for num, ans in answer_lines:
        answer_key_body += f'    \\item {ans}\n'
    answer_key_body += r'\end{enumerate}' + '\n\n'

    return preamble + header + questions_body + answer_key_body + r'\end{document}' + '\n'
