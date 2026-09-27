FROM python:3.12-slim

# fonts-wqy-microhei (WenQuanYi Micro Hei) provides Traditional Chinese
# coverage for PDF rendering (see src/report_pdf.py).
#
# NOTE: fonts-noto-cjk was tried first (per the original task instructions)
# but had to be dropped — verified 2026-07-14 that reportlab's TTFont loader
# only supports TrueType (glyf) outlines, while every weight of Noto
# Sans/Serif CJK shipped by Debian's fonts-noto-cjk package uses
# OpenType/CFF outlines. Loading it raises
# `TTFError('... postscript outlines are not supported')` for every
# subfontIndex (0-9 all tried), so it's a hard format incompatibility, not
# an index-selection issue. fonts-wqy-microhei is genuine TrueType and
# loads successfully; it ships a single weight, so REGULAR and BOLD both
# point at the same file — reportlab renders CJK bold text using the
# regular glyphs rather than true bold, which is an accepted tradeoff over
# a hard font-load failure.
# chromium：main 分支的 v9 報告是用 headless Chromium 把 HTML 印成 PDF
# （src/report_html_v9.py，程式會帶 --no-sandbox 並在 PATH 找 chromium）
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fonts-wqy-microhei \
        fontconfig \
        chromium \
    && rm -rf /var/lib/apt/lists/*

# Fail the build early and loudly if the expected CJK font file is missing,
# instead of discovering it later at PDF-generation time inside the app.
RUN fc-list | grep -i "WenQuanYi Micro Hei" \
    && test -f /usr/share/fonts/truetype/wqy/wqy-microhei.ttc

ENV REPORT_FONT_REGULAR=/usr/share/fonts/truetype/wqy/wqy-microhei.ttc
ENV REPORT_FONT_BOLD=/usr/share/fonts/truetype/wqy/wqy-microhei.ttc
ENV REPORT_FONT_REGULAR_INDEX=0
ENV REPORT_FONT_BOLD_INDEX=0

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Belt-and-suspenders: src/api/pipeline.py already creates this at import
# time via os.makedirs(OUTPUT_DIR, exist_ok=True), but ensure it exists
# even if that ever changes.
RUN mkdir -p output

EXPOSE 8000

CMD ["python", "server.py"]
