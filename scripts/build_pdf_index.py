# file: scripts/build_pdf_index.py
#!/usr/bin/env python3

from __future__ import annotations

import json
import math
import os
import re
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = ROOT / "docs"
OUTPUT_PATH = DOCS_DIR / "pdf-index.json"

AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".aac", ".ogg", ".flac"}
PDF_EXTS = {".pdf"}

STOPWORDS = {
    "dr", "doktor", "doctor", "frau", "herr", "mit", "und", "ve", "ile",
    "bei", "von", "der", "die", "das", "den", "dem", "ein", "eine",
    "patient", "patientin", "olgu", "vaka", "case", "audio", "fsp",
    "neu", "ab", "atelier", "guncel", "güncel", "kayit", "kayıt"
}

DATE_PATTERNS = [
    re.compile(r"(?<!\d)(\d{1,2})[.\-_/](\d{1,2})[.\-_/](\d{4})(?!\d)"),
    re.compile(r"(?<!\d)(\d{1,2})[.\-_/](\d{1,2})[.\-_/](\d{2})(?!\d)"),
]

WORD_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)


@dataclass(frozen=True)
class FileEntry:
    rel_path: str
    filename: str
    stem: str
    ext: str
    tokens: tuple[str, ...]
    date_key: str | None


@dataclass
class MatchEntry:
    audio_rel: str
    audio_name: str
    pdf_rel: str | None
    pdf_name: str | None
    score: float
    strategy: str
    audio_date: str | None
    pdf_date: str | None


def strip_diacritics(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def normalize_text(text: str) -> str:
    text = strip_diacritics(text).lower()
    text = text.replace("ß", "ss")
    text = text.replace("ı", "i")
    text = text.replace("’", "'")
    text = re.sub(r"\b[a]\s+", "", text)
    text = re.sub(r"[_\-]+", " ", text)
    text = re.sub(r"[^\w.\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_date_key(text: str) -> str | None:
    raw = normalize_text(text)
    for pattern in DATE_PATTERNS:
        match = pattern.search(raw)
        if not match:
            continue
        dd = int(match.group(1))
        mm = int(match.group(2))
        yy = int(match.group(3))
        if yy < 100:
            yy += 2000
        try:
            dt = datetime(yy, mm, dd)
        except ValueError:
            continue
        return dt.strftime("%Y-%m-%d")
    return None


def tokenize(text: str) -> tuple[str, ...]:
    raw = normalize_text(text)
    raw = re.sub(r"\b\d{1,2}[.\-_/]\d{1,2}[.\-_/]\d{2,4}\b", " ", raw)
    tokens = []
    for token in WORD_RE.findall(raw):
        if token in STOPWORDS:
            continue
        if len(token) <= 1:
            continue
        tokens.append(token)
    return tuple(tokens)


def rel_posix(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def walk_files(base: Path, allowed_exts: set[str]) -> list[Path]:
    if not base.exists():
        return []
    out: list[Path] = []
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() in allowed_exts:
            out.append(path)
    return sorted(out)


def make_entry(path: Path) -> FileEntry:
    rel = rel_posix(path)
    filename = path.name
    stem = path.stem
    return FileEntry(
        rel_path=rel,
        filename=filename,
        stem=stem,
        ext=path.suffix.lower(),
        tokens=tokenize(stem),
        date_key=extract_date_key(stem),
    )


def find_audio_dirs() -> list[Path]:
    candidates = [
        ROOT / "audio",
        ROOT / "audios",
        ROOT / "files",
        ROOT / "media",
        ROOT / "downloads",
        ROOT,
    ]
    seen: set[Path] = set()
    out: list[Path] = []
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.exists():
            out.append(candidate)
    return out


def load_audio_entries() -> list[FileEntry]:
    paths: list[Path] = []
    for base in find_audio_dirs():
        paths.extend(walk_files(base, AUDIO_EXTS))

    filtered: list[Path] = []
    for path in paths:
        rel = rel_posix(path)
        if rel.startswith("docs/"):
            continue
        if rel.startswith(".git/"):
            continue
        filtered.append(path)

    uniq = sorted({p.resolve(): p for p in filtered}.values(), key=lambda p: rel_posix(p))
    return [make_entry(p) for p in uniq]


def load_pdf_entries() -> list[FileEntry]:
    pdf_paths = walk_files(DOCS_DIR, PDF_EXTS)
    return [make_entry(p) for p in pdf_paths]


def token_counter(tokens: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for tok in tokens:
        counts[tok] = counts.get(tok, 0) + 1
    return counts


def cosine_score(a_tokens: tuple[str, ...], b_tokens: tuple[str, ...]) -> float:
    if not a_tokens or not b_tokens:
        return 0.0
    a = token_counter(a_tokens)
    b = token_counter(b_tokens)

    dot = 0.0
    for tok, av in a.items():
        dot += av * b.get(tok, 0)

    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    if norm_a <= 0 or norm_b <= 0:
        return 0.0
    return dot / (norm_a * norm_b)


def overlap_score(a_tokens: tuple[str, ...], b_tokens: tuple[str, ...]) -> float:
    if not a_tokens or not b_tokens:
        return 0.0
    a = set(a_tokens)
    b = set(b_tokens)
    inter = len(a & b)
    if inter == 0:
        return 0.0
    return inter / max(1, len(a | b))


def prefix_bonus(audio_name: str, pdf_name: str) -> float:
    a = normalize_text(Path(audio_name).stem)
    b = normalize_text(Path(pdf_name).stem)
    if not a or not b:
        return 0.0
    if a == b:
        return 0.35
    if a in b or b in a:
        return 0.18
    return 0.0


def date_bonus(audio_date: str | None, pdf_date: str | None) -> float:
    if not audio_date or not pdf_date:
        return 0.0
    return 0.30 if audio_date == pdf_date else -0.25


def score_match(audio: FileEntry, pdf: FileEntry) -> float:
    score = 0.0
    score += cosine_score(audio.tokens, pdf.tokens) * 0.55
    score += overlap_score(audio.tokens, pdf.tokens) * 0.35
    score += prefix_bonus(audio.filename, pdf.filename)
    score += date_bonus(audio.date_key, pdf.date_key)
    return round(score, 6)


def choose_best_pdf(audio: FileEntry, pdfs: list[FileEntry]) -> tuple[FileEntry | None, float, str]:
    if not pdfs:
        return None, 0.0, "none"

    dated_candidates = [p for p in pdfs if audio.date_key and p.date_key == audio.date_key]
    pool = dated_candidates if dated_candidates else pdfs

    scored = [(pdf, score_match(audio, pdf)) for pdf in pool]
    scored.sort(key=lambda item: (item[1], item[0].filename.lower()), reverse=True)

    best_pdf, best_score = scored[0]

    if len(pool) == 1 and dated_candidates:
      strategy = "date_only"
    elif dated_candidates:
      strategy = "date_then_similarity"
    else:
      strategy = "similarity_only"

    min_score = 0.12 if dated_candidates else 0.22
    if best_score < min_score:
        return None, best_score, f"{strategy}_below_threshold"

    return best_pdf, best_score, strategy


def build_matches(audio_entries: list[FileEntry], pdf_entries: list[FileEntry]) -> list[MatchEntry]:
    matches: list[MatchEntry] = []

    pdfs_by_date: dict[str, list[FileEntry]] = defaultdict(list)
    for pdf in pdf_entries:
        if pdf.date_key:
            pdfs_by_date[pdf.date_key].append(pdf)

    for audio in audio_entries:
        pool = pdfs_by_date.get(audio.date_key, []) if audio.date_key else []
        best_pdf, best_score, strategy = choose_best_pdf(audio, pool or pdf_entries)
        matches.append(
            MatchEntry(
                audio_rel=audio.rel_path,
                audio_name=audio.filename,
                pdf_rel=best_pdf.rel_path if best_pdf else None,
                pdf_name=best_pdf.filename if best_pdf else None,
                score=best_score,
                strategy=strategy,
                audio_date=audio.date_key,
                pdf_date=best_pdf.date_key if best_pdf else None,
            )
        )

    matches.sort(key=lambda x: x.audio_rel.lower())
    return matches


def build_lookup(matches: list[MatchEntry]) -> dict[str, dict[str, object]]:
    lookup: dict[str, dict[str, object]] = {}
    for m in matches:
        lookup[m.audio_name] = {
            "pdf": m.pdf_name,
            "pdf_rel": m.pdf_rel,
            "score": m.score,
            "strategy": m.strategy,
            "audio_date": m.audio_date,
            "pdf_date": m.pdf_date,
        }
    return lookup


def write_output(audio_entries: list[FileEntry], pdf_entries: list[FileEntry], matches: list[MatchEntry]) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "audio_count": len(audio_entries),
        "pdf_count": len(pdf_entries),
        "matched_count": sum(1 for m in matches if m.pdf_rel),
        "unmatched_count": sum(1 for m in matches if not m.pdf_rel),
        "lookup_by_audio_name": build_lookup(matches),
        "matches": [asdict(m) for m in matches],
    }

    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    audio_entries = load_audio_entries()
    pdf_entries = load_pdf_entries()

    if not pdf_entries:
        print("No PDFs found under docs/", file=sys.stderr)

    if not audio_entries:
        print("No audio files found in repository.", file=sys.stderr)

    matches = build_matches(audio_entries, pdf_entries)
    write_output(audio_entries, pdf_entries, matches)

    matched = sum(1 for m in matches if m.pdf_rel)
    unmatched = sum(1 for m in matches if not m.pdf_rel)

    print(f"Wrote: {OUTPUT_PATH.as_posix()}")
    print(f"Audio: {len(audio_entries)} | PDF: {len(pdf_entries)} | Matched: {matched} | Unmatched: {unmatched}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

