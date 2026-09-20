"""Rebuilds docs/Technical_Report.pdf from docs/Technical_Report.html using headless Chrome.

    python docs/make_report.py

Chrome's own print engine is used because this machine has no LibreOffice or wkhtmltopdf.
Edit the HTML; this script only converts it.
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC, OUT = HERE / "Technical_Report.html", HERE / "Technical_Report.pdf"
CHROME = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
          "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
          "/usr/bin/google-chrome", "/usr/bin/chromium"]

browser = next((c for c in CHROME if Path(c).exists()), None)
if browser is None:
    sys.exit("No Chrome/Edge/Chromium found — install one, or open the HTML and print to PDF by hand.")
if not SRC.exists():
    sys.exit(f"missing {SRC}")

subprocess.run([browser, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                f"--print-to-pdf={OUT}", str(SRC)], check=True, capture_output=True)
pages = open(OUT, "rb").read().count(b"/Type /Page") or open(OUT, "rb").read().count(b"/Type/Page")
print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB, ~{pages} pages)")
