import logging


def configure_logging(level_name: str) -> None:
    """Configure process logging. Callers must not pass secrets into log records."""

    level = logging.getLevelNamesMapping().get(level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
