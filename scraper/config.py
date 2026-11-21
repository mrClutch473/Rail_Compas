import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _proxies() -> list[str]:
    raw = os.getenv("SCRAPER_PROXIES", "")
    return [p.strip() for p in raw.replace("\n", ",").split(",") if p.strip()]


@dataclass(frozen=True)
class ScraperConfig:
    data_dir: Path = field(
        default_factory=lambda: Path(os.getenv("SCRAPER_DATA_DIR", ROOT / "data"))
    )
    proxies: list[str] = field(default_factory=_proxies)
    request_timeout: float = float(os.getenv("SCRAPER_TIMEOUT", "20"))
    task_timeout: float = float(os.getenv("SCRAPER_TASK_TIMEOUT", "45"))
    raw_retention_days: int = int(os.getenv("SCRAPER_RAW_RETENTION_DAYS", "14"))
    cooldown_minutes: int = int(os.getenv("SCRAPER_COOLDOWN_MINUTES", "60"))
    jitter: float = 0.5
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/142.0 Safari/537.36"
    )

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def state_file(self) -> Path:
        return self.data_dir / "state" / "sources.json"
