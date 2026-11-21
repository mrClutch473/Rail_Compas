"""Find where values live inside a probe dump: responses, websocket frames, state, HTML.

uv run python -m scraper.recon.search recon/tutu/20261121-120000 "001Ж" 2340
uv run python -m scraper.recon.search recon/tutu/latest price --keys
"""

import argparse
import json
import re
import sys
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# socket.io and similar protocols prefix JSON with a numeric packet type, e.g. `42["offers", {...}]`
FRAME_PREFIX = re.compile(r"^\d+(?=[\[{])")


def load_json(text: str):
    try:
        return json.loads(FRAME_PREFIX.sub("", text, count=1))
    except ValueError:
        return None


def walk(node, path: str = "$") -> Iterator[tuple[str, str | None, object]]:
    """Yield (path, key, value) for every leaf and every dict key."""
    if isinstance(node, dict):
        for key, value in node.items():
            child = (
                f"{path}.{key}" if re.fullmatch(r"[A-Za-z_]\w*", str(key)) else f"{path}[{key!r}]"
            )
            yield child, str(key), value
            yield from walk(value, child)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from walk(value, f"{path}[{i}]")


def matches(value, needle: str, ignore_case: bool) -> bool:
    if isinstance(value, (dict, list)) or value is None:
        return False
    text = str(value)
    if ignore_case:
        return needle.lower() in text.lower()
    return needle in text


def preview(value, limit: int = 120) -> str:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    text = text.replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "…"


def search_json(data, needles, keys: bool, ignore_case: bool) -> Iterator[str]:
    for path, key, value in walk(data):
        target = key if keys else value
        for needle in needles:
            if matches(target, needle, ignore_case):
                yield f"{path} = {preview(value)}"
                break


def search_text(text: str, needles, ignore_case: bool, context: int = 80) -> Iterator[str]:
    haystack = text.lower() if ignore_case else text
    for needle in needles:
        n = needle.lower() if ignore_case else needle
        start = 0
        hits = 0
        while (pos := haystack.find(n, start)) != -1 and hits < 20:
            snippet = text[max(0, pos - context) : pos + len(needle) + context].replace("\n", " ")
            yield f"@{pos}: …{snippet}…"
            start = pos + len(n)
            hits += 1


def iter_sources(dump: Path) -> Iterator[tuple[str, object, str]]:
    """Yield (label, parsed_json_or_None, raw_text)."""
    for path in sorted((dump / "responses").glob("*")):
        text = path.read_text(encoding="utf-8", errors="replace")
        yield path.name, load_json(text) if path.suffix == ".json" else None, text

    for path in sorted((dump / "ws").glob("*.jsonl")):
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            frame = json.loads(line)
            if "text" not in frame:
                continue
            label = f"{path.name}#{line_no} ({frame['dir']}, t={frame['t']})"
            yield label, load_json(frame["text"]), frame["text"]

    for path in sorted((dump / "state").glob("*")):
        text = path.read_text(encoding="utf-8")
        yield f"state/{path.name}", load_json(text), text

    html = dump / "page.html"
    if html.exists():
        yield "page.html", None, html.read_text(encoding="utf-8")


def resolve_dump(arg: str) -> Path:
    path = Path(arg)
    if not path.is_absolute():
        path = ROOT / path
    if path.name == "latest":
        candidates = sorted(p for p in path.parent.iterdir() if p.is_dir())
        if not candidates:
            raise SystemExit(f"no dumps in {path.parent}")
        path = candidates[-1]
    if not path.is_dir():
        raise SystemExit(f"not a dump directory: {path}")
    return path


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Search a probe dump for values or keys.")
    parser.add_argument("dump", help="dump directory, or recon/<source>/latest")
    parser.add_argument("needles", nargs="+")
    parser.add_argument("--keys", action="store_true", help="match JSON keys instead of values")
    parser.add_argument("-i", "--ignore-case", action="store_true")
    parser.add_argument("--no-html", action="store_true", help="skip page.html")
    parser.add_argument("--limit", type=int, default=15, help="max hits shown per file")
    args = parser.parse_args()

    dump = resolve_dump(args.dump)
    print(f"Searching {dump.relative_to(ROOT)} for {args.needles}\n")

    total_files = 0
    for label, data, text in iter_sources(dump):
        if args.no_html and label == "page.html":
            continue
        if data is not None:
            hits = list(search_json(data, args.needles, args.keys, args.ignore_case))
        elif args.keys:
            hits = []
        else:
            hits = list(search_text(text, args.needles, args.ignore_case))
        if not hits:
            continue
        total_files += 1
        print(f"== {label}  ({len(hits)} hits)")
        for hit in hits[: args.limit]:
            print(f"   {hit}")
        if len(hits) > args.limit:
            print(f"   … {len(hits) - args.limit} more")

    if not total_files:
        print("Nothing found.")


if __name__ == "__main__":
    main()
