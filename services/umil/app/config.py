"""UMIL-only configuration with a secret-free process boundary."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


_TRADING_SECRET_NAMES = frozenset({
    "BINANCE_API_KEY",
    "BINANCE_API_SECRET",
    "BINANCE_SECRET_KEY",
    "EXCHANGE_API_KEY",
    "EXCHANGE_API_SECRET",
})


def _boolean(value: str, *, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def assert_secret_free_environment(environment: Mapping[str, str]) -> None:
    exposed = sorted(name for name in _TRADING_SECRET_NAMES if environment.get(name))
    if exposed:
        raise RuntimeError(
            "UMIL refuses to start with trading credentials in its process environment: "
            + ", ".join(exposed)
        )


@dataclass(frozen=True, slots=True)
class Settings:
    database_path: Path
    bind_host: str = "127.0.0.1"
    bind_port: int = 8100
    quote_assets: tuple[str, ...] = ("USDT",)
    snapshot_interval_seconds: float = 5.0
    metadata_refresh_seconds: float = 21_600.0
    stale_after_seconds: float = 15.0
    provider_timeout_seconds: float = 10.0
    intelligence_refresh_seconds: float = 60.0
    intelligence_batch_size: int = 2
    candle_limit: int = 120
    intelligence_intervals: tuple[str, ...] = ("5m", "15m", "1h", "4h", "1d")
    enable_scale_provider: bool = True
    scale_refresh_seconds: float = 900.0
    scale_pages: int = 2
    auto_start: bool = True
    enforce_secret_free_environment: bool = True
    evidence_verification_interval_seconds: float = 30.0
    evidence_full_verification_seconds: float = 3600.0
    evidence_verification_batch_size: int = 64

    def __post_init__(self) -> None:
        if not 1 <= self.bind_port <= 65_535:
            raise ValueError("UMIL_BIND_PORT must be a valid TCP port")
        if self.bind_port == 8000:
            raise ValueError("UMIL cannot bind to the ATS Core port")
        if not self.quote_assets:
            raise ValueError("at least one quote asset is required")
        if any(not value.isalnum() for value in self.quote_assets):
            raise ValueError("quote assets must be alphanumeric")
        if self.snapshot_interval_seconds < 1.0:
            raise ValueError("snapshot interval must be at least one second")
        if self.metadata_refresh_seconds < 60.0:
            raise ValueError("metadata refresh must be at least 60 seconds")
        if self.stale_after_seconds <= self.snapshot_interval_seconds:
            raise ValueError("stale threshold must exceed snapshot interval")
        if self.provider_timeout_seconds <= 0:
            raise ValueError("provider timeout must be positive")
        if self.intelligence_refresh_seconds < 15:
            raise ValueError("intelligence refresh must be at least 15 seconds")
        if not 1 <= self.intelligence_batch_size <= 20:
            raise ValueError("intelligence batch size must be in [1, 20]")
        if not 30 <= self.candle_limit <= 1000:
            raise ValueError("candle limit must be in [30, 1000]")
        supported = {"1m", "5m", "15m", "1h", "4h", "1d"}
        if not self.intelligence_intervals or not set(self.intelligence_intervals) <= supported:
            raise ValueError("unsupported intelligence interval")
        if self.scale_refresh_seconds < 300:
            raise ValueError("scale refresh must be at least 300 seconds")
        if not 1 <= self.scale_pages <= 4:
            raise ValueError("scale pages must be in [1, 4]")
        if self.evidence_verification_interval_seconds < 10:
            raise ValueError("evidence verification interval must be at least 10 seconds")
        if self.evidence_full_verification_seconds < self.evidence_verification_interval_seconds:
            raise ValueError("full evidence verification interval must not be shorter than incremental verification")
        if not 16 <= self.evidence_verification_batch_size <= 4096:
            raise ValueError("evidence verification batch size must be in [16, 4096]")
        resolved = self.database_path.expanduser().resolve()
        forbidden_names = {"trading_system.db", "analytics.db"}
        if resolved.name.lower() in forbidden_names:
            raise ValueError("UMIL cannot use an ATS Core database")

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if environment is None else environment
        enforce = _boolean(
            env.get("UMIL_ENFORCE_SECRET_FREE_ENV", "true"),
            name="UMIL_ENFORCE_SECRET_FREE_ENV",
        )
        if enforce:
            assert_secret_free_environment(env)
        default_db = Path(__file__).resolve().parents[1] / "var" / "umil.db"
        quote_assets = tuple(
            token.strip().upper()
            for token in env.get("UMIL_QUOTE_ASSETS", "USDT").split(",")
            if token.strip()
        )
        return cls(
            database_path=Path(env.get("UMIL_DATABASE_PATH", str(default_db))),
            bind_host=env.get("UMIL_BIND_HOST", "127.0.0.1"),
            bind_port=int(env.get("UMIL_BIND_PORT", "8100")),
            quote_assets=quote_assets,
            snapshot_interval_seconds=float(env.get("UMIL_SNAPSHOT_INTERVAL_SECONDS", "5")),
            metadata_refresh_seconds=float(env.get("UMIL_METADATA_REFRESH_SECONDS", "21600")),
            stale_after_seconds=float(env.get("UMIL_STALE_AFTER_SECONDS", "15")),
            provider_timeout_seconds=float(env.get("UMIL_PROVIDER_TIMEOUT_SECONDS", "10")),
            intelligence_refresh_seconds=float(env.get("UMIL_INTELLIGENCE_REFRESH_SECONDS", "60")),
            intelligence_batch_size=int(env.get("UMIL_INTELLIGENCE_BATCH_SIZE", "2")),
            candle_limit=int(env.get("UMIL_CANDLE_LIMIT", "120")),
            intelligence_intervals=tuple(
                token.strip() for token in env.get(
                    "UMIL_INTELLIGENCE_INTERVALS", "5m,15m,1h,4h,1d"
                ).split(",") if token.strip()
            ),
            enable_scale_provider=_boolean(
                env.get("UMIL_ENABLE_SCALE_PROVIDER", "true"),
                name="UMIL_ENABLE_SCALE_PROVIDER",
            ),
            scale_refresh_seconds=float(env.get("UMIL_SCALE_REFRESH_SECONDS", "900")),
            scale_pages=int(env.get("UMIL_SCALE_PAGES", "2")),
            auto_start=_boolean(env.get("UMIL_AUTO_START", "true"), name="UMIL_AUTO_START"),
            enforce_secret_free_environment=enforce,
            evidence_verification_interval_seconds=float(
                env.get("UMIL_EVIDENCE_VERIFICATION_INTERVAL_SECONDS", "30")
            ),
            evidence_full_verification_seconds=float(
                env.get("UMIL_EVIDENCE_FULL_VERIFICATION_SECONDS", "3600")
            ),
            evidence_verification_batch_size=int(
                env.get("UMIL_EVIDENCE_VERIFICATION_BATCH_SIZE", "64")
            ),
        )
