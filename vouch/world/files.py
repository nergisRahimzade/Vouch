"""Writes a Bundle to disk byte-for-byte reproducibly (fixed order, LF endings, UTF-8)."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import shutil
from pathlib import Path

from vouch.world.documents import FIELDS


def _csv_text(path: str, rows: list[dict]) -> str:
    fields = FIELDS.get(Path(path).name) or FIELDS.get(Path(path).name.rsplit("_", 1)[0] + ".csv")
    if fields is None:
        fields = list(rows[0]) if rows else []
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n", extrasaction="raise")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def render(path: str, content: object) -> str:
    if path.endswith(".csv"):
        return _csv_text(path, content)
    return json.dumps(content, indent=2, ensure_ascii=False) + "\n"


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_bundle(files: dict[str, object], root: Path, extra: dict[str, object] | None = None) -> dict:
    """Replace `root` with the bundle. Returns the manifest (path -> sha256)."""
    if root.exists():
        shutil.rmtree(root)
    manifest = {}
    for path in sorted(files):
        text = render(path, files[path])
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))
        manifest[path] = sha256(text)
    for path, content in (extra or {}).items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(render(path, content).encode("utf-8"))
    (root / "manifest.json").write_bytes(render("manifest.json", {"files": manifest}).encode("utf-8"))
    return manifest
