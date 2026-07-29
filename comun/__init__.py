from pathlib import Path

RUTA_BASE = Path(__file__).resolve().parent.parent
RUTA_IMAGES = RUTA_BASE / "images"
RUTA_LOGS = RUTA_BASE / "logs"

from comun.notificador import notificar, silenciar_notificaciones, activar_notificaciones
from comun.logger import configurar_logger
from comun.config import cargar_config
from comun.llm import consultar as consultar_llm, disponible as llm_disponible
from comun import heraldo
