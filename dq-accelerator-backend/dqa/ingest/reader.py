"""Robust CSV ingestion.

The whole point of this module is that we receive files we have never seen,
exported by systems we do not control, with no schema and no cooperation.
Everything here is detection with a conservative fallback.
"""
from __future__ import annotations

import csv
import io
import re
from collections import Counter
from pathlib import Path
from typing import Iterator, Optional

import pandas as pd

from .. import config
from ..models import DialectInfo

SNIFF_BYTES = 64 * 1024
CANDIDATE_DELIMITERS = [",", ";", "\t", "|", ":"]
ENCODING_FALLBACKS = ["utf-8-sig", "utf-8", "cp1252", "latin-1"]

# Excel leaks these into exports. Normalised before type inference.
EXCEL_ERROR_VALUES = {"#REF!", "#N/A", "#VALUE!", "#DIV/0!", "#NAME?", "#NULL!", "#NUM!"}
_FORMULA_PREFIX = re.compile(r"^\s*=")


class IngestError(Exception):
    """Raised for files we cannot read at all. Message is shown to the user."""


# --------------------------------------------------------------------------
# Encoding
# --------------------------------------------------------------------------
def detect_encoding(path: Path) -> str:
    raw = path.open("rb").read(SNIFF_BYTES)
    if not raw:
        raise IngestError("The file is empty.")

    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"

    try:
        from charset_normalizer import from_bytes

        best = from_bytes(raw).best()
        if best is not None and best.encoding:
            enc = best.encoding.lower()
            # charset-normalizer reports ascii for plain files; utf-8 is a
            # superset and handles anything that appears later in the file.
            if enc == "ascii":
                return "utf-8"
            return enc
    except Exception:
        pass

    for enc in ENCODING_FALLBACKS:
        try:
            raw.decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    return "latin-1"


# --------------------------------------------------------------------------
# Delimiter
# --------------------------------------------------------------------------
def _score_delimiter(lines: list[str], delim: str) -> tuple[int, float]:
    """Return (modal field count, stability) for a candidate delimiter.

    Stability is the share of lines agreeing with the modal count. A real
    delimiter produces a consistent count above 1; a wrong one produces 1 or
    noise.
    """
    counts = []
    for line in lines:
        try:
            row = next(csv.reader([line], delimiter=delim))
            counts.append(len(row))
        except Exception:
            continue
    if not counts:
        return 0, 0.0
    modal, modal_n = Counter(counts).most_common(1)[0]
    return modal, modal_n / len(counts)


def detect_delimiter(sample: str) -> tuple[str, float]:
    lines = [ln for ln in sample.splitlines() if ln.strip()][:200]
    if not lines:
        raise IngestError("The file contains no readable rows.")

    best_delim, best_score, best_fields = ",", 0.0, 1
    for delim in CANDIDATE_DELIMITERS:
        fields, stability = _score_delimiter(lines, delim)
        if fields < 2:
            continue
        # Prefer high stability, break ties on more fields.
        score = stability + min(fields, 50) / 1000.0
        if score > best_score:
            best_delim, best_score, best_fields = delim, score, fields

    if best_fields < 2:
        # Single column file. Legal, just unusual.
        return ",", 0.3
    return best_delim, min(best_score, 1.0)


# --------------------------------------------------------------------------
# Header
# --------------------------------------------------------------------------
def _looks_like_header(cells: list[str], modal_count: int) -> bool:
    if len(cells) != modal_count:
        return False
    cleaned = [c.strip() for c in cells]
    if any(c == "" for c in cleaned):
        return False
    if len(set(c.lower() for c in cleaned)) != len(cleaned):
        return False  # duplicate names -> probably a data row
    numeric = sum(1 for c in cleaned if _is_numeric_like(c))
    return numeric <= len(cleaned) * 0.3


def _is_numeric_like(value: str) -> bool:
    v = value.strip().replace(",", "").replace(" ", "")
    if not v:
        return False
    try:
        float(v)
        return True
    except ValueError:
        return False


def detect_header(sample: str, delimiter: str, max_scan: int = 20) -> int:
    """Return the 0-based index of the header row within the raw file."""
    reader = csv.reader(io.StringIO(sample), delimiter=delimiter)
    rows: list[list[str]] = []
    for i, row in enumerate(reader):
        if i >= max_scan:
            break
        rows.append(row)
    if not rows:
        raise IngestError("The file contains no readable rows.")

    counts = [len(r) for r in rows if r]
    if not counts:
        raise IngestError("The file contains no readable rows.")
    modal = Counter(counts).most_common(1)[0][0]

    for idx, row in enumerate(rows):
        if _looks_like_header(row, modal):
            return idx
    return 0


# --------------------------------------------------------------------------
# Value normalisation
# --------------------------------------------------------------------------
def normalise_value(value: object) -> object:
    """Strip Excel artifacts before typing. Applied per chunk.

    IMPORTANT: this must NOT strip surrounding whitespace. Leading and
    trailing spaces are exactly what the consistency dimension exists to
    detect, and stripping here would silently destroy the evidence.
    """
    if not isinstance(value, str):
        return value
    if value.strip() in EXCEL_ERROR_VALUES:
        return None
    if _FORMULA_PREFIX.match(value):
        return value.strip()[1:].strip().strip('"')
    return value


def dedupe_columns(names: list[str]) -> tuple[list[str], list[str]]:
    """Suffix duplicate column names. Returns (names, notes)."""
    seen: Counter = Counter()
    out, notes = [], []
    for raw in names:
        name = (raw or "").strip() or "unnamed"
        if seen[name.lower()]:
            new = f"{name}_{seen[name.lower()]}"
            notes.append(f"Duplicate column '{name}' renamed to '{new}'")
            out.append(new)
        else:
            out.append(name)
        seen[name.lower()] += 1
    return out, notes


# --------------------------------------------------------------------------
# Source
# --------------------------------------------------------------------------
class CsvSource:
    """A detected, chunk-readable CSV file."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if not self.path.exists():
            raise IngestError("The uploaded file could not be found on disk.")
        if self.path.stat().st_size == 0:
            raise IngestError("The file is empty.")

        self.dialect = self._detect()
        self.columns, rename_notes = dedupe_columns(self._read_header())
        self.dialect.notes.extend(rename_notes)
        self.parse_warnings: int = 0
        self.parse_warning_examples: list[str] = []
        self._row_count: Optional[int] = None

    # -- detection ---------------------------------------------------------
    def _detect(self) -> DialectInfo:
        encoding = detect_encoding(self.path)
        with self.path.open("r", encoding=encoding, errors="replace", newline="") as fh:
            sample = fh.read(SNIFF_BYTES)
        delimiter, confidence = detect_delimiter(sample)
        header_row = detect_header(sample, delimiter)
        notes = []
        if header_row > 0:
            notes.append(f"Skipped {header_row} preamble row(s) before the header")
        if confidence < 0.7:
            notes.append("Delimiter detection was low confidence")
        return DialectInfo(
            encoding=encoding,
            delimiter=delimiter,
            header_row=header_row,
            preamble_rows=header_row,
            confidence=confidence,
            notes=notes,
        )

    def _read_header(self) -> list[str]:
        with self.path.open("r", encoding=self.dialect.encoding, errors="replace", newline="") as fh:
            reader = csv.reader(fh, delimiter=self.dialect.delimiter)
            for i, row in enumerate(reader):
                if i == self.dialect.header_row:
                    if not row:
                        raise IngestError("The header row is empty.")
                    return row
        raise IngestError("The file has no header row.")

    # -- counting ----------------------------------------------------------
    def row_count(self) -> int:
        """Cheap line count. Approximate when quoted newlines are present."""
        if self._row_count is not None:
            return self._row_count
        count = 0
        with self.path.open("rb") as fh:
            for _ in fh:
                count += 1
        self._row_count = max(count - self.dialect.header_row - 1, 0)
        return self._row_count

    # -- reading -----------------------------------------------------------
    def iter_chunks(self, chunk_size: int | None = None, sample_every: int = 1) -> Iterator[pd.DataFrame]:
        """Stream the file in chunks.

        `sample_every` > 1 takes a deterministic systematic sample, keeping
        every Nth row. Deterministic matters: two runs of the same file must
        produce the same scores.

        The yielded frame carries a `__row__` column holding the 1-based row
        number in the original file, so violations can point at real lines.
        """
        chunk_size = chunk_size or config.CHUNK_SIZE
        bad_lines: list[str] = []

        def on_bad_line(line: list[str]):
            if len(bad_lines) < 20:
                bad_lines.append(
                    f"Expected {len(self.columns)} columns, found {len(line)}"
                )
            self.parse_warnings += 1
            return None  # skip the row

        reader = pd.read_csv(
            self.path,
            encoding=self.dialect.encoding,
            encoding_errors="replace",
            sep=self.dialect.delimiter,
            skiprows=self.dialect.header_row,
            header=0,
            names=self.columns,
            dtype=str,
            keep_default_na=False,
            na_values=[""],
            chunksize=chunk_size,
            engine="python",
            on_bad_lines=on_bad_line,
            quotechar=self.dialect.quotechar,
        )

        offset = self.dialect.header_row + 2  # 1-based line of the first data row
        row_cursor = 0
        for chunk in reader:
            chunk = chunk.map(normalise_value)
            chunk["__row__"] = range(offset + row_cursor, offset + row_cursor + len(chunk))
            row_cursor += len(chunk)
            if sample_every > 1:
                chunk = chunk.iloc[::sample_every]
            if len(chunk):
                yield chunk.reset_index(drop=True)

        self.parse_warning_examples = bad_lines


def open_source(path: str | Path) -> CsvSource:
    return CsvSource(path)
