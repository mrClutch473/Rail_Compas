import gzip
import json
import shutil
import time
from datetime import datetime
from pathlib import Path

from .schemas import RawCapture, ScrapeRequest


def save_raw(raw_dir: Path, request: ScrapeRequest, capture: RawCapture, when: datetime) -> str:
    """Store what the source returned; the path (relative to raw_dir) goes into the task."""
    relative = (
        Path(request.source)
        / f"{when:%Y-%m-%d}"
        / f"{request.route_slug}_{request.travel_date:%Y%m%d}_{when:%H%M%S%f}.json.gz"
    )
    path = raw_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "request": request.model_dump(mode="json"),
        "captured_at": when.isoformat(),
        "capture": capture.model_dump(mode="json"),
    }
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(document, fh, ensure_ascii=False)
    return relative.as_posix()


def load_raw(path: Path) -> tuple[ScrapeRequest, RawCapture]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        document = json.load(fh)
    return ScrapeRequest(**document["request"]), RawCapture(**document["capture"])


def cleanup_raw(raw_dir: Path, keep_days: int) -> int:
    """Delete day folders older than keep_days. Returns how many were removed."""
    if not raw_dir.exists():
        return 0
    border = time.time() - keep_days * 86400
    removed = 0
    for day_dir in raw_dir.glob("*/*"):
        if day_dir.is_dir() and day_dir.stat().st_mtime < border:
            shutil.rmtree(day_dir, ignore_errors=True)
            removed += 1
    return removed
