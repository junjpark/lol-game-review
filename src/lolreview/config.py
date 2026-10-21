"""Project configuration, loaded from environment / .env.

Personal Riot keys expire every 24 hours, so the key is never hard-coded and never
written to disk inside the repo. Copy ``.env.example`` to ``.env`` and paste the
current key there.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from .riot.routing import region_for

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
BASELINE_DIR = DATA_DIR / "baselines"
MODEL_DIR = PROJECT_ROOT / "models"


class MissingAPIKey(RuntimeError):
    """Raised when RIOT_API_KEY is absent — almost always an expired-key rotation."""


@dataclass(frozen=True)
class Config:
    api_key: str
    platform: str = "na1"
    queue: int = 420
    target_patches: tuple[str, ...] = field(default_factory=tuple)

    @property
    def region(self) -> str:
        """Regional routing cluster implied by the platform."""
        return region_for(self.platform)

    def redacted(self) -> str:
        tail = self.api_key[-4:] if len(self.api_key) >= 4 else "????"
        return f"Config(platform={self.platform}, region={self.region}, queue={self.queue}, key=...{tail})"


def load_config(dotenv_path: Path | None = None) -> Config:
    """Load configuration from the environment, falling back to a .env file."""
    load_dotenv(dotenv_path or PROJECT_ROOT / ".env")

    api_key = os.getenv("RIOT_API_KEY", "").strip()
    if not api_key:
        raise MissingAPIKey(
            "RIOT_API_KEY is not set. Copy .env.example to .env and paste a current "
            "key from https://developer.riotgames.com — development keys expire daily."
        )

    patches = tuple(
        p.strip() for p in os.getenv("TARGET_PATCHES", "").split(",") if p.strip()
    )

    return Config(
        api_key=api_key,
        platform=os.getenv("RIOT_PLATFORM", "na1").strip().lower(),
        queue=int(os.getenv("RIOT_QUEUE", "420")),
        target_patches=patches,
    )


def ensure_dirs() -> None:
    """Create the data directories if they don't exist yet."""
    for d in (RAW_DIR, PROCESSED_DIR, BASELINE_DIR, MODEL_DIR):
        d.mkdir(parents=True, exist_ok=True)
