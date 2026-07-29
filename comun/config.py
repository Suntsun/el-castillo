import tomllib
from pathlib import Path


def cargar_config(ruta_automatizacion: str | Path) -> dict:
    ruta = Path(ruta_automatizacion)
    config_path = ruta if ruta.is_file() else ruta / "config.toml"

    if not config_path.exists():
        return {}

    with open(config_path, "rb") as f:
        return tomllib.load(f)
