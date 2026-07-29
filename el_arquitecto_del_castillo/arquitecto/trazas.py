"""
Trazas del Arquitecto del Castillo (Fase 3).

Persistencia append-only en formato JSONL (un objeto JSON por linea) de lo
que ocurre en cada turno del REPL: que pidio el usuario, que decidio el
cerebro, si se ejecuto algo y con que resultado.

Objetivos:
    - Auditoria: poder reconstruir despues que se ejecuto y por que.
    - Depuracion: ver decisiones invalidas y reintentos del cerebro.
    - Base para la persistencia entre sesiones de la Fase 5 (resumen).

JSONL y no un unico JSON array porque el append es O(1), no hay que releer
ni reescribir el fichero entero, y un corte a mitad de escritura solo
corrompe la ultima linea (el lector la salta) en vez de invalidar todo.

Nunca lanza desde la API publica: si no puede escribir, loguea y devuelve
False; si una linea no parsea al leer, la omite.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

_RAIZ_AUTOMATIZACIONES = Path(__file__).resolve().parent.parent.parent
if str(_RAIZ_AUTOMATIZACIONES) not in sys.path:
    sys.path.insert(0, str(_RAIZ_AUTOMATIZACIONES))

from comun.logger import configurar_logger  # noqa: E402

_log = configurar_logger("arquitecto.trazas")


# -- Constantes ----------------------------------------------------------------

# Fichero de trazas por defecto: state/ del propio Arquitecto.
_RUTA_TRAZAS_DEFAULT = (
    Path(__file__).resolve().parent.parent / "state" / "trazas.jsonl"
)

# Version del formato de traza, por si cambia el esquema mas adelante.
VERSION_TRAZA = "1.0.0"


# -- Modelo --------------------------------------------------------------------


@dataclass(frozen=True)
class ResumenEjecucion:
    """Vista compacta de un resultado de ejecucion para la traza.

    No guardamos stdout/stderr completos en la traza (pueden ser enormes);
    solo lo justo para auditar: que se lanzo y como acabo.
    """

    clave_automatizacion: str
    nombre_operacion: str
    ejecutado: bool
    codigo_salida: int | None = None
    timeout: bool = False
    bloqueado: bool = False
    motivo_no_ejecucion: str | None = None
    error: str | None = None


# -- Helpers privados ----------------------------------------------------------


def _ahora_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _resumir_resultado(resultado: Any) -> dict[str, Any] | None:
    """Extrae los campos auditables de un ResultadoEjecucion (pato-tipado).

    Acepta cualquier objeto con los atributos esperados para no acoplar
    `trazas` a `ejecutor` por import. Devuelve None si `resultado` es None.
    """
    if resultado is None:
        return None
    return {
        "clave_automatizacion": getattr(resultado, "clave_automatizacion", None),
        "nombre_operacion": getattr(resultado, "nombre_operacion", None),
        "ejecutado": getattr(resultado, "ejecutado", None),
        "codigo_salida": getattr(resultado, "codigo_salida", None),
        "timeout": getattr(resultado, "timeout", None),
        "bloqueado": getattr(resultado, "bloqueado", None),
        "motivo_no_ejecucion": getattr(resultado, "motivo_no_ejecucion", None),
        "error": getattr(resultado, "error", None),
        # Avisos (p. ej. la marca de delegacion EXCEPCIONAL o una alerta de
        # efectos fuera del sandbox). Util para auditar delegaciones.
        "avisos": list(getattr(resultado, "avisos", ()) or ()),
        # Metadatos del modo de ingenieria (delegar_ingenieria); None para
        # invocaciones normales del catalogo.
        "perfil_ingenieria": getattr(resultado, "perfil_ingenieria", None),
        "directorio_autorizado": getattr(
            resultado, "directorio_autorizado", None
        ),
    }


# -- API publica ---------------------------------------------------------------


def construir_traza(
    *,
    peticion_usuario: str,
    decision: str,
    valida: bool,
    turno_id: str = "",
    reintentos: int = 0,
    requiere_confirmacion: bool = False,
    motivo_invalidez: str | None = None,
    resultados: list[Any] | None = None,
) -> dict[str, Any]:
    """Construye el dict de una traza de turno.

    Args:
        peticion_usuario: Texto literal del usuario.
        decision: Nombre de la decision del cerebro (responder, invocar...).
        valida: Si la decision paso la validacion del contrato.
        turno_id: ID del turno (de RespuestaCerebro).
        reintentos: Reintentos que necesito el cerebro.
        requiere_confirmacion: Si la decision pidio confirmacion.
        motivo_invalidez: Motivo si `valida` es False.
        resultados: Lista de ResultadoEjecucion (1 para `invocar`, N para
            `componer`). Puede ser None si la decision no ejecuto nada.

    Returns:
        dict serializable a JSON.
    """
    ejecuciones = [
        r for r in (
            _resumir_resultado(x) for x in (resultados or [])
        ) if r is not None
    ]
    return {
        "version": VERSION_TRAZA,
        "ts": _ahora_iso(),
        "turno_id": turno_id,
        "peticion": peticion_usuario,
        "decision": decision,
        "valida": valida,
        "reintentos": reintentos,
        "requiere_confirmacion": requiere_confirmacion,
        "motivo_invalidez": motivo_invalidez,
        # Marca inequivoca de las decisiones que actuan FUERA del catalogo de
        # manifiestos (con confirmacion): `delegar_opencode`/`delegar_ingenieria`
        # (OpenCode actua) y `ejecutar_comandos` (el Arquitecto ejecuta comandos
        # de solo lectura propuestos por el cerebro). Permite auditarlas de un
        # vistazo.
        "fuera_de_manifiestos": decision in (
            "delegar_opencode", "delegar_ingenieria", "ejecutar_comandos",
        ),
        "ejecuciones": ejecuciones,
    }


def escribir_traza(traza: dict[str, Any], *, ruta: Path | None = None) -> bool:
    """Anade una traza al fichero JSONL. Crea el directorio si no existe.

    Returns:
        True si se escribio; False ante cualquier error (logueado).
    """
    destino = Path(ruta) if ruta is not None else _RUTA_TRAZAS_DEFAULT
    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        linea = json.dumps(traza, ensure_ascii=False, default=str)
        with open(destino, "a", encoding="utf-8") as fh:
            fh.write(linea + "\n")
        return True
    except (OSError, TypeError, ValueError) as exc:
        _log.error("escribir_traza: no se pudo escribir en %s: %s", destino, exc)
        return False


def registrar_turno(
    *,
    peticion_usuario: str,
    decision: str,
    valida: bool,
    turno_id: str = "",
    reintentos: int = 0,
    requiere_confirmacion: bool = False,
    motivo_invalidez: str | None = None,
    resultados: list[Any] | None = None,
    ruta: Path | None = None,
) -> bool:
    """Atajo: construye la traza del turno y la escribe en disco."""
    traza = construir_traza(
        peticion_usuario=peticion_usuario,
        decision=decision,
        valida=valida,
        turno_id=turno_id,
        reintentos=reintentos,
        requiere_confirmacion=requiere_confirmacion,
        motivo_invalidez=motivo_invalidez,
        resultados=resultados,
    )
    return escribir_traza(traza, ruta=ruta)


def leer_trazas(
    *, ruta: Path | None = None, limite: int | None = None,
) -> list[dict[str, Any]]:
    """Lee las trazas del fichero JSONL.

    Args:
        ruta: Fichero a leer; por defecto el de `state/trazas.jsonl`.
        limite: Si se indica, devuelve solo las ULTIMAS `limite` trazas.

    Returns:
        Lista de dicts en orden cronologico (mas antigua primero). Las
        lineas que no parsean como JSON se omiten silenciosamente. Si el
        fichero no existe, devuelve lista vacia.
    """
    origen = Path(ruta) if ruta is not None else _RUTA_TRAZAS_DEFAULT
    if not origen.is_file():
        return []
    trazas: list[dict[str, Any]] = []
    try:
        with open(origen, encoding="utf-8") as fh:
            for linea in fh:
                linea = linea.strip()
                if not linea:
                    continue
                try:
                    obj = json.loads(linea)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    trazas.append(obj)
    except OSError as exc:
        _log.error("leer_trazas: no se pudo leer %s: %s", origen, exc)
        return []

    if limite is not None and limite >= 0:
        return trazas[-limite:]
    return trazas
