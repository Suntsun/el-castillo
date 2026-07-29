"""
Ejecutor del Arquitecto del Castillo (Fase 3).

Lanza las invocaciones que el cerebro propuso, el validador valido y la
capa de seguridad permitio. Es la UNICA unidad del Arquitecto que crea
subprocesos. Reglas de oro:

    1. SIEMPRE `shell=False`. Nunca se construye una linea de shell; se
       pasa una LISTA de tokens a `subprocess.run`.
    2. Antes de lanzar nada se consulta a `seguridad.evaluar_invocacion`.
       Si el veredicto no permite, NO se ejecuta.
    3. La confirmacion del usuario se delega a un `confirmador` inyectable
       (callable str -> bool). Por defecto, si una operacion requiere
       confirmacion y no hay confirmador, NO se ejecuta.
    4. Timeout duro por operacion: `manifiesto.seguridad.tiempo_max_segundos`.
    5. Nunca lanza desde la API publica: todo error se refleja en el
       resultado.

API publica:
    ejecutar_invocacion(invocacion_norm, manifiesto, ...) -> ResultadoEjecucion
    ejecutar_composicion(composicion_norm, registro, ...) -> ResultadoComposicion
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TYPE_CHECKING

# Raiz de automatizaciones, para logger compartido y cwd de los subprocesos.
_RAIZ_AUTOMATIZACIONES = Path(__file__).resolve().parent.parent.parent
if str(_RAIZ_AUTOMATIZACIONES) not in sys.path:
    sys.path.insert(0, str(_RAIZ_AUTOMATIZACIONES))

from comun.logger import configurar_logger  # noqa: E402
from comun import opencode  # noqa: E402
from arquitecto import seguridad  # noqa: E402
from arquitecto import ingenieria  # noqa: E402
from arquitecto import comandos  # noqa: E402
from arquitecto.validador import normalizar_a_lista_argumentos  # noqa: E402

if TYPE_CHECKING:
    from arquitecto.registro import Manifiesto

_log = configurar_logger("arquitecto.ejecutor")


# -- Constantes ----------------------------------------------------------------

# Cota de tamano de stdout/stderr que se guarda en el resultado. Evita que
# una salida enorme infle trazas o notificaciones. La ejecucion no se ve
# afectada; solo se trunca lo que se conserva.
_MAX_CAPTURA_CHARS = 20_000

# Tipo del callback de confirmacion: recibe el texto a mostrar y devuelve
# True si el usuario autoriza.
Confirmador = Callable[[str], bool]
VerificadorRed = Callable[[], bool]


# -- Resultados ----------------------------------------------------------------


@dataclass(frozen=True)
class ResultadoEjecucion:
    """Resultado de ejecutar (o no) UNA invocacion."""

    clave_automatizacion: str
    nombre_operacion: str
    comando: tuple[str, ...]
    ejecutado: bool
    codigo_salida: int | None = None
    stdout: str = ""
    stderr: str = ""
    duracion_s: float = 0.0
    timeout: bool = False
    truncado: bool = False
    bloqueado: bool = False
    motivo_no_ejecucion: str | None = None
    error: str | None = None
    avisos: tuple[str, ...] = ()
    # Metadatos del modo de ingenieria (decision delegar_ingenieria); None
    # para invocaciones normales del catalogo.
    perfil_ingenieria: str | None = None
    directorio_autorizado: str | None = None

    @property
    def exito(self) -> bool:
        """True solo si se ejecuto y termino con codigo 0."""
        return self.ejecutado and self.codigo_salida == 0


@dataclass(frozen=True)
class ResultadoComposicion:
    """Resultado de ejecutar una decision `componer` (cadena de pasos)."""

    razon: str
    resultados: tuple[ResultadoEjecucion, ...]
    abortada: bool = False
    paso_fallido: int | None = None

    @property
    def exito(self) -> bool:
        """True si la cadena no se aborto y todos los pasos tuvieron exito."""
        return not self.abortada and all(r.exito for r in self.resultados)


@dataclass(frozen=True)
class ResultadoComandos:
    """Resultado de ejecutar una decision `ejecutar_comandos` (lote PI-2)."""

    razon: str
    resultados: tuple[ResultadoEjecucion, ...]
    abortada: bool = False
    comando_fallido: int | None = None
    bloqueado: bool = False
    motivo_no_ejecucion: str | None = None

    @property
    def exito(self) -> bool:
        """True si el lote no se bloqueo/aborto y todo comando tuvo exito."""
        return (
            not self.bloqueado
            and not self.abortada
            and bool(self.resultados)
            and all(r.exito for r in self.resultados)
        )


# -- Helpers privados ----------------------------------------------------------


def _truncar(texto: str) -> tuple[str, bool]:
    if texto is None:
        return "", False
    if len(texto) <= _MAX_CAPTURA_CHARS:
        return texto, False
    return texto[:_MAX_CAPTURA_CHARS] + "\n[...salida truncada...]", True


def hay_conectividad(*, host: str = "1.1.1.1", puerto: int = 53,
                     timeout_s: float = 3.0) -> bool:
    """Comprobacion ligera de conectividad saliente (TCP a un DNS publico).

    Tolerante: cualquier excepcion se interpreta como sin conectividad.
    Inyectable en el ejecutor para los tests (no se llama si la operacion
    no declara `requiere_red`).
    """
    try:
        with socket.create_connection((host, puerto), timeout=timeout_s):
            return True
    except OSError:
        return False


def _no_ejecutado(
    clave: str,
    nombre_op: str,
    comando: list[str],
    *,
    motivo: str,
    bloqueado: bool = False,
    avisos: tuple[str, ...] = (),
) -> ResultadoEjecucion:
    return ResultadoEjecucion(
        clave_automatizacion=clave,
        nombre_operacion=nombre_op,
        comando=tuple(comando),
        ejecutado=False,
        bloqueado=bloqueado,
        motivo_no_ejecucion=motivo,
        avisos=avisos,
    )


# -- API publica ---------------------------------------------------------------


def ejecutar_invocacion(
    invocacion_norm: dict,
    manifiesto: "Manifiesto",
    *,
    confirmador: Confirmador | None = None,
    dry_run: bool = False,
    verificador_red: VerificadorRed | None = None,
) -> ResultadoEjecucion:
    """Ejecuta UNA invocacion ya validada.

    Args:
        invocacion_norm: Sub-dict normalizado por `validador.py`. Debe traer
            `clave_automatizacion`, `nombre_operacion`, `argumentos`, y los
            campos derivados (`peligrosidad_efectiva`, etc.).
        manifiesto: Manifiesto de `invocacion_norm['clave_automatizacion']`.
        confirmador: Callable que muestra un texto y devuelve True si el
            usuario autoriza. Si la operacion requiere confirmacion y este
            es None, la operacion NO se ejecuta.
        dry_run: Si True, evalua seguridad y construye el comando pero NO
            lanza el subprocess. Util para `--dry-run` (Fase 6).
        verificador_red: Callable sin args que devuelve True si hay
            conectividad. Solo se consulta si la operacion `requiere_red`.
            Por defecto usa `hay_conectividad`.

    Returns:
        ResultadoEjecucion. Nunca lanza.
    """
    clave = str(invocacion_norm.get("clave_automatizacion", "?"))
    nombre_op = str(invocacion_norm.get("nombre_operacion", "?"))

    # 1) Veredicto de seguridad.
    veredicto = seguridad.evaluar_invocacion(invocacion_norm, manifiesto)
    if not veredicto.permitido:
        _log.warning(
            "ejecutar_invocacion: '%s.%s' bloqueado por seguridad: %s",
            clave, nombre_op, veredicto.motivo_bloqueo,
        )
        return _no_ejecutado(
            clave, nombre_op, [manifiesto.comando_base],
            motivo=veredicto.motivo_bloqueo or "bloqueado por seguridad",
            bloqueado=True,
        )

    # 2) Construir tokens (shell=False). El validador ya garantizo que los
    #    argumentos casan con la whitelist; aqui solo serializamos.
    comando = normalizar_a_lista_argumentos(invocacion_norm, manifiesto)

    # 3) Dry-run: ensenamos lo que haria sin lanzar.
    if dry_run:
        return _no_ejecutado(
            clave, nombre_op, comando,
            motivo="dry-run (no se ejecuta)",
            avisos=veredicto.avisos,
        )

    # 4) Conectividad si la operacion la requiere.
    if veredicto.requiere_red:
        verificar = verificador_red or hay_conectividad
        if not verificar():
            _log.warning(
                "ejecutar_invocacion: '%s.%s' requiere red y no hay "
                "conectividad", clave, nombre_op,
            )
            return _no_ejecutado(
                clave, nombre_op, comando,
                motivo="la operacion requiere conexion y no hay conectividad",
                avisos=veredicto.avisos,
            )

    # 5) Confirmacion si procede.
    if veredicto.requiere_confirmacion:
        if confirmador is None:
            return _no_ejecutado(
                clave, nombre_op, comando,
                motivo="requiere confirmacion y no se proporciono confirmador",
                avisos=veredicto.avisos,
            )
        try:
            autorizado = bool(confirmador(veredicto.texto_confirmacion))
        except Exception as exc:  # noqa: BLE001 - el confirmador es externo
            _log.error(
                "ejecutar_invocacion: confirmador lanzo excepcion: %s", exc,
            )
            return _no_ejecutado(
                clave, nombre_op, comando,
                motivo=f"error en el confirmador: {exc}",
                avisos=veredicto.avisos,
            )
        if not autorizado:
            _log.info(
                "ejecutar_invocacion: '%s.%s' no confirmada por el usuario",
                clave, nombre_op,
            )
            return _no_ejecutado(
                clave, nombre_op, comando,
                motivo="el usuario no confirmo la operacion",
                avisos=veredicto.avisos,
            )

    # 6) Ejecutar.
    return _lanzar_subproceso(
        clave, nombre_op, comando, manifiesto, avisos=veredicto.avisos,
    )


def _lanzar_subproceso(
    clave: str,
    nombre_op: str,
    comando: list[str],
    manifiesto: "Manifiesto",
    *,
    avisos: tuple[str, ...],
) -> ResultadoEjecucion:
    timeout_s = manifiesto.seguridad.tiempo_max_segundos
    _log.info(
        "ejecutar_invocacion: lanzando '%s.%s' -> %s (timeout=%ds)",
        clave, nombre_op, comando, timeout_s,
    )
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            comando,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            cwd=str(_RAIZ_AUTOMATIZACIONES),
        )
    except subprocess.TimeoutExpired as exc:
        duracion = time.monotonic() - t0
        _log.error(
            "ejecutar_invocacion: '%s.%s' timeout tras %ds",
            clave, nombre_op, timeout_s,
        )
        out, _ = _truncar(exc.stdout.decode() if isinstance(exc.stdout, bytes)
                          else (exc.stdout or ""))
        err, _ = _truncar(exc.stderr.decode() if isinstance(exc.stderr, bytes)
                          else (exc.stderr or ""))
        return ResultadoEjecucion(
            clave_automatizacion=clave,
            nombre_operacion=nombre_op,
            comando=tuple(comando),
            ejecutado=True,
            codigo_salida=None,
            stdout=out,
            stderr=err,
            duracion_s=duracion,
            timeout=True,
            error=f"timeout tras {timeout_s}s",
            avisos=avisos,
        )
    except FileNotFoundError:
        duracion = time.monotonic() - t0
        _log.error(
            "ejecutar_invocacion: '%s.%s' comando no encontrado: %s",
            clave, nombre_op, comando[0] if comando else "?",
        )
        return _no_ejecutado(
            clave, nombre_op, comando,
            motivo=f"comando no encontrado: {comando[0] if comando else '?'}",
            avisos=avisos,
        )
    except OSError as exc:
        duracion = time.monotonic() - t0
        _log.error(
            "ejecutar_invocacion: '%s.%s' error de SO: %s",
            clave, nombre_op, exc,
        )
        return ResultadoEjecucion(
            clave_automatizacion=clave,
            nombre_operacion=nombre_op,
            comando=tuple(comando),
            ejecutado=False,
            duracion_s=duracion,
            error=f"error de sistema al lanzar: {exc}",
            avisos=avisos,
        )

    duracion = time.monotonic() - t0
    out, trunc_out = _truncar(proc.stdout or "")
    err, trunc_err = _truncar(proc.stderr or "")
    _log.info(
        "ejecutar_invocacion: '%s.%s' termino codigo=%d duracion=%.2fs",
        clave, nombre_op, proc.returncode, duracion,
    )
    return ResultadoEjecucion(
        clave_automatizacion=clave,
        nombre_operacion=nombre_op,
        comando=tuple(comando),
        ejecutado=True,
        codigo_salida=proc.returncode,
        stdout=out,
        stderr=err,
        duracion_s=duracion,
        timeout=False,
        truncado=trunc_out or trunc_err,
        avisos=avisos,
    )


def ejecutar_composicion(
    composicion_norm: dict,
    registro: dict[str, "Manifiesto"],
    *,
    confirmador: Confirmador | None = None,
    dry_run: bool = False,
    verificador_red: VerificadorRed | None = None,
) -> ResultadoComposicion:
    """Ejecuta una decision `componer` paso a paso, en orden.

    Cada paso se ejecuta como una invocacion independiente. Si un paso
    marcado con `parar_si_falla=True` no tiene exito (bloqueado, no
    confirmado, error o codigo != 0), la cadena se aborta y los pasos
    restantes no se ejecutan.

    Args:
        composicion_norm: dict normalizado de la decision `componer`, con
            `razon` y `pasos` (cada paso lleva `parar_si_falla`).
        registro: registro de manifiestos para resolver cada paso.
        confirmador, dry_run, verificador_red: ver `ejecutar_invocacion`.

    Returns:
        ResultadoComposicion. Nunca lanza.
    """
    razon = str(composicion_norm.get("razon", ""))
    pasos = composicion_norm.get("pasos") or []

    resultados: list[ResultadoEjecucion] = []
    abortada = False
    paso_fallido: int | None = None

    for idx, paso in enumerate(pasos):
        clave = str(paso.get("clave_automatizacion", "?"))
        manifiesto = registro.get(clave)
        if manifiesto is None:
            res = _no_ejecutado(
                clave, str(paso.get("nombre_operacion", "?")),
                [clave],
                motivo=f"automatizacion '{clave}' no esta en el registro",
                bloqueado=True,
            )
        else:
            res = ejecutar_invocacion(
                paso, manifiesto,
                confirmador=confirmador,
                dry_run=dry_run,
                verificador_red=verificador_red,
            )
        resultados.append(res)

        # En dry-run nunca abortamos: queremos ver la cadena completa.
        if dry_run:
            continue

        parar = bool(paso.get("parar_si_falla", False))
        if parar and not res.exito:
            _log.warning(
                "ejecutar_composicion: paso %d ('%s.%s') fallo y "
                "parar_si_falla=True; abortando cadena",
                idx, res.clave_automatizacion, res.nombre_operacion,
            )
            abortada = True
            paso_fallido = idx
            break

    return ResultadoComposicion(
        razon=razon,
        resultados=tuple(resultados),
        abortada=abortada,
        paso_fallido=paso_fallido,
    )


# Timeout para una delegacion a OpenCode (puede implicar varios pasos).
_TIMEOUT_DELEGACION_S = 600


def delegar_a_opencode(
    decision_norm: dict,
    *,
    confirmador: Confirmador | None = None,
    dry_run: bool = False,
) -> ResultadoEjecucion:
    """Ejecuta una decision `delegar_opencode`: delega una tarea libre a
    OpenCode con un agente restringido, dentro de un directorio acotado.

    SIEMPRE pide confirmacion (la delegacion deja actuar a OpenCode). Para
    ambito 'escritura' crea el sandbox si no existe. Devuelve un
    ResultadoEjecucion para que el flujo de trazas/render sea uniforme.
    """
    ambito = str(decision_norm.get("ambito", "?"))
    tarea = str(decision_norm.get("tarea", ""))
    etiqueta_op = f"delegar_{ambito}"

    veredicto = seguridad.evaluar_delegacion(decision_norm)
    if not veredicto.permitido:
        return _no_ejecutado(
            "opencode", etiqueta_op, ["opencode"],
            motivo=veredicto.motivo_bloqueo or "delegacion no permitida",
            bloqueado=True,
        )

    agente, directorio = seguridad.resolver_delegacion(ambito)
    comando = ["opencode", "run", "--agent", agente, "--dir", directorio,
               (tarea[:60] + "...") if len(tarea) > 60 else tarea]

    if dry_run:
        return _no_ejecutado(
            "opencode", etiqueta_op, comando,
            motivo="dry-run (no se delega)",
            avisos=veredicto.avisos,
        )

    # Confirmacion obligatoria.
    if confirmador is None:
        return _no_ejecutado(
            "opencode", etiqueta_op, comando,
            motivo="la delegacion requiere confirmacion y no hay confirmador",
            avisos=veredicto.avisos,
        )
    try:
        autorizado = bool(confirmador(veredicto.texto_confirmacion))
    except Exception as exc:  # noqa: BLE001
        _log.error("delegar_a_opencode: confirmador lanzo: %s", exc)
        return _no_ejecutado("opencode", etiqueta_op, comando,
                             motivo=f"error en el confirmador: {exc}")
    if not autorizado:
        return _no_ejecutado(
            "opencode", etiqueta_op, comando,
            motivo="el usuario no confirmo la delegacion",
            avisos=veredicto.avisos,
        )

    # Para escritura, garantizar que el sandbox existe.
    if ambito == "escritura":
        try:
            Path(directorio).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return ResultadoEjecucion(
                clave_automatizacion="opencode", nombre_operacion=etiqueta_op,
                comando=tuple(comando), ejecutado=False,
                error=f"no se pudo preparar el sandbox {directorio}: {exc}",
                avisos=veredicto.avisos,
            )

    _log.warning(
        "delegar_a_opencode: CAPACIDAD EXCEPCIONAL fuera de manifiestos "
        "(ambito=%s, dir=%s)", ambito, directorio,
    )
    t0 = time.monotonic()
    texto = opencode.delegar(
        tarea, agente=agente, directorio=directorio,
        timeout_s=_TIMEOUT_DELEGACION_S,
    )
    duracion = time.monotonic() - t0

    # Verificacion POSTERIOR de efectos (solo escritura): si tras delegar
    # han aparecido symlinks en el sandbox que apuntan fuera, se señala como
    # alerta en los avisos del resultado.
    avisos_post = list(veredicto.avisos)
    if ambito == "escritura":
        escapan = seguridad.symlinks_que_escapan(seguridad.sandbox_escritura())
        if escapan:
            _log.error(
                "delegar_a_opencode: EFECTOS fuera del sandbox tras delegar: %s",
                escapan,
            )
            avisos_post.append(
                f"ALERTA: tras la delegacion hay symlinks que escapan del "
                f"sandbox: {escapan[:5]}"
            )

    if texto is None:
        return ResultadoEjecucion(
            clave_automatizacion="opencode", nombre_operacion=etiqueta_op,
            comando=tuple(comando), ejecutado=True, codigo_salida=1,
            duracion_s=duracion, error="OpenCode no devolvio resultado",
            avisos=tuple(avisos_post),
        )

    salida, trunc = _truncar(texto)
    return ResultadoEjecucion(
        clave_automatizacion="opencode", nombre_operacion=etiqueta_op,
        comando=tuple(comando), ejecutado=True, codigo_salida=0,
        stdout=salida, duracion_s=duracion, truncado=trunc,
        avisos=tuple(avisos_post),
    )


def delegar_ingenieria(
    decision_norm: dict,
    *,
    confirmador: Confirmador | None = None,
    dry_run: bool = False,
    cwd: Path | None = None,
) -> ResultadoEjecucion:
    """Ejecuta una decision `delegar_ingenieria` (PI-0 'explorar', PI-1 'editar').

    Delega a OpenCode una tarea con un agente restringido, acotada al
    directorio autorizado (`--dir`):
      - 'explorar': SOLO lectura (leer/buscar/listar) con `ingeniero-lectura`;
      - 'editar': lectura + edicion/creacion confinada con `ingeniero-codigo`.
    En ambos perfiles: SIEMPRE pide confirmacion; NO ejecuta comandos de
    shell, NO accede a la red de sistema ni usa skills (lo garantiza la
    configuracion del agente). El confinamiento de la escritura al directorio
    autorizado lo asegura `seguridad.evaluar_ingenieria` (raices + denylist).
    Devuelve un ResultadoEjecucion para que el flujo de trazas/render sea
    uniforme. Nunca lanza.
    """
    perfil = str(decision_norm.get("perfil", "?"))
    tarea = str(decision_norm.get("tarea", ""))
    etiqueta_op = f"ingenieria_{perfil}"
    cwd = cwd if cwd is not None else Path.cwd()

    veredicto = seguridad.evaluar_ingenieria(decision_norm, cwd=cwd)
    if not veredicto.permitido:
        return _no_ejecutado(
            "opencode", etiqueta_op, ["opencode"],
            motivo=veredicto.motivo_bloqueo or "ingenieria no permitida",
            bloqueado=True,
        )

    agente, directorio, _motivo = ingenieria.resolver_ejecucion_ingenieria(
        decision_norm, cwd,
    )
    dir_str = str(directorio)
    comando = ["opencode", "run", "--agent", str(agente), "--dir", dir_str,
               (tarea[:60] + "...") if len(tarea) > 60 else tarea]

    if dry_run:
        return _no_ejecutado(
            "opencode", etiqueta_op, comando,
            motivo="dry-run (no se delega)",
            avisos=veredicto.avisos,
        )

    # Confirmacion obligatoria (sesion).
    if confirmador is None:
        return _no_ejecutado(
            "opencode", etiqueta_op, comando,
            motivo="la ingenieria requiere confirmacion y no hay confirmador",
            avisos=veredicto.avisos,
        )
    try:
        autorizado = bool(confirmador(veredicto.texto_confirmacion))
    except Exception as exc:  # noqa: BLE001
        _log.error("delegar_ingenieria: confirmador lanzo: %s", exc)
        return _no_ejecutado("opencode", etiqueta_op, comando,
                             motivo=f"error en el confirmador: {exc}")
    if not autorizado:
        return _no_ejecutado(
            "opencode", etiqueta_op, comando,
            motivo="el usuario no confirmo la ingenieria",
            avisos=veredicto.avisos,
        )

    modo = ("LECTURA+EDICION confinada" if ingenieria.perfil_escribe(perfil)
            else "SOLO LECTURA")
    _log.warning(
        "delegar_ingenieria: CAPACIDAD EXCEPCIONAL fuera de manifiestos "
        "(perfil=%s, dir=%s, %s)", perfil, dir_str, modo,
    )
    t0 = time.monotonic()
    texto = opencode.delegar(
        tarea, agente=str(agente), directorio=dir_str,
        timeout_s=_TIMEOUT_DELEGACION_S,
    )
    duracion = time.monotonic() - t0

    if texto is None:
        return ResultadoEjecucion(
            clave_automatizacion="opencode", nombre_operacion=etiqueta_op,
            comando=tuple(comando), ejecutado=True, codigo_salida=1,
            duracion_s=duracion, error="OpenCode no devolvio resultado",
            avisos=veredicto.avisos,
            perfil_ingenieria=perfil, directorio_autorizado=dir_str,
        )

    salida, trunc = _truncar(texto)
    return ResultadoEjecucion(
        clave_automatizacion="opencode", nombre_operacion=etiqueta_op,
        comando=tuple(comando), ejecutado=True, codigo_salida=0,
        stdout=salida, duracion_s=duracion, truncado=trunc,
        avisos=veredicto.avisos,
        perfil_ingenieria=perfil, directorio_autorizado=dir_str,
    )


# -- Comandos estructurados gobernados (decision ejecutar_comandos, PI-2) -------


def _lanzar_comando(
    argv: list[str], directorio: Path, *, avisos: tuple[str, ...],
) -> ResultadoEjecucion:
    """Lanza UN comando del lote con `subprocess.run(shell=False)`.

    Resuelve el binario a una ruta absoluta de un directorio del sistema (PATH
    controlado, anti-shadowing), usa un entorno saneado, cwd = directorio
    autorizado, stdin cerrado y timeout duro. `argv` mantiene el NOMBRE del
    binario (no la ruta resuelta) para que la traza/render sea legible.
    """
    binario = argv[0]
    ruta, motivo = comandos.resolver_binario(binario)
    if ruta is None:
        return _no_ejecutado(
            "comandos", binario, argv,
            motivo=motivo or f"binario '{binario}' no resoluble", avisos=avisos,
        )
    argv_real = [ruta, *argv[1:]]

    _log.info(
        "ejecutar_comandos: lanzando %s (dir=%s, timeout=%ds)",
        argv, directorio, comandos.TIMEOUT_COMANDO_S,
    )
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            argv_real,
            shell=False,
            capture_output=True,
            text=True,
            timeout=comandos.TIMEOUT_COMANDO_S,
            cwd=str(directorio),
            env=comandos.entorno_seguro(),
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        duracion = time.monotonic() - t0
        _log.error("ejecutar_comandos: %s timeout tras %ds", binario,
                   comandos.TIMEOUT_COMANDO_S)
        out, _ = _truncar(exc.stdout.decode() if isinstance(exc.stdout, bytes)
                          else (exc.stdout or ""))
        err, _ = _truncar(exc.stderr.decode() if isinstance(exc.stderr, bytes)
                          else (exc.stderr or ""))
        return ResultadoEjecucion(
            clave_automatizacion="comandos", nombre_operacion=binario,
            comando=tuple(argv), ejecutado=True, codigo_salida=None,
            stdout=out, stderr=err, duracion_s=duracion, timeout=True,
            error=f"timeout tras {comandos.TIMEOUT_COMANDO_S}s", avisos=avisos,
        )
    except FileNotFoundError:
        return _no_ejecutado(
            "comandos", binario, argv,
            motivo=f"comando no encontrado: {binario}", avisos=avisos,
        )
    except OSError as exc:
        duracion = time.monotonic() - t0
        _log.error("ejecutar_comandos: %s error de SO: %s", binario, exc)
        return ResultadoEjecucion(
            clave_automatizacion="comandos", nombre_operacion=binario,
            comando=tuple(argv), ejecutado=False, duracion_s=duracion,
            error=f"error de sistema al lanzar: {exc}", avisos=avisos,
        )

    duracion = time.monotonic() - t0
    out, trunc_out = _truncar(proc.stdout or "")
    err, trunc_err = _truncar(proc.stderr or "")
    _log.info("ejecutar_comandos: %s termino codigo=%d duracion=%.2fs",
              binario, proc.returncode, duracion)
    return ResultadoEjecucion(
        clave_automatizacion="comandos", nombre_operacion=binario,
        comando=tuple(argv), ejecutado=True, codigo_salida=proc.returncode,
        stdout=out, stderr=err, duracion_s=duracion, timeout=False,
        truncado=trunc_out or trunc_err, avisos=avisos,
    )


def ejecutar_comandos(
    decision_norm: dict,
    *,
    confirmador: Confirmador | None = None,
    dry_run: bool = False,
    verificador_red: VerificadorRed | None = None,
    cwd: Path | None = None,
) -> ResultadoComandos:
    """Ejecuta una decision `ejecutar_comandos` (PI-2): un lote de comandos de
    SOLO LECTURA propuestos por el cerebro y ejecutados por el Arquitecto.

    Flujo: veredicto de seguridad (revalida y confina TODO el lote) ->
    (dry-run: muestra sin ejecutar) -> red si procede -> UNA confirmacion humana
    para el lote completo -> ejecucion en orden con `shell=False`, STOP-ON-FAIL
    (si un comando falla, no se ejecutan los siguientes). Nunca lanza.
    """
    razon = str(decision_norm.get("razon", ""))

    veredicto = seguridad.evaluar_comandos(decision_norm, cwd=cwd)
    if not veredicto.permitido:
        _log.warning("ejecutar_comandos: lote bloqueado por seguridad: %s",
                     veredicto.motivo_bloqueo)
        res = _no_ejecutado(
            "comandos", "lote", ["comandos"],
            motivo=veredicto.motivo_bloqueo or "bloqueado por seguridad",
            bloqueado=True,
        )
        return ResultadoComandos(
            razon=razon, resultados=(res,), bloqueado=True,
            motivo_no_ejecucion=veredicto.motivo_bloqueo,
        )

    preparados, motivo = comandos.preparar_lote(
        decision_norm.get("comandos") or [], cwd_base=cwd,
    )
    if motivo is not None:
        # Defensa: no deberia ocurrir si el veredicto permitio.
        res = _no_ejecutado("comandos", "lote", ["comandos"],
                            motivo=motivo, bloqueado=True)
        return ResultadoComandos(razon=razon, resultados=(res,),
                                 bloqueado=True, motivo_no_ejecucion=motivo)

    # Dry-run: mostrar el argv exacto de cada comando sin ejecutar nada.
    if dry_run:
        resultados = [
            _no_ejecutado("comandos", argv[0], argv,
                          motivo="dry-run (no se ejecuta)",
                          avisos=veredicto.avisos)
            for argv, _dir, _cmd in preparados
        ]
        return ResultadoComandos(razon=razon, resultados=tuple(resultados))

    # Conectividad si algun comando la requiere (en v1: nunca).
    if veredicto.requiere_red:
        verificar = verificador_red or hay_conectividad
        if not verificar():
            res = _no_ejecutado(
                "comandos", "lote", ["comandos"],
                motivo="el lote requiere conexion y no hay conectividad",
                avisos=veredicto.avisos,
            )
            return ResultadoComandos(razon=razon, resultados=(res,),
                                     motivo_no_ejecucion="sin conectividad")

    # Confirmacion UNICA para todo el lote.
    if confirmador is None:
        res = _no_ejecutado(
            "comandos", "lote", ["comandos"],
            motivo="requiere confirmacion y no se proporciono confirmador",
            avisos=veredicto.avisos,
        )
        return ResultadoComandos(razon=razon, resultados=(res,),
                                 motivo_no_ejecucion="sin confirmador")
    try:
        autorizado = bool(confirmador(veredicto.texto_confirmacion))
    except Exception as exc:  # noqa: BLE001 - el confirmador es externo
        _log.error("ejecutar_comandos: confirmador lanzo: %s", exc)
        res = _no_ejecutado("comandos", "lote", ["comandos"],
                            motivo=f"error en el confirmador: {exc}")
        return ResultadoComandos(razon=razon, resultados=(res,),
                                 motivo_no_ejecucion="error en el confirmador")
    if not autorizado:
        res = _no_ejecutado(
            "comandos", "lote", ["comandos"],
            motivo="el usuario no confirmo los comandos",
            avisos=veredicto.avisos,
        )
        return ResultadoComandos(razon=razon, resultados=(res,),
                                 motivo_no_ejecucion="no confirmado")

    # Ejecutar en orden, STOP-ON-FAIL.
    _log.warning(
        "ejecutar_comandos: ejecutando lote de %d comando(s) de solo lectura",
        len(preparados),
    )
    resultados: list[ResultadoEjecucion] = []
    abortada = False
    comando_fallido: int | None = None
    for idx, (argv, directorio, _cmd) in enumerate(preparados):
        # El aviso de capacidad va solo en el primer resultado (no repetir).
        res = _lanzar_comando(
            argv, directorio, avisos=veredicto.avisos if idx == 0 else (),
        )
        resultados.append(res)
        if not res.exito:
            _log.warning(
                "ejecutar_comandos: comando %d (%s) fallo; stop-on-fail",
                idx, res.nombre_operacion,
            )
            abortada = True
            comando_fallido = idx
            break

    return ResultadoComandos(
        razon=razon, resultados=tuple(resultados),
        abortada=abortada, comando_fallido=comando_fallido,
    )
