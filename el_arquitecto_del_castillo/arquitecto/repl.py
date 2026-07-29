"""
REPL del Arquitecto del Castillo dirigido por el cerebro (Fase 4).

Sustituye al bucle del monolito (`el_arquitecto_del_castillo.py`) por uno
que usa OpenCode como motor de razonamiento. Por cada turno:

    1. Lee la peticion del usuario.
    2. La envia al cerebro (`SesionCerebro.turno`), que devuelve una
       decision JSON ya validada contra el contrato y el registro.
    3. Enruta esa decision:
         - texto puro    -> responder, aclarar, proponer_nueva,
                            rechazar_peligro
         - ejecucion     -> invocar, componer
         - confirmacion  -> pedir_confirmacion
    4. Las ejecuciones pasan por `ejecutor` (que a su vez consulta
       `seguridad`). Las que requieren confirmacion la piden por terminal.
    5. Registra el turno en `trazas`.

El nucleo de enrutado (`procesar_respuesta`) esta separado de la E/S para
poder testearlo sin terminal. El bucle (`repl_cerebro`) es la capa fina de
presentacion.

Si OpenCode no esta disponible, `repl_cerebro` devuelve False sin entrar
al bucle, para que el llamador (el monolito) caiga al REPL legacy (Qwen).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

_RAIZ_AUTOMATIZACIONES = Path(__file__).resolve().parent.parent.parent
if str(_RAIZ_AUTOMATIZACIONES) not in sys.path:
    sys.path.insert(0, str(_RAIZ_AUTOMATIZACIONES))

from comun import notificar, configurar_logger, cargar_config  # noqa: E402
from comun import heraldo  # noqa: E402
from arquitecto import ejecutor, ingenieria, trazas  # noqa: E402
from arquitecto.cerebro import SesionCerebro  # noqa: E402
from arquitecto.registro import cargar_registro  # noqa: E402

if TYPE_CHECKING:
    from arquitecto.cerebro import RespuestaCerebro
    from arquitecto.ejecutor import Confirmador, ResultadoEjecucion, VerificadorRed
    from arquitecto.registro import Manifiesto

try:
    import readline  # noqa: F401 — habilita historial en input()
except ImportError:
    pass

_RUTA_AUTO = Path(__file__).resolve().parent.parent

logger = configurar_logger("arquitecto.repl")

CONSEJERO = "el_arquitecto_del_castillo"

# -- Colores ANSI --------------------------------------------------------------

C = {
    "verde": "\033[32m",
    "amarillo": "\033[33m",
    "rojo": "\033[31m",
    "cyan": "\033[36m",
    "dim": "\033[2m",
    "bold": "\033[1m",
    "reset": "\033[0m",
}


# -- Resultado de un turno -----------------------------------------------------


@dataclass(frozen=True)
class ResultadoTurno:
    """Lo que el REPL debe mostrar tras procesar una decision del cerebro.

    Separa el QUE mostrar (mensajes) del COMO mostrarlo (el bucle). Asi el
    enrutado es testeable sin terminal.
    """

    decision: str
    mensajes: tuple[str, ...]
    resultados: tuple = ()        # tuple[ResultadoEjecucion, ...]
    ejecuto_algo: bool = False


# -- Confirmador por terminal --------------------------------------------------


def confirmador_terminal(texto: str) -> bool:
    """Pide confirmacion al usuario por terminal. Default seguro: NO.

    Solo acepta 's', 'si', 'y', 'yes' (case-insensitive) como afirmativo.
    Cualquier otra respuesta, incluyendo vacío o prefijos tipo 'salir',
    se interpreta como NO.

    Si stdin no es un TTY (p.ej. pipe/redirección), cancela directamente
    sin leer — evita que líneas del pipe colisionen con la confirmación.
    EOF o Ctrl+C también cuentan como no.
    """
    import sys
    if not sys.stdin.isatty():
        return False

    bold, cyan, dim, r = C["bold"], C["cyan"], C["dim"], C["reset"]
    print(f"\n  {cyan}{texto}{r}")
    try:
        resp = input(f"  {bold}¿Ejecutar? (escribe 's', 'si', 'y' o 'yes') [s/N]{r} ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return resp in ("s", "si", "y", "yes")


# -- Render de resultados de ejecucion -----------------------------------------


def _render_resultado(res: "ResultadoEjecucion") -> list[str]:
    """Convierte un ResultadoEjecucion en lineas legibles para terminal."""
    verde, rojo, amarillo, dim, r = (
        C["verde"], C["rojo"], C["amarillo"], C["dim"], C["reset"]
    )
    etiqueta = f"{res.clave_automatizacion}.{res.nombre_operacion}"
    lineas: list[str] = []

    if not res.ejecutado:
        color = rojo if res.bloqueado else amarillo
        motivo = res.motivo_no_ejecucion or res.error or "no ejecutado"
        lineas.append(f"  {color}✗ {etiqueta}: {motivo}{r}")
        return lineas

    if res.timeout:
        lineas.append(f"  {rojo}✗ {etiqueta}: timeout{r}")
    elif res.codigo_salida == 0:
        lineas.append(
            f"  {verde}✓ {etiqueta}{r} {dim}({res.duracion_s:.1f}s){r}"
        )
    else:
        lineas.append(
            f"  {rojo}✗ {etiqueta}: codigo {res.codigo_salida}{r}"
        )

    salida = (res.stdout or "").strip()
    if salida:
        lineas.append(f"{dim}{salida}{r}")
    err = (res.stderr or "").strip()
    if err and res.codigo_salida != 0:
        lineas.append(f"  {rojo}{err}{r}")
    for aviso in res.avisos:
        lineas.append(f"  {amarillo}⚠ {aviso}{r}")
    return lineas


# -- Enrutado de decisiones (nucleo testeable) ---------------------------------


def procesar_respuesta(
    respuesta: "RespuestaCerebro",
    registro: dict[str, "Manifiesto"],
    *,
    confirmador: "Confirmador" = confirmador_terminal,
    dry_run: bool = False,
    verificador_red: "VerificadorRed | None" = None,
    ruta_trazas: Path | None = None,
    peticion_usuario: str = "",
) -> ResultadoTurno:
    """Enruta una RespuestaCerebro ya validada y, si procede, ejecuta.

    Registra el turno en trazas (a `ruta_trazas`, o al fichero por defecto
    si es None) y devuelve un ResultadoTurno con lo que mostrar.

    Nunca lanza: cualquier problema se refleja en los mensajes.
    """
    norm = respuesta.normalizada or {}
    decision = str(norm.get("decision", respuesta.decision or "?"))
    mensajes: list[str] = []
    resultados: list = []

    if decision == "responder":
        mensajes.append(norm.get("texto", ""))

    elif decision == "aclarar":
        mensajes.append(norm.get("pregunta", ""))
        for opcion in norm.get("opciones", []) or []:
            mensajes.append(f"  · {opcion}")

    elif decision == "proponer_nueva":
        mensajes.append(
            f"Propuesta de nueva automatizacion: "
            f"{norm.get('nombre_sugerido', '?')}"
        )
        mensajes.append(norm.get("descripcion", ""))
        mensajes.append(f"Justificacion: {norm.get('justificacion', '')}")
        if norm.get("encaje_ecosistema"):
            mensajes.append(f"Encaje: {norm['encaje_ecosistema']}")

    elif decision == "rechazar_peligro":
        mensajes.append(norm.get("motivo", "Peticion rechazada."))
        if norm.get("sugerencia_segura"):
            mensajes.append(f"Alternativa: {norm['sugerencia_segura']}")

    elif decision == "invocar":
        res = _ejecutar_una(
            norm, registro, confirmador=confirmador,
            dry_run=dry_run, verificador_red=verificador_red,
        )
        resultados.append(res)
        mensajes.extend(_render_resultado(res))

    elif decision == "componer":
        comp = ejecutor.ejecutar_composicion(
            norm, registro, confirmador=confirmador,
            dry_run=dry_run, verificador_red=verificador_red,
        )
        mensajes.append(f"Cadena: {norm.get('razon', '')}")
        for res in comp.resultados:
            resultados.append(res)
            mensajes.extend(_render_resultado(res))
        if comp.abortada:
            mensajes.append(
                f"  {C['rojo']}Cadena abortada en el paso "
                f"{comp.paso_fallido}.{C['reset']}"
            )

    elif decision == "pedir_confirmacion":
        mensaje = norm.get("mensaje", "¿Confirmas la accion?")
        invocacion = norm.get("invocacion") or {}
        clave = invocacion.get("clave_automatizacion", "?")
        manifiesto = registro.get(clave)
        if manifiesto is None:
            mensajes.append(
                f"{C['rojo']}No puedo confirmar: automatizacion '{clave}' "
                f"no esta en el registro.{C['reset']}"
            )
        elif not confirmador(mensaje):
            mensajes.append(f"{C['dim']}Accion cancelada.{C['reset']}")
        else:
            # El usuario YA confirmo aqui; el ejecutor no debe re-preguntar.
            res = ejecutor.ejecutar_invocacion(
                invocacion, manifiesto,
                confirmador=lambda _texto: True,
                dry_run=dry_run, verificador_red=verificador_red,
            )
            resultados.append(res)
            mensajes.extend(_render_resultado(res))

    elif decision == "delegar_opencode":
        mensajes.append(
            f"{C['dim']}Tarea para OpenCode ({norm.get('ambito', '?')}): "
            f"{norm.get('razon', '')}{C['reset']}"
        )
        res = ejecutor.delegar_a_opencode(
            norm, confirmador=confirmador, dry_run=dry_run,
        )
        resultados.append(res)
        mensajes.extend(_render_resultado(res))

    elif decision == "delegar_ingenieria":
        _perfil_ing = norm.get("perfil", "?")
        _modo_ing = ("lectura+edicion confinada"
                     if ingenieria.perfil_escribe(_perfil_ing)
                     else "solo lectura")
        mensajes.append(
            f"{C['dim']}Ingenieria ({_perfil_ing}, {_modo_ing}): "
            f"{norm.get('razon', '')}{C['reset']}"
        )
        res = ejecutor.delegar_ingenieria(
            norm, confirmador=confirmador, dry_run=dry_run,
        )
        resultados.append(res)
        mensajes.extend(_render_resultado(res))

    elif decision == "ejecutar_comandos":
        lote = ejecutor.ejecutar_comandos(
            norm, confirmador=confirmador, dry_run=dry_run,
            verificador_red=verificador_red,
        )
        mensajes.append(
            f"{C['dim']}Comandos (solo lectura): {norm.get('razon', '')}"
            f"{C['reset']}"
        )
        for res in lote.resultados:
            resultados.append(res)
            mensajes.extend(_render_resultado(res))
        if lote.abortada:
            mensajes.append(
                f"  {C['rojo']}Lote detenido en el comando "
                f"{lote.comando_fallido} (stop-on-fail).{C['reset']}"
            )

    else:
        mensajes.append(
            f"{C['rojo']}Decision desconocida: {decision}{C['reset']}"
        )

    # Registrar la traza del turno (no bloquea ni rompe el flujo si falla).
    trazas.registrar_turno(
        peticion_usuario=peticion_usuario,
        decision=decision,
        valida=respuesta.valida,
        turno_id=respuesta.turno_id,
        reintentos=respuesta.reintentos,
        requiere_confirmacion=respuesta.requiere_confirmacion,
        motivo_invalidez=respuesta.motivo_invalidez,
        resultados=resultados,
        ruta=ruta_trazas,
    )

    return ResultadoTurno(
        decision=decision,
        mensajes=tuple(m for m in mensajes if m is not None),
        resultados=tuple(resultados),
        ejecuto_algo=any(getattr(r, "ejecutado", False) for r in resultados),
    )


def _ejecutar_una(
    norm: dict,
    registro: dict[str, "Manifiesto"],
    *,
    confirmador: "Confirmador",
    dry_run: bool,
    verificador_red: "VerificadorRed | None",
) -> "ResultadoEjecucion":
    """Resuelve el manifiesto de una invocacion y la ejecuta."""
    clave = norm.get("clave_automatizacion", "?")
    manifiesto = registro.get(clave)
    if manifiesto is None:
        return ejecutor.ResultadoEjecucion(
            clave_automatizacion=clave,
            nombre_operacion=norm.get("nombre_operacion", "?"),
            comando=(clave,),
            ejecutado=False,
            bloqueado=True,
            motivo_no_ejecucion=f"automatizacion '{clave}' no esta en el registro",
        )
    return ejecutor.ejecutar_invocacion(
        norm, manifiesto, confirmador=confirmador,
        dry_run=dry_run, verificador_red=verificador_red,
    )


# -- Presentacion --------------------------------------------------------------


def _banner(n_manifiestos: int) -> str:
    bold, cyan, dim, r = C["bold"], C["cyan"], C["dim"], C["reset"]
    return (
        f"\n  {bold}{cyan}El Arquitecto del Castillo{r}\n"
        f"  {dim}cerebro: OpenCode · {n_manifiestos} automatizaciones en el "
        f"catalogo{r}\n"
        f"  {dim}escribe lo que necesitas, o 'ayuda' / 'salir'{r}\n"
    )


def _ayuda() -> str:
    bold, dim, r = C["bold"], C["dim"], C["reset"]
    return (
        f"\n  {bold}El Arquitecto interpreta lenguaje natural.{r}\n"
        f"  {dim}Pidele cosas como 'que errores hay' o 'busca duplicados'.\n"
        f"  Comandos del REPL: ayuda · limpiar · salir{r}\n"
    )


def _mostrar(resultado: ResultadoTurno) -> None:
    cyan, r = C["cyan"], C["reset"]
    print()
    for linea in resultado.mensajes:
        # Las lineas de _render_resultado ya traen su propio formato/sangria.
        if linea.startswith("  ") or "\033[" in linea:
            print(linea)
        else:
            print(f"  {cyan}{linea}{r}")
    print()


# -- Contexto de turno para re-inyección al cerebro ----------------------------


def _construir_contexto_turno(resultado: ResultadoTurno) -> str:
    """Genera un snapshot del estado REAL del ultimo turno para reinyectarlo al cerebro.

    Solo se genera contenido cuando hubo ejecucion o bloqueo; en turnos de
    texto puro (responder, aclarar, proponer_nueva) devuelve cadena vacia
    para no inflar el prompt innecesariamente.

    INVARIANTE: el render visible al usuario (exito/bloqueo) SIEMPRE lo produce
    _render_resultado, nunca el texto del LLM. Esta funcion solo informa al
    cerebro de lo que ocurrio para que no fabule en turnos posteriores.
    """
    if not resultado.resultados:
        return ""

    lineas: list[str] = [
        "Estado del turno anterior (informacion factual para tu razonamiento):"
    ]
    for res in resultado.resultados:
        etiqueta = (
            f"{getattr(res, 'clave_automatizacion', '?')}."
            f"{getattr(res, 'nombre_operacion', '?')}"
        )
        if not getattr(res, "ejecutado", False):
            bloqueado = getattr(res, "bloqueado", False)
            motivo = (
                getattr(res, "motivo_no_ejecucion", None)
                or getattr(res, "error", None)
                or "no ejecutado"
            )
            estado = "BLOQUEADO" if bloqueado else "NO_EJECUTADO"
            lineas.append(f"  - {etiqueta}: {estado} — {motivo}")
        elif getattr(res, "timeout", False):
            lineas.append(f"  - {etiqueta}: TIMEOUT")
        else:
            codigo = getattr(res, "codigo_salida", None)
            duracion = getattr(res, "duracion_s", 0.0)
            if codigo == 0:
                lineas.append(
                    f"  - {etiqueta}: EXITO (exit 0, {duracion:.1f}s)"
                )
            else:
                lineas.append(
                    f"  - {etiqueta}: FALLO (exit {codigo}, {duracion:.1f}s)"
                )

    return "\n".join(lineas)


# -- Bucle principal -----------------------------------------------------------


def repl_cerebro(
    *,
    ruta_ecosistema: Path | None = None,
    dry_run: bool = False,
) -> bool:
    """Bucle interactivo dirigido por el cerebro.

    Returns:
        True si la sesion se desarrollo con OpenCode. False si OpenCode no
        estaba disponible (el llamador debe caer al REPL legacy).
    """
    base = ruta_ecosistema or _RAIZ_AUTOMATIZACIONES
    config = cargar_config(_RUTA_AUTO)
    registro = cargar_registro(base)

    bold, cyan, dim, rojo, r = (
        C["bold"], C["cyan"], C["dim"], C["rojo"], C["reset"]
    )

    print("\033[2J\033[H", end="")  # clear
    print(_banner(len(registro)))

    with SesionCerebro(registro) as cerebro:
        if not cerebro.disponible:
            # P0: ya NO hay fallback a Qwen. Devolvemos False y el llamador
            # (main del monolito) aplica el fallo seguro: informa y no
            # ejecuta nada fuera del camino gobernado.
            logger.warning(
                "repl_cerebro: cerebro no disponible; devolviendo False "
                "(el llamador aplicara el fallo seguro)"
            )
            return False

        notif = config.get("notificacion", {})
        notificar(CONSEJERO, "El Arquitecto esta listo", "info",
                  notif.get("duracion", 3000))
        if dry_run:
            print(f"  {dim}[modo dry-run: no se ejecutara nada]{r}\n")

        _contexto_ultimo_turno: str = ""

        while True:
            try:
                entrada = input(f"  {bold}{cyan}arqui>{r} ").strip()
            except EOFError:
                print(f"\n\n  {dim}Hasta la proxima.{r}\n")
                break
            except KeyboardInterrupt:
                print()
                continue

            if not entrada:
                continue
            low = entrada.lower()
            if low in ("salir", "exit", "q"):
                print(f"\n  {dim}Hasta la proxima.{r}\n")
                break
            if low in ("ayuda", "help"):
                print(_ayuda())
                continue
            if low in ("limpiar", "clear"):
                print("\033[2J\033[H", end="")
                print(_banner(len(registro)))
                continue

            # Espera ambientada (cosmética, en hilo aparte). Fuera de TTY es
            # un no-op total: no imprime, no lanza hilo, cero latencia. El
            # trabajo real bloquea de todos modos, así que no añade latencia.
            try:
                with heraldo.pensando(tema=heraldo.tema_actual()):
                    respuesta = cerebro.turno(entrada, contexto_extra=_contexto_ultimo_turno)
            except KeyboardInterrupt:
                # El usuario interrumpió mientras el cerebro pensaba.
                # El context manager del heraldo ya limpió el terminal;
                # aquí solo informamos y volvemos al prompt.
                print(f"\n  {dim}Interrumpido.{r}\n")
                continue

            resultado = procesar_respuesta(
                respuesta, registro,
                confirmador=confirmador_terminal,
                dry_run=dry_run,
                peticion_usuario=entrada,
            )
            _mostrar(resultado)

            # Construir el contexto que se reinyectará en el SIGUIENTE turno.
            # Solo se incluye cuando hubo ejecución o bloqueo relevante,
            # para que el cerebro razone sobre hechos reales y no alucine.
            _contexto_ultimo_turno = _construir_contexto_turno(resultado)

    return True
