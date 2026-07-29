"""
Cerebro del Arquitecto: interfaz con OpenCode.

OpenCode se invoca como subproceso (`opencode run -s <session_id>`) y
mantiene la sesion viva durante la vida del REPL. Cada turno del usuario:

    1. El Arquitecto envia un turno al cerebro: petición del usuario
       + contexto opcional (snapshots on-demand).
    2. El cerebro responde con UN JSON conforme al contrato (ver
       prompts/contrato_json.md).
    3. El Arquitecto valida (validador.py) y devuelve RespuestaCerebro.

Esta unidad NO ejecuta automatizaciones. Solo habla con el LLM, parsea
la respuesta y la valida.

Decisiones de diseno fijadas (Fase 0 y Fase 2):
    - Canal: subprocess + opencode CLI. No HTTP. No MCP.
    - Modelo: el default de la instalacion de OpenCode (sin pago).
    - Prompt fundacional: se inyecta como PRIMER MENSAJE DE USUARIO en
      la sesion (no se usa agente custom). Si esto contamina demasiado
      el contrato JSON con el system prompt interno de OpenCode, se
      evaluara migrar a agente; por ahora se mantiene portable.
    - Sesion: vive solo durante el REPL; al salir __exit__ llama a
      opencode.borrar_sesion() para no acumular basura.
    - Reintentos: hasta `max_reintentos` con prompt corrector que cita
      el motivo exacto del rechazo. Tras agotar, devuelve una decision
      sintetica `rechazar_peligro`.
"""

from __future__ import annotations

import json
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Any

# Aseguramos importar comun.* y arquitecto.* sin depender del cwd.
_AQUI = Path(__file__).resolve()
_RAIZ_AUTOMATIZACIONES = _AQUI.parent.parent.parent
_PAQUETE_ARQUI = _AQUI.parent.parent
for _ruta in (_RAIZ_AUTOMATIZACIONES, _PAQUETE_ARQUI):
    if str(_ruta) not in sys.path:
        sys.path.insert(0, str(_ruta))

from comun import opencode  # noqa: E402
from comun.logger import configurar_logger  # noqa: E402
from arquitecto.registro import vista_para_cerebro  # noqa: E402
from arquitecto.validador import validar_decision  # noqa: E402

if TYPE_CHECKING:
    from arquitecto.registro import Manifiesto


# -- Constantes ----------------------------------------------------------------

BINARIO_OPENCODE = "opencode"

_RUTA_PROMPTS_DEFAULT = _PAQUETE_ARQUI / "prompts"
_FICHERO_FUNDACIONAL = "fundacional.md"
_FICHERO_CONTRATO = "contrato_json.md"

# Prompt minimo de respaldo si los ficheros no existen (defensa-en-profundidad).
_FUNDACIONAL_FALLBACK = (
    "Eres el cerebro del Arquitecto del Castillo. Respondes SIEMPRE con UN "
    "unico objeto JSON valido y nada mas. El primer caracter es '{' y el "
    "ultimo '}'. La decision DEBE ser una de: responder, aclarar, invocar, "
    "proponer_nueva, rechazar_peligro, pedir_confirmacion, componer."
)

# Texto LITERAL del prompt corrector cuando una respuesta sale fuera de
# contrato. Se le antepone el motivo concreto del rechazo en cada turno.
_PROMPT_CORRECTOR_PLANTILLA = (
    "Tu respuesta anterior NO cumple el contrato JSON.\n"
    "Motivo del rechazo: {motivo}\n"
    "Repite la decision SOLO como JSON, sin texto adicional, sin "
    "fences de markdown, sin preambulo ni cierre. El primer caracter debe "
    "ser '{{' y el ultimo '}}'. Recuerda que 'decision' debe estar en el "
    "enum cerrado y todos los campos obligatorios deben aparecer."
)

# Marcador para distinguir respuesta sintetica de error.
_RAZON_FUERA_DE_CONTRATO = "cerebro fuera de contrato tras {n} reintentos"


_log = configurar_logger("arquitecto.cerebro")


# -- Tipos publicos ------------------------------------------------------------


@dataclass(frozen=True)
class RespuestaCerebro:
    """Resultado tipado de un turno del cerebro."""

    decision: str              # Nombre de la decision (responder, invocar, ...).
    bruto: dict = field(default_factory=dict)
    normalizada: dict = field(default_factory=dict)
    valida: bool = False
    motivo_invalidez: str | None = None
    reintentos: int = 0
    requiere_confirmacion: bool = False
    turno_id: str = ""


# -- Helpers de parsing JSON ---------------------------------------------------

# Regex tolerante para extraer ```json ... ``` o ``` ... ``` con JSON dentro.
_REGEX_FENCE_JSON = re.compile(
    r"```(?:json)?\s*(\{.*?\})\s*```",
    re.DOTALL | re.IGNORECASE,
)


def _extraer_json(texto: str) -> tuple[dict | None, str]:
    """Busca el primer objeto JSON valido dentro de `texto`.

    Estrategia, en orden:
        1. Intentar `json.loads(texto.strip())`.
        2. Buscar bloque fenced ```json ... ```; intentar parsear su contenido.
        3. Recorrer caracteres y, ante cada '{', avanzar contando llaves
           balanceadas (respetando strings y escapes) hasta el '}' que
           cierra; intentar parsear el slice.

    Returns:
        (dict_parseado, motivo). Si dict_parseado is None, motivo explica
        por que no se encontro JSON valido.
    """
    if not isinstance(texto, str) or not texto.strip():
        return None, "respuesta vacia o no es texto"

    candidato = texto.strip()

    # 1) Intento directo.
    try:
        obj = json.loads(candidato)
        if isinstance(obj, dict):
            return obj, ""
    except json.JSONDecodeError:
        pass

    # 2) Fences markdown.
    for m in _REGEX_FENCE_JSON.finditer(candidato):
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict):
                return obj, ""
        except json.JSONDecodeError:
            continue

    # 3) Llaves balanceadas, respetando strings.
    n = len(candidato)
    i = 0
    while i < n:
        if candidato[i] == "{":
            j = _buscar_cierre_balanceado(candidato, i)
            if j is not None:
                slice_ = candidato[i:j + 1]
                try:
                    obj = json.loads(slice_)
                    if isinstance(obj, dict):
                        return obj, ""
                except json.JSONDecodeError:
                    pass
        i += 1

    return None, "no se encontro objeto JSON valido en la respuesta"


def _buscar_cierre_balanceado(texto: str, inicio: int) -> int | None:
    """Devuelve el indice del '}' que cierra el '{' en `inicio`.

    Respeta cadenas JSON (no cuenta llaves dentro de strings) y escapes.
    Devuelve None si no hay cierre.
    """
    if inicio >= len(texto) or texto[inicio] != "{":
        return None
    profundidad = 0
    en_string = False
    escape = False
    for k in range(inicio, len(texto)):
        ch = texto[k]
        if en_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                en_string = False
        else:
            if ch == '"':
                en_string = True
            elif ch == "{":
                profundidad += 1
            elif ch == "}":
                profundidad -= 1
                if profundidad == 0:
                    return k
    return None


# -- Helpers de prompts --------------------------------------------------------


def _leer_prompt(ruta_prompts: Path, nombre_fichero: str) -> str | None:
    """Lee un prompt desde disco. Devuelve None si no existe o no es UTF-8."""
    ruta = ruta_prompts / nombre_fichero
    if not ruta.is_file():
        return None
    try:
        return ruta.read_text(encoding="utf-8")
    except OSError:
        return None


def _ensamblar_prompt_fundacional(
    ruta_prompts: Path,
    registro: dict[str, "Manifiesto"],
) -> str:
    """Construye el primer mensaje de usuario que arranca el contrato.

    Estructura:
        <fundacional.md>
        \n---\n
        <contrato_json.md>
        \n---\n
        <vista_para_cerebro(registro)>
        \n---\n
        Si has entendido, responde EXACTAMENTE con:
        {"decision":"responder","texto":"Listo."}
    """
    fundacional = _leer_prompt(ruta_prompts, _FICHERO_FUNDACIONAL)
    if fundacional is None:
        _log.warning(
            "fundacional.md no encontrado en %s; usando fallback embebido",
            ruta_prompts,
        )
        fundacional = _FUNDACIONAL_FALLBACK

    contrato = _leer_prompt(ruta_prompts, _FICHERO_CONTRATO)
    if contrato is None:
        _log.warning(
            "contrato_json.md no encontrado en %s; el cerebro arrancara sin "
            "esquema formal (solo reglas del fundacional)",
            ruta_prompts,
        )
        contrato = ""

    vista = vista_para_cerebro(registro)
    _log.info(
        "catalogo para el cerebro: %d automatizaciones, %d bytes",
        len(registro), len(vista.encode("utf-8")),
    )

    partes = [
        fundacional.strip(),
        "---",
        contrato.strip() if contrato else "",
        "---",
        "Catalogo inicial de automatizaciones del Castillo:",
        vista,
        "---",
        "Si has entendido el contrato, responde EXACTAMENTE con:",
        '{"decision":"responder","texto":"Listo."}',
    ]
    return "\n\n".join(p for p in partes if p)


# -- SesionCerebro -------------------------------------------------------------


class SesionCerebro:
    """Maneja la sesion de OpenCode durante la vida del REPL.

    Uso:

        with SesionCerebro(registro) as cerebro:
            if not cerebro.disponible:
                ...  # fallback (Qwen, mensaje al usuario, etc.)
            else:
                respuesta = cerebro.turno("muestrame los errores de hoy")

    `__enter__` NO lanza si OpenCode no esta instalado o no responde: en
    su lugar marca `self.disponible = False`. Razon: simetria con
    `comun.opencode` (que tampoco lanza) y para que el REPL pueda
    degradar a fallback offline sin try/except externo. Si el caller
    prefiere fallar duro, basta con comprobar `cerebro.disponible` tras
    el `with` y lanzar el mismo.
    """

    def __init__(
        self,
        registro: dict[str, "Manifiesto"],
        *,
        modelo: str | None = None,
        max_reintentos: int = 2,
        ruta_prompts: Path | None = None,
        timeout_turno_s: int = 60,
    ) -> None:
        self._registro = registro
        self._modelo = modelo  # Reservado: hoy se usa el default de OpenCode.
        self._max_reintentos = max(0, int(max_reintentos))
        self._ruta_prompts = (ruta_prompts or _RUTA_PROMPTS_DEFAULT).resolve()
        self._timeout_turno = timeout_turno_s

        self._session_id: str | None = None
        self.disponible: bool = False

        # Metricas del ciclo de vida.
        self._turnos_atendidos: int = 0
        self._reintentos_totales: int = 0
        self._t_inicio: float = 0.0

    # --- ciclo de vida -------------------------------------------------------

    def __enter__(self) -> "SesionCerebro":
        self._t_inicio = time.time()

        if not opencode.disponible():
            _log.error(
                "SesionCerebro.__enter__: opencode no disponible; "
                "se devuelve sesion en modo disponible=False"
            )
            return self

        sid = opencode.nueva_sesion()
        if sid is None:
            _log.error(
                "SesionCerebro.__enter__: nueva_sesion devolvio None; "
                "marcando disponible=False"
            )
            return self

        self._session_id = sid

        prompt = _ensamblar_prompt_fundacional(self._ruta_prompts, self._registro)
        bytes_prompt = len(prompt.encode("utf-8"))
        _log.info(
            "SesionCerebro: sesion=%s creada; inyectando prompt fundacional "
            "(%d bytes)", sid, bytes_prompt,
        )

        respuesta_inicial = opencode.enviar(
            sid, prompt, timeout_s=self._timeout_turno,
        )
        if respuesta_inicial is None:
            _log.error(
                "SesionCerebro.__enter__: el prompt fundacional no recibio "
                "respuesta; sesion=%s queda marcada como NO disponible", sid,
            )
            # Intentamos borrar la sesion fallida para no dejar basura.
            opencode.borrar_sesion(sid)
            self._session_id = None
            return self

        _log.info(
            "SesionCerebro: prompt fundacional aceptado por sesion=%s "
            "(respuesta inicial %d chars, descartada)",
            sid, len(respuesta_inicial),
        )
        self.disponible = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        try:
            if self._session_id is not None:
                ok = opencode.borrar_sesion(self._session_id)
                if not ok:
                    _log.warning(
                        "SesionCerebro: borrar_sesion devolvio False para %s",
                        self._session_id,
                    )
        finally:
            duracion = time.time() - self._t_inicio if self._t_inicio else 0.0
            _log.info(
                "SesionCerebro: cierre; turnos=%d reintentos_totales=%d "
                "duracion=%.1fs",
                self._turnos_atendidos, self._reintentos_totales, duracion,
            )
            # No suprimimos excepciones.
            self._session_id = None
            self.disponible = False
        return None

    # --- API publica --------------------------------------------------------

    def turno(
        self,
        peticion_usuario: str,
        *,
        contexto_extra: str = "",
    ) -> RespuestaCerebro:
        """Envia un turno y devuelve la RespuestaCerebro validada.

        Args:
            peticion_usuario: Texto literal del usuario.
            contexto_extra: Snapshot opcional que el REPL quiera adjuntar.

        Returns:
            RespuestaCerebro. Si la validacion falla tras `max_reintentos`,
            devuelve una RespuestaCerebro con `decision='rechazar_peligro'`,
            `valida=False` y `motivo_invalidez` poblado.
        """
        turno_id = uuid.uuid4().hex[:12]

        if not self.disponible or self._session_id is None:
            _log.warning(
                "turno %s: sesion no disponible; devolviendo respuesta "
                "sintetica rechazar_peligro", turno_id,
            )
            return _respuesta_sintetica_fallo(
                turno_id, motivo="cerebro no disponible (sesion no inicializada)",
            )

        prompt = _construir_prompt_turno(peticion_usuario, contexto_extra)
        respuesta = self._enviar(prompt)
        reintentos = 0
        ultimo_motivo: str = ""

        while True:
            if respuesta is None:
                ultimo_motivo = "OpenCode no devolvio texto (timeout o error)"
                _log.warning(
                    "turno %s reintento %d: %s",
                    turno_id, reintentos, ultimo_motivo,
                )
            else:
                obj, motivo_parse = _extraer_json(respuesta)
                if obj is None:
                    ultimo_motivo = f"JSON no parseable: {motivo_parse}"
                    _log.warning(
                        "turno %s reintento %d: %s | bruto[0:160]=%r",
                        turno_id, reintentos, ultimo_motivo, respuesta[:160],
                    )
                else:
                    ok, motivo_val, norm = validar_decision(obj, self._registro)
                    if ok and norm is not None:
                        self._turnos_atendidos += 1
                        self._reintentos_totales += reintentos
                        _log.info(
                            "turno %s: decision=%s valida=True reintentos=%d",
                            turno_id, norm.get("decision", "?"), reintentos,
                        )
                        return RespuestaCerebro(
                            decision=str(norm.get("decision", "?")),
                            bruto=obj,
                            normalizada=norm,
                            valida=True,
                            motivo_invalidez=None,
                            reintentos=reintentos,
                            requiere_confirmacion=bool(
                                norm.get("requiere_confirmacion", False)
                            ),
                            turno_id=turno_id,
                        )
                    ultimo_motivo = motivo_val
                    _log.warning(
                        "turno %s reintento %d: contrato invalido: %s",
                        turno_id, reintentos, motivo_val,
                    )

            # Si hemos agotado reintentos, salimos.
            if reintentos >= self._max_reintentos:
                break

            # Reintento con prompt corrector.
            reintentos += 1
            corrector = _PROMPT_CORRECTOR_PLANTILLA.format(motivo=ultimo_motivo)
            respuesta = self._enviar(corrector)

        # Fuera del bucle: damos por perdida la decision.
        self._turnos_atendidos += 1
        self._reintentos_totales += reintentos
        motivo_final = _RAZON_FUERA_DE_CONTRATO.format(n=reintentos)
        _log.error(
            "turno %s: %s | ultimo_motivo=%s",
            turno_id, motivo_final, ultimo_motivo,
        )
        return _respuesta_sintetica_fallo(
            turno_id,
            motivo=f"{motivo_final}: {ultimo_motivo}",
            reintentos=reintentos,
        )

    # --- privados -----------------------------------------------------------

    def _enviar(self, mensaje: str) -> str | None:
        """Envia `mensaje` a la sesion actual. Devuelve texto o None."""
        if self._session_id is None:
            return None
        return opencode.enviar(
            self._session_id, mensaje, timeout_s=self._timeout_turno,
        )


# -- Funciones de modulo -------------------------------------------------------


def _construir_prompt_turno(
    peticion_usuario: str,
    contexto_extra: str = "",
) -> str:
    """Compone el mensaje de UN turno conforme al formato del fundacional.

    No se reincluye el catalogo en cada turno: ya esta en la memoria de la
    sesion desde __enter__. Si en el futuro hay snapshots o catalogo en
    caliente, se inyectan en `contexto_extra`.
    """
    partes = [
        "<entrada_usuario>",
        peticion_usuario.strip(),
        "</entrada_usuario>",
    ]
    if contexto_extra.strip():
        partes.extend([
            "<contexto_extra>",
            contexto_extra.strip(),
            "</contexto_extra>",
        ])
    partes.append(
        "Responde SOLO con el JSON de tu decision. Nada antes, nada despues."
    )
    return "\n".join(partes)


def _respuesta_sintetica_fallo(
    turno_id: str,
    *,
    motivo: str,
    reintentos: int = 0,
) -> RespuestaCerebro:
    """Construye una RespuestaCerebro de tipo `rechazar_peligro` sintetica."""
    bruto: dict[str, Any] = {
        "decision": "rechazar_peligro",
        "motivo": motivo[:280],
    }
    norm: dict[str, Any] = {
        "decision": "rechazar_peligro",
        "motivo": motivo[:280],
        "requiere_confirmacion": False,
    }
    return RespuestaCerebro(
        decision="rechazar_peligro",
        bruto=bruto,
        normalizada=norm,
        valida=False,
        motivo_invalidez=motivo,
        reintentos=reintentos,
        requiere_confirmacion=False,
        turno_id=turno_id,
    )


class CerebroNoDisponibleError(Exception):
    """Para callers que prefieran lanzar en vez de inspeccionar `disponible`."""
