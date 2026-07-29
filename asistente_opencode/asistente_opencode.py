#!/usr/bin/env python3
"""
Nombre: Asistente OpenCode
Propósito: envuelve capacidades de SOLO LECTURA de OpenCode (explicar,
    analizar, resumir, buscar) como un comando del Castillo, para que el
    Arquitecto pueda invocarlas dentro de su flujo seguro normal.
Parte del ecosistema: herramienta para Claude Code / OpenCode.
Autor: generado por el Agente Arquitecto
Versión: 1.0.0

Uso:
    asistente explicar  /ruta/al/fichero.py
    asistente analizar  /ruta/al/fichero.py
    asistente resumir   /ruta/al/directorio
    asistente buscar    "donde se valida el login"

Todas las acciones usan el agente OpenCode restringido `arquitecto-lectura`
(sin escritura, sin bash, sin red de herramientas): solo lee y responde.
"""

# ── Configuración ────────────────────────────────────────────────
RUTA_BASE = "/home/sun/Escritorio/automatizaciones"
RUTA_LOGS = f"{RUTA_BASE}/logs"
NOMBRE_LOG = "asistente_opencode.log"

AGENTE_LECTURA = "arquitecto-lectura"
TIMEOUT_S = 240

# ── Imports ──────────────────────────────────────────────────────
import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, RUTA_BASE)

from comun import configurar_logger  # noqa: E402
from comun import heraldo  # noqa: E402
from comun.opencode import _parsear_stream_ndjson, BINARIO  # noqa: E402

# ── Logger ───────────────────────────────────────────────────────
logger = configurar_logger("asistente_opencode")

# ── Plantillas de prompt por acción ──────────────────────────────
# La ruta del objetivo se incrusta en el prompt y el agente la lee con su
# herramienta `read` (tiene permiso de lectura y se le pasa `--dir`). NO se
# usa el flag `-f` de opencode porque es de tipo array y se traga el mensaje.
def _prompt(accion: str, objetivo: str) -> str:
    if accion == "explicar":
        return (
            f"Explica de forma clara y concisa qué hace el fichero «{objetivo}» "
            f"y cómo: su propósito, sus partes principales y el flujo general. "
            f"Léelo primero. En español."
        )
    if accion == "analizar":
        return (
            f"Analiza el fichero «{objetivo}»: estructura, patrones de diseño, "
            f"posibles fallos, riesgos de seguridad y mejoras (sin implementarlas). "
            f"Léelo primero. Sé concreto. En español."
        )
    if accion == "resumir":
        return (
            f"Recorre el directorio «{objetivo}» y resume su estructura y "
            f"propósito: qué es el proyecto, sus módulos/carpetas principales y "
            f"cómo encajan. En español."
        )
    # buscar
    return (
        f"Localiza en este proyecto lo siguiente y di en qué ficheros y "
        f"funciones está, con una breve explicación. Petición: {objetivo}. "
        f"En español."
    )


# ── Funciones ────────────────────────────────────────────────────
def construir_comando(accion: str, objetivo: str) -> tuple[list[str], str]:
    """Construye la lista de tokens para `opencode run` y valida el objetivo.

    Returns:
        (comando, descripcion_objetivo). Lanza SystemExit (código 2) si el
        objetivo no es válido para la acción.
    """
    cmd = [BINARIO, "run", "--agent", AGENTE_LECTURA, "--format", "json"]

    if accion in ("explicar", "analizar"):
        ruta = Path(objetivo).expanduser().resolve()
        if not ruta.is_file():
            _salir_error(f"No es un fichero existente: {ruta}")
        # Elegir --dir de forma condicional:
        # - Si el fichero está DENTRO de RUTA_BASE → usar RUTA_BASE para que
        #   el agente tenga acceso a comun/, config, etc. (fix N-003).
        # - Si el fichero está FUERA de RUTA_BASE → usar el directorio padre
        #   del fichero para que sea legible (fix R3-003).
        ruta_base_path = Path(RUTA_BASE).resolve()
        try:
            ruta.relative_to(ruta_base_path)
            directorio_trabajo = RUTA_BASE
        except ValueError:
            directorio_trabajo = str(ruta.parent)
        cmd += ["--dir", directorio_trabajo]
        cmd.append(_prompt(accion, str(ruta)))
        return cmd, str(ruta)

    if accion == "resumir":
        ruta = Path(objetivo).expanduser()
        if ruta.exists() and ruta.is_file():
            _salir_error(
                f"'resumir' opera sobre directorios. "
                f"Para un fichero usa: asistente explicar {ruta}"
            )
        if not ruta.is_dir():
            _salir_error(f"No es un directorio existente: {ruta}")
        cmd += ["--dir", str(ruta)]
        cmd.append(_prompt("resumir", str(ruta)))
        return cmd, str(ruta)

    # buscar: el objetivo es una consulta en lenguaje natural; se busca en
    # el directorio de trabajo actual.
    if not objetivo.strip():
        _salir_error("buscar requiere una consulta no vacía")
    directorio = Path.cwd()
    cmd += ["--dir", str(directorio)]
    cmd.append(_prompt("buscar", objetivo))
    return cmd, f"'{objetivo}' en {directorio}"


def ejecutar(accion: str, objetivo: str) -> int:
    """Lanza OpenCode en modo solo-lectura y muestra la respuesta."""
    cmd, descripcion = construir_comando(accion, objetivo)

    logger.info("asistente %s sobre %s", accion, descripcion)
    print(f"  OpenCode ({accion}) sobre {descripcion}...\n")

    try:
        # Spinner medieval del Heraldo SOLO durante la espera bloqueante de
        # OpenCode. Fuera de TTY (pipe, captura por scripts, uso desde el REPL
        # u otra automatización) es no-op total: cero salida, cero latencia, y
        # la salida funcional queda byte-idéntica. El toggle es el mismo que
        # usa `arqui`: env ARQUI_TEMA=clasico o el fichero de tema.
        with heraldo.pensando(tema=heraldo.tema_actual()):
            res = subprocess.run(
                cmd,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                text=True,
                timeout=TIMEOUT_S,
            )
    except subprocess.TimeoutExpired:
        logger.error("timeout tras %ss en %s %s", TIMEOUT_S, accion, objetivo)
        print(f"  Tiempo agotado ({TIMEOUT_S}s).", file=sys.stderr)
        return 1
    except OSError as exc:
        logger.error("error lanzando opencode: %s", exc)
        print(f"  Error lanzando OpenCode: {exc}", file=sys.stderr)
        return 1

    if res.returncode != 0:
        logger.error("opencode returncode=%s stderr=%s", res.returncode, res.stderr[:300])
        print(f"  OpenCode falló (código {res.returncode}).", file=sys.stderr)
        if res.stderr.strip():
            print(f"  {res.stderr.strip()[:300]}", file=sys.stderr)
        return res.returncode

    texto, _ = _parsear_stream_ndjson(res.stdout)
    if texto is None or not texto.strip():
        logger.warning("respuesta vacía de opencode para %s %s", accion, objetivo)
        print("  OpenCode no devolvió texto.", file=sys.stderr)
        return 1

    print(texto.strip())
    logger.info("asistente %s completado (%d chars)", accion, len(texto))
    return 0


def _salir_error(mensaje: str) -> None:
    logger.error(mensaje)
    print(f"  {mensaje}", file=sys.stderr)
    sys.exit(2)


# ── Main ─────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Asistente OpenCode — capacidades de solo lectura del Castillo",
    )
    parser.add_argument(
        "accion",
        choices=["explicar", "analizar", "resumir", "buscar"],
        help="Qué hacer: explicar/analizar un fichero, resumir un directorio, buscar en el proyecto",
    )
    parser.add_argument(
        "objetivo",
        nargs="+",
        help="Ruta del fichero/directorio, o la consulta para 'buscar'",
    )
    args = parser.parse_args()
    objetivo = " ".join(args.objetivo)

    try:
        sys.exit(ejecutar(args.accion, objetivo))
    except KeyboardInterrupt:
        print("\n  Cancelado.", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
