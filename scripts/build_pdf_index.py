# scripts/build_pdf_index.py
from __future__ import annotations

import json
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = ROOT / "docs"
OUTPUT_PATH = DOCS_DIR / "pdf-index.json"

AUDIO_EXTENSIONS = {".m4a", ".mp3", ".wav", ".aac", ".ogg", ".flac"}
ARCHIVE_IDENTIFIERS: list[tuple[str, str]] = [
    ("S1", "vorhofflimmern-bei-bekannter-khk-dr-oemer-dr-remzi-09.05.25"),
    ("S2", "FSPneu"),
]

BLOCKED_S2_PREFIXES = {
    "dusseldorf",
    "düsseldorf",
    "stuttgart",
    "rheutlingen",
    "reutlingen",
    "sachsen",
    "hessen",
    "karlsruhe",
}

STOP_WORDS = {
    "dr",
    "doktor",
    "mit",
    "und",
    "bei",
    "von",
    "der",
    "die",
    "das",
    "a",
    "frau",
    "herr",
    "abi",
}

DATE_RE = re.compile(r"(\d{1,2})[.\-_/](\d{1,2})[.\-_/](\d{2}|\d{4})")
TR_TZ = timezone(timedelta(hours=3))


@dataclass(frozen=True)
class AudioItem:
    source: str
    identifier: str
    audio_id: str
    audio_name: str
    audio_title: str
    audio_date: str
    normalized_base: str
    tokens: tuple[str, ...]


@dataclass(frozen=True)
class PdfItem:
    pdf_name: str
    pdf_path: str
    pdf_date: str
    normalized_base: str
    tokens: tuple[str, ...]


def normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = text.replace("’", "").replace("'", "").replace("`", "").replace('"', "")
    text = re.sub(r"[_./\\\-]+", " ", text)
    text = re.sub(r"&", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def remove_extension(name: str) -> str:
    return Path(name).stem.strip()


def extract_date_token(value: str) -> str:
    match = DATE_RE.search(value or "")
    if not match:
        return ""

    day = int(match.group(1))
    month = int(match.group(2))
    year_raw = match.group(3)

    if len(year_raw) == 2:
        year = 2000 + int(year_raw)
    else:
        year = int(year_raw)

    if not (1 <= day <= 31 and 1 <= month <= 12):
        return ""

    return f"{day:02d}.{month:02d}.{year:04d}"


def tokenize(value: str) -> tuple[str, ...]:
    out: list[str] = []
    for token in normalize_text(value).split():
        if len(token) <= 1:
            continue
        if token in STOP_WORDS:
            continue
        out.append(token)
    return tuple(out)


def jaccard_score(a_tokens: Iterable[str], b_tokens: Iterable[str]) -> int:
    a = set(a_tokens)
    b = set(b_tokens)
    if not a and not b:
        return 0
    union = len(a | b)
    if union == 0:
        return 0
    inter = len(a & b)
    return round((inter / union) * 100)


def is_blocked_s2_name(file_name: str) -> bool:
    base = remove_extension(Path(file_name).name)
    base = re.sub(r"^A[\s_-]+", "", base, flags=re.IGNORECASE).strip()
    folded = normalize_text(base)

    for prefix in BLOCKED_S2_PREFIXES:
        p = normalize_text(prefix)
        if folded.startswith(p):
            rest = folded[len(p):len(p) + 1]
            if not rest or re.match(r"[\s\-_0-9.:]", rest):
                return True
    return False


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw)


def load_archive_audio_items() -> list[AudioItem]:
    items: list[AudioItem] = []

    for source, identifier in ARCHIVE_IDENTIFIERS:
        meta_url = f"https://archive.org/metadata/{urllib.parse.quote(identifier, safe='')}"
        payload = fetch_json(meta_url)
        files = payload.get("files") or []

        for index, entry in enumerate(files):
            name = str(entry.get("name") or "").strip()
            if not name:
                continue

            suffix = Path(name.lower()).suffix
            if suffix not in AUDIO_EXTENSIONS:
                continue

            source_flag = str(entry.get("source") or "").lower().strip()
            if "source" in entry and source_flag and source_flag != "original":
                continue

            if source == "S2" and is_blocked_s2_name(name):
                continue

            title = str(entry.get("title") or "").strip() or Path(name).name
            local_id = name or f"{title}#{index}"
            audio_id = f"{source}|{local_id}"
            audio_name = Path(name).name
            audio_base = remove_extension(audio_name)

            items.append(
                AudioItem(
                    source=source,
                    identifier=identifier,
                    audio_id=audio_id,
                    audio_name=audio_name,
                    audio_title=title,
                    audio_date=extract_date_token(audio_base),
                    normalized_base=normalize_text(audio_base),
                    tokens=tokenize(audio_base),
                )
            )

    return items


def load_pdf_items() -> list[PdfItem]:
    if not DOCS_DIR.exists():
        return []

    items: list[PdfItem] = []

    for path in sorted(DOCS_DIR.rglob("*.pdf")):
        if path.name.lower() == "pdf-index.json":
            continue

        rel = path.relative_to(ROOT).as_posix()
        pdf_name = path.name
        pdf_base = remove_extension(pdf_name)

        items.append(
            PdfItem(
                pdf_name=pdf_name,
                pdf_path=f"./{rel}",
                pdf_date=extract_date_token(pdf_base),
                normalized_base=normalize_text(pdf_base),
                tokens=tokenize(pdf_base),
            )
        )

    return items


def score_match(audio: AudioItem, pdf: PdfItem) -> int:
    score = 0

    if audio.audio_date and pdf.pdf_date:
        if audio.audio_date != pdf.pdf_date:
            return -1
        score += 100

    if audio.normalized_base == pdf.normalized_base:
        score += 120

    if audio.normalized_base and pdf.normalized_base:
        if audio.normalized_base in pdf.normalized_base or pdf.normalized_base in audio.normalized_base:
            score += 30

    score += jaccard_score(audio.tokens, pdf.tokens)
    return score


def match_audio_to_pdf(audio_items: list[AudioItem], pdf_items: list[PdfItem]) -> list[dict]:
    rows: list[dict] = []

    for audio in audio_items:
        if not pdf_items:
            continue

        same_date = [pdf for pdf in pdf_items if audio.audio_date and pdf.pdf_date == audio.audio_date]
        pool = same_date if same_date else pdf_items

        best_pdf: PdfItem | None = None
        best_score = -1

        for pdf in pool:
            score = score_match(audio, pdf)
            if score > best_score:
                best_score = score
                best_pdf = pdf

        if not best_pdf:
            continue

        if same_date:
            if best_score < 120:
                continue
        else:
            if best_score < 160:
                continue

        rows.append(
            {
                "audioId": audio.audio_id,
                "audioName": audio.audio_name,
                "audioTitle": audio.audio_title,
                "audioDate": audio.audio_date,
                "pdfName": best_pdf.pdf_name,
                "pdfPath": best_pdf.pdf_path,
                "score": best_score,
            }
        )

    rows.sort(key=lambda row: (row["audioDate"], row["audioName"], row["pdfName"]))
    return rows


def write_output(items: list[dict]) -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    payload = {
        "generatedAt": datetime.now(TR_TZ).strftime("%d.%m.%Y %H:%M"),
        "count": len(items),
        "items": items,
    }

    OUTPUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> int:
    try:
        print(f"ROOT={ROOT}")
        print(f"DOCS_DIR={DOCS_DIR}")
        print(f"OUTPUT_PATH={OUTPUT_PATH}")

        audio_items = load_archive_audio_items()
        print(f"audio_items={len(audio_items)}")

        pdf_items = load_pdf_items()
        print(f"pdf_items={len(pdf_items)}")

        matches = match_audio_to_pdf(audio_items, pdf_items)
        print(f"matches={len(matches)}")

        write_output(matches)
        print(f"Built {OUTPUT_PATH} with {len(matches)} matches.")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
