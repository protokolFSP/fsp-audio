# file: scripts/build_pdf_index.py
from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = REPO_ROOT / "docs"
OUTPUT_FILE = DOCS_DIR / "pdf-index.json"

AUDIO_EXTENSIONS = {".mp3", ".m4a", ".wav", ".aac", ".ogg", ".flac"}
PDF_EXTENSION = ".pdf"

DATE_PATTERNS = [
    re.compile(r"(?<!\d)(\d{2})[.\-_](\d{2})[.\-_](\d{4})(?!\d)"),
    re.compile(r"(?<!\d)(\d{2})[.\-_](\d{2})[.\-_](\d{2})(?!\d)"),
]

STOPWORDS = {
    "dr",
    "doktor",
    "frau",
    "herr",
    "mit",
    "und",
    "ve",
    "ile",
    "bei",
    "von",
    "der",
    "die",
    "das",
    "ein",
    "eine",
    "audio",
    "fsp",
    "aufnahme",
    "kayit",
    "kaydı",
    "kaydi",
}


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.casefold()
    value = value.replace("ß", "ss")
    value = re.sub(r"\.(mp3|m4a|wav|aac|ogg|flac|pdf)$", "", value, flags=re.I)
    value = re.sub(r"[_\-]+", " ", value)
    value = re.sub(r"[^\w\s.]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def extract_date_parts(value: str) -> str | None:
    for pattern in DATE_PATTERNS:
        match = pattern.search(value)
        if not match:
            continue
        dd, mm, yy = match.groups()
        if len(yy) == 2:
            yy = f"20{yy}"
        return f"{dd}.{mm}.{yy}"
    return None


def tokenize_for_similarity(value: str) -> list[str]:
    normalized = normalize_text(value)
    normalized = re.sub(r"(?<!\d)(\d{2})[.\-_](\d{2})[.\-_](\d{2,4})(?!\d)", " ", normalized)
    tokens = re.findall(r"[a-zA-Z0-9]+", normalized)
    return [t for t in tokens if len(t) >= 2 and t not in STOPWORDS]


def similarity_score(audio_name: str, pdf_name: str) -> float:
    a_tokens = tokenize_for_similarity(audio_name)
    p_tokens = tokenize_for_similarity(pdf_name)

    if not a_tokens or not p_tokens:
      return 0.0

    a_count = Counter(a_tokens)
    p_count = Counter(p_tokens)

    common = sum((a_count & p_count).values())
    total = max(len(a_tokens), len(p_tokens))
    base = common / total if total else 0.0

    a_set = set(a_tokens)
    p_set = set(p_tokens)
    overlap = len(a_set & p_set)
    bonus = min(0.25, overlap * 0.05)

    return round(base + bonus, 6)


def collect_files(root: Path, extensions: set[str]) -> list[Path]:
    if not root.exists():
        return []
    return sorted(
        [
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in extensions
        ],
        key=lambda p: str(p.relative_to(root)).casefold(),
    )


def to_repo_rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def build_index() -> dict[str, Any]:
    pdf_files = collect_files(DOCS_DIR, {PDF_EXTENSION})
    audio_files = collect_files(REPO_ROOT, AUDIO_EXTENSIONS)

    pdf_by_date: dict[str, list[Path]] = {}
    pdf_meta: dict[str, dict[str, Any]] = {}

    for pdf in pdf_files:
        rel = to_repo_rel(pdf)
        name = pdf.name
        date_key = extract_date_parts(name)
        if date_key:
            pdf_by_date.setdefault(date_key, []).append(pdf)
        pdf_meta[rel] = {
            "path": rel,
            "name": name,
            "date": date_key,
        }

    matches: list[dict[str, Any]] = []
    unmatched_audio: list[dict[str, Any]] = []

    used_pdf_paths: set[str] = set()

    for audio in audio_files:
        rel_audio = to_repo_rel(audio)
        audio_name = audio.name
        date_key = extract_date_parts(audio_name)

        candidates = pdf_by_date.get(date_key, []) if date_key else []
        picked: Path | None = None
        picked_score = 0.0

        if len(candidates) == 1:
            picked = candidates[0]
            picked_score = 1.0
        elif len(candidates) > 1:
            ranked = sorted(
                (
                    (similarity_score(audio_name, candidate.name), candidate)
                    for candidate in candidates
                ),
                key=lambda item: (item[0], item[1].name.casefold()),
                reverse=True,
            )
            best_score, best_candidate = ranked[0]
            if best_score > 0:
                picked = best_candidate
                picked_score = best_score

        if picked is None:
            unmatched_audio.append(
                {
                    "audio_path": rel_audio,
                    "audio_name": audio_name,
                    "date": date_key,
                }
            )
            continue

        rel_pdf = to_repo_rel(picked)
        used_pdf_paths.add(rel_pdf)
        matches.append(
            {
                "audio_path": rel_audio,
                "audio_name": audio_name,
                "audio_date": date_key,
                "pdf_path": rel_pdf,
                "pdf_name": picked.name,
                "score": picked_score,
            }
        )

    unmatched_pdfs = [
        meta
        for rel, meta in sorted(pdf_meta.items(), key=lambda item: item[0].casefold())
        if rel not in used_pdf_paths
    ]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "docs_dir": "docs",
        "match_strategy": {
            "primary": "same date",
            "secondary": "token similarity when multiple pdf files share the same date",
        },
        "counts": {
            "pdf_files": len(pdf_files),
            "audio_files": len(audio_files),
            "matches": len(matches),
            "unmatched_audio": len(unmatched_audio),
            "unmatched_pdfs": len(unmatched_pdfs),
        },
        "matches": sorted(matches, key=lambda x: x["audio_path"].casefold()),
        "unmatched_audio": unmatched_audio,
        "unmatched_pdfs": unmatched_pdfs,
    }


def main() -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    data = build_index()
    OUTPUT_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Written: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()


# file: .github/workflows/build-pdf-index.yml
# this file is YAML, not Python
# save it separately under .github/workflows/build-pdf-index.yml

YAML_WORKFLOW = r"""
name: Build PDF Index

on:
  workflow_dispatch:
  schedule:
    - cron: "17 */6 * * *"

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
        with:
          fetch-depth: 0

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Show Python version
        run: python --version

      - name: Ensure script exists
        shell: bash
        run: test -f scripts/build_pdf_index.py

      - name: Build pdf index
        run: python scripts/build_pdf_index.py

      - name: Verify output
        shell: bash
        run: |
          test -f docs/pdf-index.json
          python - <<'PY'
          import json
          from pathlib import Path

          p = Path("docs/pdf-index.json")
          data = json.loads(p.read_text(encoding="utf-8"))

          if not isinstance(data, dict):
              raise SystemExit("pdf-index.json root must be an object")
          if "generated_at" not in data:
              raise SystemExit("missing generated_at")
          if "matches" not in data:
              raise SystemExit("missing matches")
          if not isinstance(data["matches"], list):
              raise SystemExit("matches must be a list")

          print(f"OK: matches={len(data['matches'])}")
          PY

      - name: Commit and push if changed
        shell: bash
        run: |
          if git diff --quiet -- docs/pdf-index.json; then
            echo "No changes"
            exit 0
          fi

          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

          git add docs/pdf-index.json
          git commit -m "chore: update pdf-index.json"
          git push
"""
