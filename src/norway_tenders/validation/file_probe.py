from __future__ import annotations

import hashlib
import zipfile
from dataclasses import dataclass
from pathlib import Path

from norway_tenders.parsers.pdf_parser import extract_pdf_text

PDF_SIG = b"%PDF"
XLSX_SIG = b"PK"
OLD_XLS_SIG = b"\xd0\xcf\x11\xe0"


@dataclass
class FileProbeResult:
    local_file: str
    filename: str
    extension: str
    detected_type: str
    file_size: int
    sha256: str
    is_valid: bool
    has_embedded_text: bool
    validation_warning: str


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _validate_xlsx(path: Path) -> tuple[bool, str]:
    try:
        with zipfile.ZipFile(path) as zf:
            if "[Content_Types].xml" not in zf.namelist():
                return False, "Missing [Content_Types].xml"
        return True, ""
    except zipfile.BadZipFile:
        return False, "Invalid ZIP/OpenXML structure"
    except Exception as exc:
        return False, str(exc)


def probe_file(path: Path, seeds_root: Path) -> FileProbeResult:
    rel = str(path.relative_to(seeds_root))
    ext = path.suffix.lower().lstrip(".")
    data = path.read_bytes()
    size = len(data)
    warning = ""

    if size == 0:
        return FileProbeResult(
            rel, path.name, ext, "empty", size, sha256_file(path),
            False, False, "Empty file",
        )

    sig = data[:4]
    detected = "unknown"
    is_valid = False
    has_text = False

    if sig.startswith(PDF_SIG):
        detected = "pdf"
        is_valid = True
        text = extract_pdf_text(path)
        has_text = text is not None and len(text.strip()) >= 20
        if not has_text:
            warning = "PDF has no extractable embedded text"
    elif sig.startswith(XLSX_SIG) and ext in {"xlsx", "xlsm"}:
        detected = "xlsx"
        ok, err = _validate_xlsx(path)
        is_valid = ok
        if not ok:
            warning = err
    elif sig.startswith(XLSX_SIG) and ext == "docx":
        detected = "docx"
        ok, err = _validate_xlsx(path)
        is_valid = ok
        if not ok:
            warning = err
    elif sig.startswith(OLD_XLS_SIG):
        detected = "xls"
        is_valid = True
        warning = "Legacy XLS format; parser may not support"
    elif ext == "xlsx" and not sig.startswith(XLSX_SIG):
        detected = "mislabeled"
        warning = "Extension xlsx but signature is not ZIP/OpenXML"
    elif ext == "pdf" and not sig.startswith(PDF_SIG):
        detected = "mislabeled"
        warning = "Extension pdf but missing PDF signature"
    else:
        warning = f"Unsupported file type (signature {sig[:4]!r})"

    if ext != detected and detected not in {"unknown", "mislabeled", "empty"}:
        if ext == "xlsm" and detected == "xlsx":
            pass
        elif not warning:
            warning = f"Extension .{ext} vs detected {detected}"

    return FileProbeResult(
        local_file=rel,
        filename=path.name,
        extension=ext,
        detected_type=detected,
        file_size=size,
        sha256=sha256_file(path),
        is_valid=is_valid,
        has_embedded_text=has_text,
        validation_warning=warning,
    )
