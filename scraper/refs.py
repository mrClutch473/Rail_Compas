from pathlib import Path

import yaml

from .config import ROOT
from .schemas import CityRef

SEED_FILE = ROOT / "seed" / "reference.yaml"


def city_refs(source: str, seed_file: Path = SEED_FILE) -> dict[str, CityRef]:
    """City references for one seller from the seed file; lets the CLI run without a database."""
    data = yaml.safe_load(seed_file.read_text(encoding="utf-8"))
    refs = {}
    for city in data.get("cities", []):
        ids = (city.get("sources") or {}).get(source)
        if ids:
            refs[city["slug"]] = CityRef(
                slug=city["slug"],
                name=city["name"],
                timezone=city["timezone"],
                external_id=str(ids["external_id"]),
                url_slug=ids.get("url_slug", ""),
            )
    return refs
