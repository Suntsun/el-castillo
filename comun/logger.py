import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_RUTA_LOGS = Path(__file__).resolve().parent.parent / "logs"
_FORMATO = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 3


def configurar_logger(nombre: str, nivel: int = logging.INFO) -> logging.Logger:
    _RUTA_LOGS.mkdir(exist_ok=True)

    logger = logging.getLogger(nombre)
    logger.setLevel(nivel)

    if logger.handlers:
        return logger

    formato = logging.Formatter(_FORMATO)

    fh = RotatingFileHandler(
        _RUTA_LOGS / f"{nombre}.log",
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    fh.setFormatter(formato)
    logger.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setFormatter(formato)
    logger.addHandler(ch)

    return logger
