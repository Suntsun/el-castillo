import subprocess
import tomllib
from pathlib import Path

_DIR_CONFIG = Path.home() / ".config" / "automatizaciones"
_FICHERO_SILENCIO = _DIR_CONFIG / "silencio"
_RUTA_BASE = Path(__file__).resolve().parent.parent
_RUTA_IMAGES = _RUTA_BASE / "images"
_RUTA_CONSEJEROS = Path(__file__).resolve().parent / "consejeros.toml"

_MAPA_URGENCIA = {
    "info": "low",
    "exito": "low",
    "aviso": "normal",
    "error": "critical",
    "critico": "critical",
}


def _cargar_consejeros() -> dict:
    with open(_RUTA_CONSEJEROS, "rb") as f:
        return tomllib.load(f)


def notificar(consejero: str, mensaje: str, severidad: str = "info", duracion: int = 5000):
    if _FICHERO_SILENCIO.exists():
        return

    try:
        datos = _cargar_consejeros()
    except FileNotFoundError:
        datos = {}

    if consejero in datos:
        info = datos[consejero]
        titulo = f"{info['nombre']} — {info['titulo']}"
        imagen = _RUTA_IMAGES / info.get("imagen", f"{consejero}.png")
        if not imagen.exists():
            imagen = None
    else:
        titulo = consejero.replace("_", " ").title()
        imagen = None

    urgencia = _MAPA_URGENCIA.get(severidad, "normal")

    cmd = [
        "notify-send",
        "-a", "automatizaciones",
        "-u", urgencia,
        "-t", str(duracion),
    ]
    if imagen:
        cmd.extend(["-i", str(imagen)])
    cmd.extend([titulo, mensaje])

    subprocess.run(cmd, capture_output=True)


def silenciar_notificaciones():
    _DIR_CONFIG.mkdir(parents=True, exist_ok=True)
    _FICHERO_SILENCIO.touch()


def activar_notificaciones():
    if _FICHERO_SILENCIO.exists():
        _FICHERO_SILENCIO.unlink()
