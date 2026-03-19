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
    text = text.replace('\u2212', '-')
    text = text.replace('\u2018', "'").replace('\u2019', "'")
    text = text.replace('\u201c', '"').replace('\u201d', '"')
    text = text.replace('\u2013', '--')
    text = text.replace('\u2014', '---')
    text = text.replace('\u00a0', ' ')
    return text


def download_image(url: str) -> str | None:
    """Downloads an image to ASSETS_DIR and returns the local path (or None on failure)."""
    if not isinstance(url, str) or not url.startswith('http'):
        return None

    try:
        if 'drive.google.com' in url and '/view' in url:
            match = re.search(r'/file/d/([^/]+)', url)
            if match:
                file_id = match.group(1)
                url = f'https://drive.google.com/uc?export=download&id={file_id}'

        filename = hashlib.md5(url.encode()).hexdigest() + '.jpg'
        path = os.path.join(ASSETS_DIR, filename)

        if os.path.exists(path):
            return path

        print(f"Downloading image: {url}...")
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
      - $$...$$  -> raw LaTeX block math (passed through)
      - $...$    -> raw LaTeX inline math (passed through)
      - #img-URL# -> downloads image and inserts \\includegraphics
      - plain text -> fully escaped for LaTeX
    """
    if not content:
        return ''

    content = normalize_unicode(content)

    # Step 1: Replace #img-URL# with placeholders, download images
    image_map = {}
    img_counter = [0]

    def _replace_img(m):
        url = m.group(1).strip()
        key = f'IMGPLACEHOLDER{img_counter[0]}ENDIMG'
        img_counter[0] += 1
        local_path = download_image(url)
        if local_path:
            image_map[key] = (
                r'\newline \includegraphics[max width=\linewidth]{'
                + local_path + '}'
            )
        else:
            image_map[key] = '[Image]'
        return key

    content = re.sub(r'#img-(.*?)#', _replace_img, content)

    # Step 2: Split on math delimiters, escape plain text
    pattern = r'(\$\$.*?\$\$|\$.*?\$)'
    parts = re.split(pattern, content, flags=re.DOTALL)

    result = ''
    for part in parts:
        if part is None:
            continue
        if part.startswith('$$') and part.endswith('$$'):
            inner = part[2:-2].replace('\n', ' ').replace('\r', ' ').strip()
            result += f'$${inner}$$'
        elif part.startswith('$') and part.endswith('$') and len(part) >= 2:
            inner = part[1:-1].replace('\n', ' ').replace('\r', ' ').strip()
            result += f'${inner}$'
        else:
            if part:
                result += escape_latex_text(part)

    # Step 3: Restore image placeholders (they were escaped, undo that)
    for key, latex_cmd in image_map.items():
        result = result.replace(escape_latex_text(key), latex_cmd)
        result = result.replace(key, latex_cmd)

    return result


def build_latex_document(rows: list[dict], title: str) -> str:
    """
    Builds a complete XeLaTeX document:
      - Two-column page layout (multicols)
      - Each question in a bordered tabularx table
      - 3 columns: Q.No | SR No. | Question + Options
      - Answer key in compact multi-column list
    """

    has_any_sr_no = any(str(row.get('SR_NO', '')).strip() for row in rows)

    preamble = r"""\documentclass[9pt,a4paper]{article}
\usepackage{geometry}
\geometry{top=1.27cm, bottom=1.27cm, left=1.27cm, right=1.27cm}
\usepackage{amsmath}
\usepackage{amsfonts}
\usepackage{amssymb}
\usepackage{graphicx}
\usepackage[export]{adjustbox}
\usepackage{tabularx}
\usepackage{multicol}
\usepackage{enumitem}
\usepackage{array}
\usepackage{xltxtra}
\usepackage{fontspec}
\setmainfont{Latin Modern Roman}

\setlength{\parskip}{0pt}
\setlength{\parindent}{0pt}
\setlength{\columnsep}{0.6cm}
\setlength{\tabcolsep}{4pt}
\renewcommand{\arraystretch}{1.3}

\begin{document}
"""

    # Title block (full width, above the two columns)
    escaped_title = escape_latex_text(title)
    header = (
        r'\begin{center}' + '\n'
        r'{\LARGE\textbf{' + escaped_title + r'}}\\[0.3em]' + '\n'
        r'\end{center}' + '\n'
        r'\noindent\rule{\linewidth}{0.4pt}' + '\n'
        r'\vspace{0.3em}' + '\n\n'
    )

    # ── Questions section (two-column) ──────────────────────────
    body = r'\begin{multicols}{2}' + '\n'
    body += r'\raggedcolumns' + '\n\n'

    answer_data = []

    for i, row in enumerate(rows, start=1):
        raw_sr_no = str(row.get('SR_NO', '')).strip()
        safe_sr_no = escape_latex_text(raw_sr_no) if raw_sr_no else ''

        q_text = process_content(str(row.get('Question_Text', '')).strip())
        opt_a  = process_content(str(row.get('Option_A',  '')).strip())
        opt_b  = process_content(str(row.get('Option_B',  '')).strip())
        opt_c  = process_content(str(row.get('Option_C',  '')).strip())
        opt_d  = process_content(str(row.get('Option_D',  '')).strip())
        answer = process_content(str(row.get('Correct_Answer', '')).strip())

        # Build question cell content (question text + options below)
        cell = q_text

        opt_parts = []
        for opt, label in [(opt_a, 'a'), (opt_b, 'b'), (opt_c, 'c'), (opt_d, 'd')]:
            if opt:
                opt_parts.append(f'({label})~{opt}')

        if opt_parts:
            # Arrange options: 2 per line
            opt_lines = []
            for j in range(0, len(opt_parts), 2):
                pair = opt_parts[j:j+2]
                opt_lines.append(' \\hfill '.join(pair))
            cell += r' \newline {\small ' + r' \newline '.join(opt_lines) + '}'

        # Individual bordered table for this question
        body += r'\noindent' + '\n'
        if has_any_sr_no:
            body += r'\begin{tabularx}{\columnwidth}{|c|c|X|}' + '\n'
            body += r'\hline' + '\n'
            body += f'\\textbf{{{i}}} & {safe_sr_no} & {cell} \\\\\n'
        else:
            body += r'\begin{tabularx}{\columnwidth}{|c|X|}' + '\n'
            body += r'\hline' + '\n'
            body += f'\\textbf{{{i}}} & {cell} \\\\\n'

        body += r'\hline' + '\n'
        body += r'\end{tabularx}' + '\n'
        body += r'\vspace{0.2em}' + '\n\n'

        answer_data.append((i, safe_sr_no, answer))

    body += r'\end{multicols}' + '\n\n'

    # ── Answer Key (compact multi-column list) ──────────────────
    body += r'\newpage' + '\n'
    body += r'\begin{center}' + '\n'
    body += r'{\Large\textbf{Answer Key}}\\[0.3em]' + '\n'
    body += r'\end{center}' + '\n'
    body += r'\noindent\rule{\linewidth}{0.4pt}' + '\n'
    body += r'\vspace{0.3em}' + '\n\n'

    body += r'\begin{multicols}{4}' + '\n'
    body += r'\small' + '\n'
    body += r'\begin{enumerate}[leftmargin=*, label=\textbf{\arabic*.}]' + '\n'
    for seq, sr, ans in answer_data:
        if sr:
            body += f'  \\item {sr}: {ans}\n'
        else:
            body += f'  \\item {ans}\n'
    body += r'\end{enumerate}' + '\n'
    body += r'\end{multicols}' + '\n\n'

    return preamble + header + body + r'\end{document}' + '\n'
