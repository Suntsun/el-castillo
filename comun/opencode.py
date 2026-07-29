"""
Wrapper subprocess para el CLI `opencode`.

Diseno (decidido en Fase 0 del Arquitecto del Castillo):
    - Canal: subprocess + binario `opencode`. NO HTTP. NO MCP.
    - Modelo: el default configurado en OpenCode (sin pago).
    - Sesion: la crea el codigo cliente (REPL del Arquitecto), este modulo
      solo recibe y reutiliza el session_id.
    - Nunca lanza excepciones: devuelve `None` ante cualquier fallo,
      siguiendo el estilo de `comun/llm.py`.

Formato de salida observado con `opencode run --format json "<mensaje>"`:

    Cada linea de stdout es un objeto JSON ("NDJSON"). Tipos relevantes:

      {"type":"step_start","timestamp":...,"sessionID":"ses_XXX",
       "part":{"id":"prt_...","messageID":"msg_...","sessionID":"ses_XXX",
               "type":"step-start"}}

      {"type":"text","timestamp":...,"sessionID":"ses_XXX",
       "part":{"id":"prt_...","messageID":"msg_...","sessionID":"ses_XXX",
               "type":"text","text":"contenido del modelo",
               "time":{"start":...,"end":...}}}

    El sessionID aparece en TODAS las lineas (atributo top-level). El texto
    final del assistant es la concatenacion de los `part.text` de todas las
    lineas con `type == "text"` y `part.type == "text"` (en orden de
    aparicion).

`opencode export <sessionID>` produce stdout con UNA linea de prefacio
(ej.: "Exporting session: ses_XXX") seguida de un objeto JSON completo. El
parser de exportar() salta hasta la primera linea que empiece por '{' y
parsea el resto como JSON.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Optional

from comun.logger import configurar_logger

# -- Constantes publicas -------------------------------------------------------

BINARIO = "opencode"
TIMEOUT_DEFAULT_S = 60

# Agente OpenCode restringido bajo el que corre la SESION PRINCIPAL del
# cerebro (P1.1). Deniega bash/edit/write, red y filesystem: el cerebro solo
# razona y emite JSON. Definido en
# ~/.config/opencode/agent/arquitecto-cerebro.md. Antes la sesion usaba el
# agente por defecto de OpenCode (con herramientas habilitadas); ahora queda
# restringida tecnicamente, no solo por prompt.
AGENTE_CEREBRO = "arquitecto-cerebro"

# -- Internos ------------------------------------------------------------------

_log = configurar_logger("opencode")

_PROMPT_SEMILLA = "ping"
_PROMPT_RESUMEN = (
    "Resume nuestra conversacion completa en 10 bullets como maximo. "
    "Cada bullet en una linea, empezando con '- '. Solo el resumen, "
    "sin preambulo ni cierre."
)


def _parsear_stream_ndjson(stdout: str) -> tuple[Optional[str], Optional[str]]:
    """Parsea el stream NDJSON de `opencode run --format json`.

    Args:
        stdout: Salida estandar completa del subproceso.

    Returns:
        (texto_final, session_id):
          - texto_final: concatenacion en orden de los `part.text` de cada
            linea con type=="text" y part.type=="text". None si no hubo
            ninguna linea de texto valida.
          - session_id: primer sessionID encontrado en el stream. None si
            no aparece en ninguna linea.
    """
    if not stdout:
        return (None, None)

    fragmentos: list[str] = []
    session_id: Optional[str] = None

    for linea in stdout.splitlines():
        linea = linea.strip()
        if not linea or not linea.startswith("{"):
            continue
        try:
            evento = json.loads(linea)
        except json.JSONDecodeError:
            # Linea ruido (banner, log diagnostico). Saltar.
            continue

        if session_id is None:
            sid = evento.get("sessionID")
            if isinstance(sid, str) and sid:
                session_id = sid

        if evento.get("type") == "text":
            parte = evento.get("part") or {}
            if isinstance(parte, dict) and parte.get("type") == "text":
                texto = parte.get("text")
                if isinstance(texto, str) and texto:
                    fragmentos.append(texto)

    texto_final = "".join(fragmentos) if fragmentos else None
    return (texto_final, session_id)


# -- API publica ---------------------------------------------------------------


def disponible() -> bool:
    """Comprueba si el binario `opencode` esta en PATH y responde a --version."""
    if shutil.which(BINARIO) is None:
        _log.error("binario '%s' no encontrado en PATH", BINARIO)
        return False
    try:
        res = subprocess.run(
            [BINARIO, "--version"],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            text=True,
            timeout=5,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        _log.error("opencode --version fallo: %s", exc)
        return False
    if res.returncode != 0:
        _log.warning("opencode --version returncode=%s", res.returncode)
        return False
    _log.info("opencode disponible (version %s)", res.stdout.strip())
    return True


def nueva_sesion(*, agente: str = AGENTE_CEREBRO) -> Optional[str]:
    """Crea una nueva sesion de OpenCode y devuelve su session_id.

    La sesion se materializa bajo el agente restringido `agente`
    (por defecto `AGENTE_CEREBRO`), de modo que el cerebro nunca dispone de
    bash/edit/write/red ni filesystem durante su turno.

    OpenCode no expone un comando para crear una sesion vacia; la sesion se
    materializa al ejecutar `opencode run`. Usamos un mensaje semilla muy
    corto ("ping") para forzar la creacion y extraer el sessionID del primer
    evento NDJSON.

    Coste: una invocacion al modelo por sesion (mensaje semilla). Con los
    modelos gratuitos configurados en OpenCode (Zen), el coste monetario es 0;
    el coste en tokens es el minimo de una respuesta corta.

    Returns:
        session_id (string `ses_...`) o None si falla.
    """
    if not disponible():
        return None
    try:
        res = subprocess.run(
            [BINARIO, "run", "--agent", agente, "--format", "json",
             _PROMPT_SEMILLA],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            text=True,
            timeout=TIMEOUT_DEFAULT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        _log.error("nueva_sesion: timeout tras %ss", TIMEOUT_DEFAULT_S)
        return None
    except OSError as exc:
        _log.error("nueva_sesion: OSError %s", exc)
        return None

    if res.returncode != 0:
        _log.warning(
            "nueva_sesion: returncode=%s stderr=%s",
            res.returncode,
            res.stderr[:200],
        )
        return None

    _, session_id = _parsear_stream_ndjson(res.stdout)
    if session_id is None:
        _log.warning("nueva_sesion: stream sin sessionID")
        return None
    _log.info("nueva_sesion: creada %s", session_id)
    return session_id


def enviar(
    session_id: str,
    mensaje: str,
    *,
    timeout_s: int = TIMEOUT_DEFAULT_S,
    agente: str = AGENTE_CEREBRO,
) -> Optional[str]:
    """Envia un mensaje a una sesion existente y devuelve el texto final.

    Cada turno se ejecuta bajo el agente restringido `agente` (por defecto
    `AGENTE_CEREBRO`): el cerebro razona y emite JSON, sin bash/edit/write
    ni filesystem.

    Args:
        session_id: ID de sesion devuelto por `nueva_sesion()`.
        mensaje: Texto del prompt a enviar.
        timeout_s: Timeout duro en segundos para la invocacion.
        agente: Agente OpenCode restringido bajo el que correr el turno.

    Returns:
        Texto concatenado de los eventos `text` del stream NDJSON, o None
        si hay timeout, error de proceso o stream sin texto.
    """
    if not session_id or not mensaje:
        _log.warning("enviar: session_id o mensaje vacio")
        return None
    try:
        res = subprocess.run(
            [BINARIO, "run", "-s", session_id, "--agent", agente,
             "--format", "json", mensaje],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        _log.error("enviar: timeout tras %ss (sesion=%s)", timeout_s, session_id)
        return None
    except OSError as exc:
        _log.error("enviar: OSError %s", exc)
        return None

    if res.returncode != 0:
        _log.warning(
            "enviar: returncode=%s stderr=%s",
            res.returncode,
            res.stderr[:200],
        )
        return None

    texto, _ = _parsear_stream_ndjson(res.stdout)
    if texto is None:
        _log.warning("enviar: stream sin texto (sesion=%s)", session_id)
        return None
    _log.info("enviar: %d chars recibidos (sesion=%s)", len(texto), session_id)
    return texto


def resumir(session_id: str, *, timeout_s: int = TIMEOUT_DEFAULT_S) -> Optional[str]:
    """Pide a OpenCode un resumen breve (10 bullets) de la conversacion.

    Internamente es solo un `enviar()` con un prompt fijo.
    """
    return enviar(session_id, _PROMPT_RESUMEN, timeout_s=timeout_s)


def exportar(session_id: str) -> Optional[dict]:
    """Exporta una sesion a dict usando `opencode export <sessionID>`.

    El comando emite UNA linea de prefacio ("Exporting session: ses_XXX")
    seguida de un objeto JSON multilinea. Saltamos lineas hasta encontrar
    la primera que empiece por '{' y parseamos desde ahi.

    Returns:
        Dict con la estructura completa de la sesion, o None si falla.
    """
    if not session_id:
        _log.warning("exportar: session_id vacio")
        return None
    try:
        res = subprocess.run(
            [BINARIO, "export", session_id],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        _log.error("exportar: timeout (sesion=%s)", session_id)
        return None
    except OSError as exc:
        _log.error("exportar: OSError %s", exc)
        return None

    if res.returncode != 0:
        _log.warning(
            "exportar: returncode=%s stderr=%s",
            res.returncode,
            res.stderr[:200],
        )
        return None

    # Saltar prefacio: buscar primera linea que empiece por '{'.
    lineas = res.stdout.splitlines()
    inicio = None
    for i, linea in enumerate(lineas):
        if linea.lstrip().startswith("{"):
            inicio = i
            break
    if inicio is None:
        _log.warning("exportar: no se encontro JSON en stdout")
        return None

    bloque = "\n".join(lineas[inicio:])
    try:
        return json.loads(bloque)
    except json.JSONDecodeError as exc:
        _log.warning("exportar: JSON invalido: %s", exc)
        return None


def borrar_sesion(session_id: str, *, timeout_s: int = 15) -> bool:
    """Borra una sesion de OpenCode (`opencode session delete <id>`).

    Anhadida en Fase 2 del Arquitecto: la sesion del cerebro vive solo
    durante el REPL; al salir el `SesionCerebro.__exit__` invoca esta
    funcion para no acumular sesiones zombi en disco.

    Args:
        session_id: ID de sesion (ses_...) devuelto por `nueva_sesion()`.
        timeout_s: Timeout duro de la operacion.

    Returns:
        True si el borrado retorno codigo 0; False ante cualquier fallo
        (binario ausente, timeout, returncode!=0, OSError). Nunca lanza.
    """
    if not session_id:
        _log.warning("borrar_sesion: session_id vacio")
        return False
    if shutil.which(BINARIO) is None:
        _log.warning("borrar_sesion: binario '%s' no encontrado", BINARIO)
        return False
    try:
        res = subprocess.run(
            [BINARIO, "session", "delete", session_id],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        _log.error("borrar_sesion: timeout (sesion=%s)", session_id)
        return False
    except OSError as exc:
        _log.error("borrar_sesion: OSError %s", exc)
        return False

    if res.returncode != 0:
        _log.warning(
            "borrar_sesion: returncode=%s stderr=%s",
            res.returncode,
            res.stderr[:200],
        )
        return False

    _log.info("borrar_sesion: sesion %s borrada", session_id)
    return True


def delegar(
    tarea: str,
    *,
    agente: str,
    directorio: str,
    timeout_s: int = 600,
) -> Optional[str]:
    """Delega una tarea libre a OpenCode en una invocacion de un solo turno.

    A diferencia de `enviar()` (que usa la sesion del cerebro con el contrato
    JSON), esto lanza OpenCode con un AGENTE concreto y restringido para que
    realice una tarea agentica (leer/editar ficheros) acotada a `directorio`.
    NUNCA usa `--dangerously-skip-permissions`: rige la politica de permisos
    del agente.

    Args:
        tarea: Instruccion en lenguaje natural para OpenCode.
        agente: Nombre del agente OpenCode a usar (p.ej. 'arquitecto-lectura'
            o 'arquitecto-escritura').
        directorio: Directorio de trabajo (`--dir`); acota el alcance.
        timeout_s: Timeout duro de la invocacion.

    Returns:
        Texto final de OpenCode, o None ante timeout/error/sin texto.
        Nunca lanza.
    """
    if not tarea or not agente or not directorio:
        _log.warning("delegar: argumentos incompletos")
        return None
    if shutil.which(BINARIO) is None:
        _log.warning("delegar: binario '%s' no encontrado", BINARIO)
        return None
    try:
        res = subprocess.run(
            [BINARIO, "run", "--agent", agente, "--dir", directorio,
             "--format", "json", tarea],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        _log.error("delegar: timeout tras %ss (agente=%s)", timeout_s, agente)
        return None
    except OSError as exc:
        _log.error("delegar: OSError %s", exc)
        return None

    if res.returncode != 0:
        _log.warning(
            "delegar: returncode=%s stderr=%s", res.returncode, res.stderr[:200],
        )
        return None

    texto, _ = _parsear_stream_ndjson(res.stdout)
    if texto is None:
        _log.warning("delegar: stream sin texto (agente=%s)", agente)
        return None
    _log.info("delegar: %d chars recibidos (agente=%s)", len(texto), agente)
    return texto
