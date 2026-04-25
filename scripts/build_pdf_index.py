# scripts/build_pdf_index.py

#!/usr/bin/env python3

from __future__ import annotations

import json

import os

import re

import sys

import unicodedata

from collections import defaultdict

from dataclasses import dataclass, asdict

from difflib import SequenceMatcher

from pathlib import Path

from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]

DOCS_DIR = REPO_ROOT / "docs"

OUTPUT_JSON = DOCS_DIR / "pdf-index.json"

AUDIO_EXTENSIONS = {".mp3", ".m4a", ".wav", ".aac", ".ogg", ".flac"}

PDF_EXTENSIONS = {".pdf"}

STOPWORDS = {

    "dr", "doktor", "frau", "herr", "mit", "und", "ve", "ile", "bei",

    "der", "die", "das", "ein", "eine", "bir", "von", "zu", "im", "in",

    "de", "den", "dem", "des", "fur", "für", "ohne", "not", "audio",

    "fsp", "guncel", "güncel", "atelier"

}

DATE_PATTERNS = [

    re.compile(r"(?<!\d)(\d{1,2})[.\-_/](\d{1,2})[.\-_/](\d{4})(?!\d)"),

    re.compile(r"(?<!\d)(\d{1,2})[.\-_/](\d{1,2})[.\-_/](\d{2})(?!\d)"),

]

TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)

@dataclass

class AudioCandidate:

    id: str

    src: str

    title: str

    name: str

    normalized_name: str

    normalized_title: str

    date_key: str | None

@dataclass

class PdfEntry:

    pdf_name: str

    pdf_path: str

    audio_id: str | None

    audio_name: str | None

    audio_src: str | None

    score: float

    date_key: str | None

    reason: str

def strip_ext(name: str) -> str:

    return os.path.splitext(name)[0]

def normalize_text(value: str) -> str:

    value = unicodedata.normalize("NFKD", value)

    value = "".join(ch for ch in value if not unicodedata.combining(ch))

    value = value.lower()

    value = value.replace("ı", "i").replace("ß", "ss")

    value = re.sub(r"[\u2010-\u2015]", "-", value)

    value = value.replace("_", " ")

    value = value.replace("-", " ")

    value = re.sub(r"\s+", " ", value).strip()

    return value

def tokenize(value: str) -> list[str]:

    norm = normalize_text(value)

    tokens = TOKEN_RE.findall(norm)

    return [t for t in tokens if t not in STOPWORDS and len(t) > 1]

def normalize_filename_for_match(name: str) -> str:

    base = strip_ext(Path(name).name)

    base = normalize_text(base)

    base = re.sub(r"^a[\s_-]+", "", base).strip()

    return base

def extract_date_key(value: str) -> str | None:

    text = Path(value).name

    for pattern in DATE_PATTERNS:

        match = pattern.search(text)

        if not match:

            continue

        dd = int(match.group(1))

        mm = int(match.group(2))

        yy = int(match.group(3))

        if yy < 100:

            yy += 2000

        if 1 <= dd <= 31 and 1 <= mm <= 12:

            return f"{yy:04d}-{mm:02d}-{dd:02d}"

    return None

def similarity(a: str, b: str) -> float:

    if not a or not b:

        return 0.0

    return SequenceMatcher(None, a, b).ratio()

def token_jaccard(a: Iterable[str], b: Iterable[str]) -> float:

    sa = set(a)

    sb = set(b)

    if not sa or not sb:

        return 0.0

    inter = len(sa & sb)

    union = len(sa | sb)

    return inter / union if union else 0.0

def combined_score(pdf_name: str, pdf_tokens: list[str], candidate: AudioCandidate) -> float:

    s1 = similarity(pdf_name, candidate.normalized_name)

    s2 = similarity(pdf_name, candidate.normalized_title)

    s3 = token_jaccard(pdf_tokens, tokenize(candidate.normalized_name))

    s4 = token_jaccard(pdf_tokens, tokenize(candidate.normalized_title))

    return max(s1, s2) * 0.7 + max(s3, s4) * 0.3

def build_audio_candidates() -> list[AudioCandidate]:

    candidates: list[AudioCandidate] = []

    s1_identifier = "vorhofflimmern-bei-bekannter-khk-dr-oemer-dr-remzi-09.05.25"

    s2_identifier = "FSPneu"

    def add_candidate(src: str, name: str, title: str) -> None:

        local_id = name or title

        audio_id = f"{src}|{local_id}"

        candidates.append(

            AudioCandidate(

                id=audio_id,

                src=src,

                title=title,

                name=name,

                normalized_name=normalize_filename_for_match(name),

                normalized_title=normalize_text(title),

                date_key=extract_date_key(name or title),

            )

        )

    static_candidates = [

        (

            "S1",

            s1_identifier,

            "Vorhofflimmern bei bekannter KHK Dr Ömer Dr Remzi 09.05.25.m4a",

            "Vorhofflimmern bei bekannter KHK Dr Ömer Dr Remzi 09.05.25",

        ),

    ]

    for src, _, file_name, title in static_candidates:

        add_candidate(src, file_name, title)

    for pdf_file in DOCS_DIR.glob("*.pdf"):

        base = strip_ext(pdf_file.name)

        date_key = extract_date_key(base)

        if not date_key:

            continue

        possible_audio_name = base + ".m4a"

        add_candidate("S2", possible_audio_name, base)

    return dedupe_audio_candidates(candidates)

def dedupe_audio_candidates(candidates: list[AudioCandidate]) -> list[AudioCandidate]:

    best: dict[str, AudioCandidate] = {}

    for item in candidates:

        key = f"{item.src}|{item.normalized_name}|{item.date_key or ''}"

        if key not in best:

            best[key] = item

            continue

        old = best[key]

        if len(item.title) > len(old.title):

            best[key] = item

    return list(best.values())

def scan_pdfs() -> list[Path]:

    if not DOCS_DIR.exists():

        raise FileNotFoundError(f"docs klasörü bulunamadı: {DOCS_DIR}")

    return sorted(

        [p for p in DOCS_DIR.iterdir() if p.is_file() and p.suffix.lower() in PDF_EXTENSIONS],

        key=lambda p: p.name.lower(),

    )

def match_pdf_to_audio(pdf_path: Path, candidates: list[AudioCandidate]) -> PdfEntry:

    pdf_name = pdf_path.name

    pdf_base = strip_ext(pdf_name)

    pdf_norm = normalize_filename_for_match(pdf_name)

    pdf_tokens = tokenize(pdf_base)

    pdf_date = extract_date_key(pdf_name)

    dated_candidates = [c for c in candidates if pdf_date and c.date_key == pdf_date]

    pool = dated_candidates if dated_candidates else candidates

    if not pool:

        return PdfEntry(

            pdf_name=pdf_name,

            pdf_path=f"docs/{pdf_name}",

            audio_id=None,

            audio_name=None,

            audio_src=None,

            score=0.0,

            date_key=pdf_date,

            reason="no-candidate",

        )

    scored = []

    for cand in pool:

        score = combined_score(pdf_norm, pdf_tokens, cand)

        if pdf_date and cand.date_key == pdf_date:

            score += 0.20

        scored.append((score, cand))

    scored.sort(key=lambda x: x[0], reverse=True)

    best_score, best = scored[0]

    if best_score < 0.25:

        return PdfEntry(

            pdf_name=pdf_name,

            pdf_path=f"docs/{pdf_name}",

            audio_id=None,

            audio_name=None,

            audio_src=None,

            score=round(best_score, 4),

            date_key=pdf_date,

            reason="low-score",

        )

    reason = "date+similarity" if pdf_date and best.date_key == pdf_date else "similarity-fallback"

    return PdfEntry(

        pdf_name=pdf_name,

        pdf_path=f"docs/{pdf_name}",

        audio_id=best.id,

        audio_name=best.name,

        audio_src=best.src,

        score=round(best_score, 4),

        date_key=pdf_date,

        reason=reason,

    )

def build_index() -> dict:

    pdf_files = scan_pdfs()

    audio_candidates = build_audio_candidates()

    matches = [match_pdf_to_audio(pdf, audio_candidates) for pdf in pdf_files]

    by_audio_id: dict[str, dict] = {}

    pdfs = []

    for match in matches:

        pdfs.append(asdict(match))

        if match.audio_id:

            by_audio_id[match.audio_id] = {

                "pdf_name": match.pdf_name,

                "pdf_path": match.pdf_path,

                "score": match.score,

                "reason": match.reason,

                "date_key": match.date_key,

            }

    unmatched = [asdict(m) for m in matches if not m.audio_id]

    return {

        "version": 1,

        "generated_at_utc": __import__("datetime").datetime.utcnow().replace(microsecond=0).isoformat() + "Z",

        "docs_dir": "docs",

        "pdf_count": len(pdf_files),

        "matched_count": len(matches) - len(unmatched),

        "unmatched_count": len(unmatched),

        "by_audio_id": by_audio_id,

        "pdfs": pdfs,

        "unmatched": unmatched,

    }

def main() -> int:

    try:

        index = build_index()

        DOCS_DIR.mkdir(parents=True, exist_ok=True)

        OUTPUT_JSON.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

        print(f"OK: {OUTPUT_JSON}")

        print(f"pdf_count={index['pdf_count']}")

        print(f"matched_count={index['matched_count']}")

        print(f"unmatched_count={index['unmatched_count']}")

        return 0

    except Exception as exc:

        print(f"HATA: {exc}", file=sys.stderr)

        return 1

if __name__ == "__main__":

    raise SystemExit(main())
