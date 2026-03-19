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
      - $$...$$  -> raw LaTeX block math
      - $...$    -> raw LaTeX inline math
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

    # Step 3: Restore image placeholders
    for key, latex_cmd in image_map.items():
        result = result.replace(escape_latex_text(key), latex_cmd)
        result = result.replace(key, latex_cmd)

    return result


def build_latex_document(rows: list[dict], title: str, for_docx: bool = False, test_title: str | None = None) -> str:
    """
    Builds a complete XeLaTeX document optimized for either PDF or Word (via Pandoc).
    """

    has_any_sr_no = any(str(row.get('SR_NO', '')).strip() for row in rows)

    # Logo Setup
    logo_path = "/app/COCOON_LOGO.png"
    if not os.path.exists(logo_path):
        if os.path.exists("COCOON_LOGO.webp"):
            logo_path = os.path.abspath("COCOON_LOGO.webp")
        elif os.path.exists("COCOON_LOGO.png"):
            logo_path = os.path.abspath("COCOON_LOGO.png")

    if for_docx:
        # Preamble for Word output
        preamble = r"""\documentclass[11pt,a4paper]{article}
\usepackage{geometry}
\geometry{top=1.27cm, bottom=1.27cm, left=1.27cm, right=1.27cm}
\usepackage{amsmath}
\usepackage{amsfonts}
\usepackage{amssymb}
\usepackage{graphicx}
\usepackage[export]{adjustbox}
\usepackage{longtable}
\usepackage{tabularx}
\usepackage{array}
\usepackage{xltxtra}
\usepackage{fontspec}
\setmainfont{Latin Modern Roman}

\setlength{\parskip}{6pt}
\setlength{\parindent}{0pt}
\renewcommand{\arraystretch}{1.5}

\begin{document}
"""
    else:
        # Preamble for PDF (Two column)
        preamble = r"""\documentclass[11pt,a4paper]{article}
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

    # ── Header ──────────────────────────────────────────────
    header_title = escape_latex_text("COCOON GROUP TUITIONS")
    header_subtitle = escape_latex_text(test_title or title)
    
    if for_docx:
        # Word needs a TABLE for the header to keep Logo left and Text center
        header = r'\begin{center}' + '\n'
        header += r'  \begin{tabular}{@{}m{2.5cm}m{\dimexpr\textwidth-3cm\relax}@{}}' + '\n'
        if os.path.exists(logo_path) or logo_path == "/app/COCOON_LOGO.png":
            header += rf'    \includegraphics[width=2cm]{{{logo_path}}} &' + '\n'
        else:
            header += r'    [LOGO] &' + '\n'
        header += r'    \begin{center}' + '\n'
        header += rf'      {{\LARGE\textbf{{{header_title}}}}}\\[0.2em]' + '\n'
        header += rf'      {{\Large\textbf{{{header_subtitle}}}}}' + '\n' # Bold title
        header += r'    \end{center}' + '\n'
        header += r'  \end{tabular}' + '\n'
        header += r'\end{center}' + '\n'
    else:
        # PDF Header (Minipage)
        header = r'\begin{center}' + '\n'
        header += r'  \begin{minipage}{0.15\textwidth}' + '\n'
        if os.path.exists(logo_path) or logo_path == "/app/COCOON_LOGO.png":
            header += rf'    \includegraphics[width=\textwidth]{{{logo_path}}}' + '\n'
        else:
            header += r'    [LOGO]' + '\n'
        header += r'  \end{minipage}' + '\n'
        header += r'  \begin{minipage}{0.8\textwidth}' + '\n'
        header += r'    \begin{center}' + '\n'
        header += rf'      {{\LARGE\textbf{{{header_title}}}}}\\[0.2em]' + '\n'
        header += rf'      {{\Large\textbf{{{header_subtitle}}}}}' + '\n' # Bold title
        header += r'    \end{center}' + '\n'
        header += r'  \end{minipage}' + '\n'
        header += r'\end{center}' + '\n'

    header += r'\noindent\rule{\linewidth}{0.4pt}' + '\n'
    header += r'\vspace{0.3em}' + '\n\n'

    content = ""
    answer_data = []

    if for_docx:
        # Word-optimized: Individual tables per question for cleaner conversion
        for i, row in enumerate(rows, start=1):
            raw_sr_no = str(row.get('SR_NO', '')).strip()
            safe_sr_no = escape_latex_text(raw_sr_no) if raw_sr_no else ''
            q_text = process_content(str(row.get('Question_Text', '')).strip())
            opt_a  = process_content(str(row.get('Option_A',  '')).strip())
            opt_b  = process_content(str(row.get('Option_B',  '')).strip())
            opt_c  = process_content(str(row.get('Option_C',  '')).strip())
            opt_d  = process_content(str(row.get('Option_D',  '')).strip())
            answer = process_content(str(row.get('Correct_Answer', '')).strip())

            # Options grid for Word
            option_grid = r' \begin{tabular}{@{}p{0.45\linewidth}p{0.45\linewidth}@{}}' + '\n'
            option_grid += f' (a)~{opt_a} & (b)~{opt_b} \\\\' + '\n'
            option_grid += f' (c)~{opt_c} & (d)~{opt_d} \\\\' + '\n'
            option_grid += r'\end{tabular}'
            
            cell = q_text + r' \newline ' + option_grid

            content += r'\noindent' + '\n'
            if has_any_sr_no:
                # Use tabularx for Word too if possible, Pandoc handles simple tabularx
                content += r'\begin{tabularx}{\textwidth}{|c|c|X|}' + '\n'
                content += r'\hline' + '\n'
                content += f'\\textbf{{{i}}} & {safe_sr_no} & {cell} \\\\' + '\n'
            else:
                content += r'\begin{tabularx}{\textwidth}{|c|X|}' + '\n'
                content += r'\hline' + '\n'
                content += f'\\textbf{{{i}}} & {cell} \\\\' + '\n'
            content += r'\hline' + '\n'
            content += r'\end{tabularx}' + '\n'
            content += r'\vspace{0.3em}' + '\n\n'
            answer_data.append((i, safe_sr_no, answer))

    else:
        # PDF-optimized layout
        content += r'\begin{multicols}{2}' + '\n'
        content += r'\raggedcolumns' + '\n\n'

        for i, row in enumerate(rows, start=1):
            raw_sr_no = str(row.get('SR_NO', '')).strip()
            safe_sr_no = escape_latex_text(raw_sr_no) if raw_sr_no else ''
            q_text = process_content(str(row.get('Question_Text', '')).strip())
            opt_a  = process_content(str(row.get('Option_A',  '')).strip())
            opt_b  = process_content(str(row.get('Option_B',  '')).strip())
            opt_c  = process_content(str(row.get('Option_C',  '')).strip())
            opt_d  = process_content(str(row.get('Option_D',  '')).strip())
            answer = process_content(str(row.get('Correct_Answer', '')).strip())

            # Options for PDF: Balanced 2x2 grid
            option_grid = r' \begin{tabular}{@{}p{0.45\linewidth}p{0.45\linewidth}@{}}' + '\n'
            option_grid += f' (a)~{opt_a} & (b)~{opt_b} \\\\' + '\n'
            option_grid += f' (c)~{opt_c} & (d)~{opt_d} \\' + '\n'
            option_grid += r'\end{tabular}'

            cell = q_text + r' \newline {\small ' + option_grid + '}'

            content += r'\noindent' + '\n'
            if has_any_sr_no:
                content += r'\begin{tabularx}{\columnwidth}{|c|c|X|}' + '\n'
                content += r'\hline' + '\n'
                content += f'\\textbf{{{i}}} & {safe_sr_no} & {cell} \\\\\n'
            else:
                content += r'\begin{tabularx}{\columnwidth}{|c|X|}' + '\n'
                content += r'\hline' + '\n'
                content += f'\\textbf{{{i}}} & {cell} \\\\\n'

            content += r'\hline' + '\n'
            content += r'\end{tabularx}' + '\n'
            content += r'\vspace{0.2em}' + '\n\n'
            answer_data.append((i, safe_sr_no, answer))

        content += r'\end{multicols}' + '\n\n'

    # Answer Key section
    content += r'\newpage' + '\n'
    content += r'\begin{center}' + '\n'
    content += r'{\Large\textbf{Answer Key}}\\[0.3em]' + '\n'
    content += r'\end{center}' + '\n'
    content += r'\noindent\rule{\linewidth}{0.4pt}' + '\n'
    content += r'\vspace{0.3em}' + '\n\n'

    content += r'\begin{multicols}{4}' + '\n'
    content += r'\small' + '\n'
    content += r'\begin{enumerate}[leftmargin=*, label=\textbf{\arabic*.}]' + '\n'
    for seq, sr, ans in answer_data:
        if sr:
            content += f'  \\item {sr}: {ans}\n'
        else:
            content += f'  \\item {ans}\n'
    content += r'\end{enumerate}' + '\n'
    content += r'\end{multicols}' + '\n\n'

    return preamble + header + content + r'\end{document}' + '\n'
