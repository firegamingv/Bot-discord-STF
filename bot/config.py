"""Chargement de la configuration depuis les variables d'environnement (.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    """Configuration invalide ou incomplète."""


def _opt_int(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} doit être un nombre entier (reçu : {raw!r}).") from exc


@dataclass(frozen=True, slots=True)
class Config:
    discord_token: str
    guild_id: int | None
    riot_api_key: str | None
    riot_region: str
    riot_platform: str
    timezone: ZoneInfo
    database_path: Path
    log_level: str
    log_dir: Path
    lolesports_api_key: str

    @classmethod
    def from_env(cls, env_file: str | os.PathLike | None = None) -> "Config":
        load_dotenv(env_file)
        token = os.getenv("DISCORD_TOKEN", "").strip()
        if not token:
            raise ConfigError(
                "DISCORD_TOKEN manquant. Copie .env.example en .env et renseigne le token du bot."
            )
        tz_name = os.getenv("TIMEZONE", "Europe/Paris").strip() or "Europe/Paris"
        try:
            tz = ZoneInfo(tz_name)
        except Exception as exc:  # noqa: BLE001 - ZoneInfo lève plusieurs types
            raise ConfigError(f"Fuseau horaire inconnu : {tz_name!r}") from exc

        return cls(
            discord_token=token,
            guild_id=_opt_int("GUILD_ID"),
            riot_api_key=os.getenv("RIOT_API_KEY", "").strip() or None,
            riot_region=os.getenv("RIOT_REGION", "europe").strip().lower() or "europe",
            riot_platform=os.getenv("RIOT_PLATFORM", "euw1").strip().lower() or "euw1",
            timezone=tz,
            database_path=Path(os.getenv("DATABASE_PATH", "data/bot.db")),
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
            log_dir=Path(os.getenv("LOG_DIR", "logs")),
            lolesports_api_key=os.getenv(
                "LOLESPORTS_API_KEY", "0TvQnueqKa5mxJntVWt0w4LpLfEkrV1Ta8rQBb9Z"
            ).strip(),
        )
