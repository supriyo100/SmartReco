#!/usr/bin/env python3
"""collect_helper.py — fetch ONE course page/PDF, print a cleaned text dump
for fast manual copy into seed JSON. This is deliberately NOT a crawler:
it does zero extraction logic, no auto-populated fields, no batch mode.
You read the output and type the JSON yourself — that reading IS the QA
step that caught the objectives/syllabus mismatch on the cloud bootcamp.

Usage:
  python collect_helper.py <url>              # HTML page
  python collect_helper.py <url> --pdf        # PDF syllabus (e.g. bit.ly links)

Install once: pip install httpx beautifulsoup4 pypdf --break-system-packages
"""
import re
import sys

import httpx
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def dump_html(url: str):
    r = httpx.get(url, headers={"User-Agent": UA}, follow_redirects=True, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "svg"]):
        tag.decompose()
    text = soup.get_text("\n")
    text = re.sub(r"\n{2,}", "\n", text)
    text = "\n".join(line.strip() for line in text.splitlines() if line.strip())

    print(f"=== {url} ===\n")
    price = re.findall(r"₹\s?[\d,]+", text)
    if price:
        print("PRICE candidates:", price)
    mentors = re.findall(r"Mentors?:.*", text)
    if mentors:
        print("MENTORS line:", mentors[0])
    print("\n--- full cleaned text (read for objectives / modules / perks) ---\n")
    print(text)


def dump_pdf(url: str):
    from pypdf import PdfReader
    r = httpx.get(url, headers={"User-Agent": UA}, follow_redirects=True, timeout=30)
    r.raise_for_status()
    with open("/tmp/_syllabus.pdf", "wb") as f:
        f.write(r.content)
    reader = PdfReader("/tmp/_syllabus.pdf")
    print(f"=== {url} ({len(reader.pages)} pages) ===\n")
    for i, page in enumerate(reader.pages):
        t = page.extract_text() or ""
        if t.strip():
            print(f"--- page {i+1} ---")
            print(t.strip())
            print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    url = sys.argv[1]
    if "--pdf" in sys.argv or url.lower().endswith(".pdf"):
        dump_pdf(url)
    else:
        dump_html(url)
