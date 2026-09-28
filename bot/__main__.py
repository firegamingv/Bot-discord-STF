"""Point d'entrée : ``python -m bot``."""

from __future__ import annotations

import asyncio
import logging
import sys

from bot.config import Config, ConfigError
from bot.core.bot import STFBot
from bot.logging_setup import setup_logging


async def _run() -> None:
    config = Config.from_env()
    setup_logging(config.log_level, config.log_dir)
    bot = STFBot(config)
    async with bot:
        await bot.start(config.discord_token)


def main() -> None:
    try:
        asyncio.run(_run())
    except ConfigError as exc:
        print(f"[config] {exc}", file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Arrêt demandé (Ctrl+C).")


if __name__ == "__main__":
    main()
