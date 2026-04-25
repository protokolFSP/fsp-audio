# scripts/build_pdf_index.py
from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

IA_IDENTIFIER_1 = "vorhofflimmern-bei-bekannter-khk-dr-oemer-dr-remzi-09.05.25"
IA_IDENTIFIER_2 = "FSPneu"
OUTPUT_JSON = "docs/pdf-index.json"
DOCS_DIR = "docs"
USER_AGENT = "fsp-audio-pdf-index/1.0"
REQUEST_TIMEOUT = 30

BLOCK_PREFIXES = (
    "dusseldorf",
    "düsseldorf",
    "stuttgart",
    "rheutlingen",
    "reutlingen",
    "sachsen",
    "hessen",
    "karlsruhe",
)

STOPWORDS = {
    "dr",
    "doktor",
    "doctor",
    "mit",
    "ve",
    "ile",
    "und",
    "bei",
    "audio",
    "fsp",
    "a",
    "the",
    "der",
    "die",
    "das",
    "fr",
    "frau",
    "herr",
}


@dataclass(frozen=True)
class AudioItem:
    id: str
    src: str
    title: str
    name: str


@dataclass(frozen=True)
class PdfItem:
    path: str
    name: str
    stem: str
    norm: str
    date_token: str


def log(msg: str) -> None:
    print(msg, flush=True)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def json_get(url: str) -> dict:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw)


def fold_tr(text: str) -> str:
    value = (text or "").strip().lower()
    value = value.replace("ı", "i")
    value = unicodedata.normalize("NFD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"\s+", " ", value).strip()
    return value


def normalize_base_name(text: str) -> str:
    value = text or ""
    value = re.sub(r"\.(mp3|m4a|wav|aac|ogg|flac|pdf|txt|srt)$", "", value, flags=re.I)
    value = value.replace("_", " ").replace("-", " ")
    value = value.replace("’", "").replace("'", "").replace('"', "")
    value = re.sub(r"[(){}\[\],;:!?]", " ", value)
    value = fold_tr(value)
    value = unicodedata.normalize("NFKC", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def extract_date_token(text: str) -> str:
    match = re.search(r"(\d{1,2})[.\-_](\d{1,2})[.\-_](\d{2}|\d{4})", text or "")
    if not match:
        return ""
    dd = f"{int(match.group(1)):02d}"
    mm = f"{int(match.group(2)):02d}"
    yy = match.group(3)
    if len(yy) == 2:
        yy = f"20{yy}"
    return f"{dd}.{mm}.{yy}"


def tokenize_for_similarity(text: str) -> list[str]:
    value = normalize_base_name(text)
    value = re.sub(r"\d{1,2}[.\-_]\d{1,2}[.\-_]\d{2,4}", " ", value)
    parts = re.split(r"\s+", value)
    return [p for p in parts if p and len(p) >= 2 and p not in STOPWORDS]


def token_similarity(a: str, b: str) -> float:
    sa = set(tokenize_for_similarity(a))
    sb = set(tokenize_for_similarity(b))
    if not sa or not sb:
        return 0.0
    common = len(sa & sb)
    return common / max(len(sa), len(sb))


def is_blocked_ia2_name(name: str) -> bool:
    base = Path(name).name
    base = re.sub(r"\.(m4a|mp3)$", "", base, flags=re.I).strip()
    base = re.sub(r"^A[\s_-]+", "", base, flags=re.I).strip()
    folded = fold_tr(base)
    for prefix in BLOCK_PREFIXES:
      candidate = fold_tr(prefix)
      if folded.startswith(candidate):
          next_char = folded[len(candidate):len(candidate) + 1]
          if not next_char or re.match(r"[\s\-_0-9.:]", next_char):
              return True
    return False


def fetch_archive_audio(identifier: str, src: str) -> list[AudioItem]:
    url = f"https://archive.org/metadata/{identifier}"
    data = json_get(url)
    files = data.get("files") or []
    out: list[AudioItem] = []

    for idx, file_obj in enumerate(files):
        name = str(file_obj.get("name") or "")
        if not name:
            continue
        if not re.search(r"\.(m4a|mp3)$", name, flags=re.I):
            continue
        if "source" in file_obj and str(file_obj.get("source") or "").lower() != "original":
            continue
        if src == "S2" and is_blocked_ia2_name(name):
            continue

        title = str(file_obj.get("title") or "").strip() or Path(name).name
        local_id = name or f"{title}#{idx}"
        out.append(
            AudioItem(
                id=f"{src}|{local_id}",
                src=src,
                title=title,
                name=name,
            )
        )
    return out


def scan_pdfs(root: Path) -> list[PdfItem]:
    docs_root = root / DOCS_DIR
    pdf_paths = sorted(docs_root.rglob("*.pdf"))
    out: list[PdfItem] = []

    for path in pdf_paths:
        rel = path.relative_to(root).as_posix()
        name = path.name
        out.append(
            PdfItem(
                path=rel,
                name=name,
                stem=path.stem,
                norm=normalize_base_name(name),
                date_token=extract_date_token(name),
            )
        )
    return out


def expected_pdf_name(audio_name: str) -> str:
    return re.sub(r"\.(mp3|m4a|wav|aac|ogg|flac)$", ".pdf", Path(audio_name).name, flags=re.I)


def match_pdf(audio: AudioItem, pdfs: list[PdfItem]) -> tuple[PdfItem | None, str, float]:
    target_pdf_name = expected_pdf_name(audio.name)
    target_norm = normalize_base_name(target_pdf_name)
    audio_name = Path(audio.name).name
    audio_date = extract_date_token(audio_name)

    for pdf in pdfs:
        if pdf.name == target_pdf_name:
            return pdf, "exact_name", 1.0

    for pdf in pdfs:
        if pdf.norm == target_norm:
            return pdf, "exact_normalized", 0.99

    if audio_date:
        same_date = [pdf for pdf in pdfs if pdf.date_token == audio_date]
        if len(same_date) == 1:
            return same_date[0], "date_only", 0.80
        if len(same_date) > 1:
            ranked = sorted(
                ((pdf, token_similarity(audio_name, pdf.name)) for pdf in same_date),
                key=lambda x: x[1],
                reverse=True,
            )
            best_pdf, best_score = ranked[0]
            if best_score > 0:
                return best_pdf, "date_similarity", best_score

    ranked_all = sorted(
        ((pdf, token_similarity(audio_name, pdf.name)) for pdf in pdfs),
        key=lambda x: x[1],
        reverse=True,
    )
    if ranked_all and ranked_all[0][1] >= 0.35:
        return ranked_all[0][0], "global_similarity", ranked_all[0][1]

    return None, "", 0.0


def build_index(audio_items: list[AudioItem], pdf_items: list[PdfItem]) -> dict:
    matches = []
    unmatched_audio = []
    matched_pdf_paths = set()

    for audio in audio_items:
        pdf, method, score = match_pdf(audio, pdf_items)
        audio_name = Path(audio.name).name
        if pdf is None:
            unmatched_audio.append(
                {
                    "audio_id": audio.id,
                    "audio_src": audio.src,
                    "audio_name": audio_name,
                    "audio_title": audio.title,
                    "audio_date": extract_date_token(audio_name),
                }
            )
            continue

        matched_pdf_paths.add(pdf.path)
        matches.append(
            {
                "audio_id": audio.id,
                "audio_src": audio.src,
                "audio_name": audio_name,
                "audio_title": audio.title,
                "audio_path": audio.name,
                "audio_date": extract_date_token(audio_name),
                "pdf_name": pdf.name,
                "pdf_path": pdf.path,
                "match_method": method,
                "match_score": round(score, 4),
            }
        )

    unmatched_pdfs = [
        {"pdf_name": pdf.name, "pdf_path": pdf.path, "pdf_date": pdf.date_token}
        for pdf in pdf_items
        if pdf.path not in matched_pdf_paths
    ]

    return {
        "version": 1,
        "generated_by": "scripts/build_pdf_index.py",
        "matches_count": len(matches),
        "audio_count": len(audio_items),
        "pdf_count": len(pdf_items),
        "matches": matches,
        "unmatched_audio": unmatched_audio,
        "unmatched_pdfs": unmatched_pdfs,
        "pdf_files": [{"pdf_name": pdf.name, "pdf_path": pdf.path} for pdf in pdf_items],
    }


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    root = repo_root()
    out_path = root / OUTPUT_JSON

    try:
        pdf_items = scan_pdfs(root)
        log(f"PDF bulundu: {len(pdf_items)}")

        s1 = fetch_archive_audio(IA_IDENTIFIER_1, "S1")
        s2 = fetch_archive_audio(IA_IDENTIFIER_2, "S2")
        audio_items = s1 + s2
        log(f"Audio bulundu: {len(audio_items)}")

        payload = build_index(audio_items, pdf_items)
        write_json(out_path, payload)

        log(f"Yazıldı: {out_path.relative_to(root).as_posix()}")
        log(f"Eşleşen: {payload['matches_count']}")
        log(f"Eşleşmeyen audio: {len(payload['unmatched_audio'])}")
        log(f"Eşleşmeyen pdf: {len(payload['unmatched_pdfs'])}")
        return 0

    except HTTPError as exc:
        log(f"HTTP error: {exc.code} {exc.reason}")
        return 1
    except URLError as exc:
        log(f"URL error: {exc.reason}")
        return 1
    except Exception as exc:
        log(f"Hata: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

