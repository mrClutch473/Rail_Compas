"""Print the shape of a big JSON document: keys, types, list sizes and sample values.

Dicts keyed by ids/hashes are collapsed into one `{<id>}` entry, lists show their first item.

    uv run python -m scraper.recon.shape recon/tutu/.../responses/0108_offers.json --depth 6
"""

import argparse
import json
import re
import sys
from pathlib import Path

ID_KEY = re.compile(r"^([0-9a-f]{16,}|\d+|[0-9a-f-]{36})$", re.IGNORECASE)


def sample(value) -> str:
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= 60 else text[:60] + "…"


def is_id_map(node: dict) -> bool:
    return len(node) > 1 and all(ID_KEY.match(str(k)) for k in node)


def show(node, name: str, indent: int, depth: int, out: list[str]) -> None:
    pad = "  " * indent
    if isinstance(node, dict):
        if is_id_map(node):
            first_key = next(iter(node))
            out.append(f"{pad}{name}: {{<id> x{len(node)}}}  e.g. {first_key}")
            if indent < depth:
                show(node[first_key], "<id>", indent + 1, depth, out)
            return
        out.append(f"{pad}{name}: {{{len(node)} keys}}")
        if indent < depth:
            for key, value in node.items():
                show(value, str(key), indent + 1, depth, out)
    elif isinstance(node, list):
        out.append(f"{pad}{name}: [{len(node)}]")
        if node and indent < depth:
            show(node[0], "[0]", indent + 1, depth, out)
    else:
        out.append(f"{pad}{name}: {type(node).__name__} = {sample(node)}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Show the structure of a JSON file.")
    parser.add_argument("path")
    parser.add_argument("--depth", type=int, default=5)
    parser.add_argument("--at", help="dotted path to start from, e.g. 0.dictionary.train")
    args = parser.parse_args()

    data = json.loads(Path(args.path).read_text(encoding="utf-8"))
    name = "$"
    if args.at:
        for part in args.at.split("."):
            data = data[int(part)] if isinstance(data, list) else data[part]
        name = args.at

    lines: list[str] = []
    show(data, name, 0, args.depth, lines)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
