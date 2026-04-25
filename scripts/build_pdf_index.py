# file: scripts/build_pdf_index.py

#!/usr/bin/env python3

from __future__ import annotations

import json

import os

import re

import sys

import time

import urllib.error

import urllib.parse

import urllib.request

from collections import Counter

from dataclasses import dataclass

from difflib import SequenceMatcher

from pathlib import Path

from typing import Any

IA_IDENTIFIER_1 = "vorhofflimmern-bei-bekannter-khk-dr-oemer-dr-remzi-09.05.25"

IA_IDENTIFIER_2 = "FSPneu"

GITHUB_OWNER = "protokolFSP"

GITHUB_REPO = "fsp-audio"

GITHUB_BRANCH = "main"

DOCS_DIR = "docs"

OUTPUT_PATH = Path("docs/pdf-index.json")

DATE_RE = re.compile(r"(?<!\d)(\d{1,2})[.\-_/](\d{1,2})[.\-_/](\d{2,4})(?!\d)")

EXT_RE = re.compile(r"\.(mp3|m4a|wav|aac|ogg|flac|pdf)$", re.IGNORECASE)

SPACE_RE = re.compile(r"\s+")

NON_WORD_RE = re.compile(r"[^0-9a-zA-ZçğıöşüÇĞİÖŞÜäöüÄÖÜß]+")

STOPWORDS = {

    "dr", "doktor", "fr", "frau", "herr", "mit", "und", "ve", "ile",

    "the", "der", "die", "das", "ein", "eine", "zu", "im", "in", "bei",

    "von", "vom", "für", "fur", "am", "an", "auf", "oder", "de", "da",

    "do", "la", "le", "el", "del", "den", "dem", "des", "ibn", "bin",

    "audio", "pdf", "fsp", "kayit", "kayıt"

}

@dataclass

class AudioItem:

    id: str

    src: str

    name: str

    title: str

    filename: str

    normalized_base: str

    date_key: str | None

    tokens: list[str]

@dataclass

class PdfItem:

    name: str

    filename: str

    normalized_base: str

    date_key: str | None

    tokens: list[str]

    url: str

def fetch_json(url: str) -> Any:

    req = urllib.request.Request(

        url,

        headers={

            "User-Agent": "fsp-audio-pdf-index-builder/1.0",

            "Accept": "application/json",

        },

    )

    with urllib.request.urlopen(req, timeout=30) as resp:

        return json.loads(resp.read().decode("utf-8"))

def get_archive_audio(identifier: str, src: str) -> list[AudioItem]:

    url = f"https://archive.org/metadata/{urllib.parse.quote(identifier)}"

    payload = fetch_json(url)

    files = payload.get("files") or []

    out: list[AudioItem] = []

    for i, file_item in enumerate(files):

        name = str(file_item.get("name") or "")

        if not name.lower().endswith((".m4a", ".mp3")):

            continue

        if "source" in file_item and str(file_item.get("source") or "").lower() != "original":

            continue

        title = str(file_item.get("title") or "").strip() or Path(name).name

        filename = urllib.parse.unquote(Path(name).name)

        local_id = name or title or f"{src}-{i}"

        item_id = f"{src}|{local_id}"

        normalized_base = normalize_base(filename)

        out.append(

            AudioItem(

                id=item_id,

                src=src,

                name=name,

                title=title,

                filename=filename,

                normalized_base=normalized_base,

                date_key=extract_date_key(filename),

                tokens=tokenize_name(normalized_base),

            )

        )

    return out

def get_repo_pdfs(owner: str, repo: str, branch: str, docs_dir: str) -> list[PdfItem]:

    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{docs_dir}?ref={urllib.parse.quote(branch)}"

    payload = fetch_json(url)

    out: list[PdfItem] = []

    for entry in payload:

        if str(entry.get("type")) != "file":

            continue

        name = str(entry.get("name") or "")

        if not name.lower().endswith(".pdf"):

            continue

        filename = urllib.parse.unquote(name)

        normalized_base = normalize_base(filename)

        pages_url = f"https://{owner.lower()}.github.io/{repo}/{docs_dir}/{urllib.parse.quote(filename)}"

        out.append(

            PdfItem(

                name=name,

                filename=filename,

                normalized_base=normalized_base,

                date_key=extract_date_key(filename),

                tokens=tokenize_name(normalized_base),

                url=pages_url,

            )

        )

    return out

def normalize_text(text: str) -> str:

    value = urllib.parse.unquote(text or "")

    value = value.replace("\u00a0", " ")

    value = value.strip()

    value = value.normalize("NFC") if hasattr(value, "normalize") else value

    value = SPACE_RE.sub(" ", value)

    return value

def normalize_base(filename: str) -> str:

    value = normalize_text(filename)

    value = EXT_RE.sub("", value)

    value = DATE_RE.sub(" ", value)

    value = NON_WORD_RE.sub(" ", value)

    value = SPACE_RE.sub(" ", value).strip().lower()

    return value

def extract_date_key(text: str) -> str | None:

    value = normalize_text(text)

    match = DATE_RE.search(value)

    if not match:

        return None

    day = int(match.group(1))

    month = int(match.group(2))

    year = int(match.group(3))

    if year < 100:

        year += 2000

    if not (1 <= day <= 31 and 1 <= month <= 12 and 2000 <= year <= 2100):

        return None

    return f"{year:04d}-{month:02d}-{day:02d}"

def tokenize_name(normalized_base: str) -> list[str]:

    raw = [part for part in normalized_base.split(" ") if part]

    out: list[str] = []

    for token in raw:

        if token in STOPWORDS:

            continue

        if len(token) == 1 and not token.isdigit():

            continue

        out.append(token)

    return out

def token_similarity(a_tokens: list[str], b_tokens: list[str]) -> float:

    if not a_tokens or not b_tokens:

        return 0.0

    a_counter = Counter(a_tokens)

    b_counter = Counter(b_tokens)

    inter = sum((a_counter & b_counter).values())

    union = sum((a_counter | b_counter).values())

    return inter / union if union else 0.0

def ordered_similarity(a_text: str, b_text: str) -> float:

    if not a_text or not b_text:

        return 0.0

    return SequenceMatcher(None, a_text, b_text).ratio()

def score_match(audio: AudioItem, pdf: PdfItem) -> float:

    token_score = token_similarity(audio.tokens, pdf.tokens)

    ordered_score = ordered_similarity(audio.normalized_base, pdf.normalized_base)

    exact_bonus = 0.0

    if audio.normalized_base == pdf.normalized_base:

      exact_bonus = 0.35

    subset_bonus = 0.0

    a_set = set(audio.tokens)

    p_set = set(pdf.tokens)

    if a_set and p_set and (a_set <= p_set or p_set <= a_set):

        subset_bonus = 0.10

    return (token_score * 0.70) + (ordered_score * 0.30) + exact_bonus + subset_bonus

def choose_best_pdf(audio: AudioItem, pdfs: list[PdfItem]) -> tuple[PdfItem | None, float]:

    if not pdfs:

        return None, 0.0

    same_date = [pdf for pdf in pdfs if pdf.date_key and pdf.date_key == audio.date_key]

    candidates = same_date if same_date else pdfs

    if len(candidates) == 1:

        return candidates[0], 1.0

    scored = [(pdf, score_match(audio, pdf)) for pdf in candidates]

    scored.sort(key=lambda item: item[1], reverse=True)

    best_pdf, best_score = scored[0]

    second_score = scored[1][1] if len(scored) > 1 else -1.0

    if best_score < 0.20:

        return None, best_score

    if second_score >= 0 and (best_score - second_score) < 0.05 and best_score < 0.55:

        return None, best_score

    return best_pdf, best_score

def build_index(audios: list[AudioItem], pdfs: list[PdfItem]) -> dict[str, Any]:

    files = sorted({pdf.filename for pdf in pdfs})

    matches: dict[str, dict[str, Any]] = {}

    unmatched: list[dict[str, Any]] = []

    for audio in audios:

        pdf, score = choose_best_pdf(audio, pdfs)

        if pdf is None:

            unmatched.append(

                {

                    "audio_id": audio.id,

                    "audio_name": audio.filename,

                    "audio_date": audio.date_key,

                    "score": round(score, 4),

                }

            )

            continue

        matches[audio.id] = {

            "pdf_name": pdf.filename,

            "pdf_url": pdf.url,

            "audio_date": audio.date_key,

            "pdf_date": pdf.date_key,

            "score": round(score, 4),

        }

    return {

        "generated_at": int(time.time()),

        "source": "github-actions-cron",

        "strategy": {

            "primary": "date",

            "secondary": "token-similarity",

            "tie_breaker": "sequence-similarity",

        },

        "files": files,

        "matches": matches,

        "unmatched": unmatched,

    }

def write_json(path: Path, payload: dict[str, Any]) -> None:

    path.parent.mkdir(parents=True, exist_ok=True)

    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def main() -> int:

    try:

        audios = get_archive_audio(IA_IDENTIFIER_1, "S1") + get_archive_audio(IA_IDENTIFIER_2, "S2")

        pdfs = get_repo_pdfs(GITHUB_OWNER, GITHUB_REPO, GITHUB_BRANCH, DOCS_DIR)

        payload = build_index(audios, pdfs)

        write_json(OUTPUT_PATH, payload)

        print(f"Audio count: {len(audios)}")

        print(f"PDF count: {len(pdfs)}")

        print(f"Matched: {len(payload['matches'])}")

        print(f"Unmatched: {len(payload['unmatched'])}")

        print(f"Wrote: {OUTPUT_PATH}")

        return 0

    except urllib.error.HTTPError as exc:

        print(f"HTTP error: {exc.code} {exc.reason}", file=sys.stderr)

        return 1

    except Exception as exc:

        print(f"Build failed: {exc}", file=sys.stderr)

        return 1

if __name__ == "__main__":

    raise SystemExit(main())

# file: .github/workflows/pdf-index-cron.yml

name: Build PDF Index

on:

  workflow_dispatch:

  schedule:

    - cron: "17 * * * *"

  push:

    branches:

      - main

    paths:

      - "docs/**"

      - ".github/workflows/pdf-index-cron.yml"

      - "scripts/build_pdf_index.py"

permissions:

  contents: write

concurrency:

  group: build-pdf-index

  cancel-in-progress: false

jobs:

  build-pdf-index:

    runs-on: ubuntu-latest

    steps:

      - name: Checkout

        uses: actions/checkout@v4

      - name: Setup Python

        uses: actions/setup-python@v5

        with:

          python-version: "3.12"

      - name: Build docs/pdf-index.json

        run: python scripts/build_pdf_index.py

      - name: Commit changes

        shell: bash

        run: |

          if git diff --quiet -- docs/pdf-index.json; then

            echo "No changes"

            exit 0

          fi

          git config user.name "github-actions[bot]"

          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

          git add docs/pdf-index.json

          git commit -m "chore: refresh pdf-index.json"

          git push
