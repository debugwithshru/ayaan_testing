# ─── Base image ───────────────────────────────────────────────
FROM ubuntu:22.04

# Avoid interactive prompts during apt installs
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Kolkata

# ─── System dependencies ──────────────────────────────────────
# texlive-xetex   → xelatex binary
# texlive-fonts-recommended + texlive-latex-extra → common LaTeX packages
#   (includes latin modern, enumitem, setspace, parskip, geometry, amsmath, etc.)
# fonts-lmodern   → Latin Modern fonts (used as the main font - no system font needed)
# python3 + pip
# curl            → healthcheck / debugging
RUN apt-get update && apt-get install -y --no-install-recommends \
    texlive-xetex \
    texlive-fonts-recommended \
    texlive-latex-extra \
    texlive-science \
    fonts-lmodern \
    python3 \
    python3-pip \
    curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ─── App setup ────────────────────────────────────────────────
WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY app.py .
COPY latex_utils.py .

# Temp directory for generated assets / PDFs
RUN mkdir -p /tmp/paper_assets

# ─── Run ──────────────────────────────────────────────────────
# Railway sets $PORT automatically; default to 8000
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
