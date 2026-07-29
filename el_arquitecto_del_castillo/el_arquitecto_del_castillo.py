#!/usr/bin/env python3
"""
El Arquitecto del Castillo — punto de entrada (lanzado por `arqui`).
Parte del ecosistema: orquestacion.

ARQUITECTURA ACTUAL ("Arquitecto soberano con cerebro OpenCode"):

    Usuario -> arqui -> main() -> repl_cerebro (paquete `arquitecto/`)
        - OpenCode es el CEREBRO restringido (agente `arquitecto-cerebro`,
          sin bash/edit/red): razona e emite UN JSON con la decision.
        - El ARQUITECTO es la autoridad: valida la decision contra los
          manifiestos (`validador`), aplica politica de seguridad
          (`seguridad`), pide confirmacion humana y ejecuta con
          `subprocess.run(shell=False)` (`ejecutor`). Todo queda en `trazas`.
        - `delegar_opencode` es una EXCEPCION gobernada (tarea libre fuera de
          manifiestos): siempre confirmada, agente sin bash, escritura
          confinada al sandbox `~/arqui-sandbox`.

    FALLO SEGURO: si OpenCode no esta disponible, `main()` NO ejecuta nada
    (no hay fallback a shell ni a un LLM local). Ver `_fallo_seguro`.

CODIGO LEGACY INERTE: las funciones de abajo (dict COMANDOS, matching por
keyword/URL, splash, ayuda y los stubs `repl`/`ejecutar_comando`/
`_generar_comando_fs`) son el antiguo REPL por keywords. NO son alcanzables
desde `arqui` y no ejecutan nada; se conservan unicamente porque las pruebas
unitarias existentes las cubren y verifican que siguen inertes.
"""

import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from comun import configurar_logger

try:
    import readline  # noqa: F401 — habilita historial en input()
except ImportError:
    pass

RUTA_AUTO = Path(__file__).resolve().parent
RUTA_ECOSISTEMA = Path(__file__).resolve().parent.parent

logger = configurar_logger("el_arquitecto_del_castillo")

CONSEJERO = "el_arquitecto_del_castillo"

# -- Colores ANSI ---------------------------------------------------------------

C = {
    "verde": "\033[32m",
    "amarillo": "\033[33m",
    "rojo": "\033[31m",
    "cyan": "\033[36m",
    "dim": "\033[2m",
    "bold": "\033[1m",
    "reset": "\033[0m",
}

# ==============================================================================
# LEGACY INERTE — antiguo REPL por keywords. NO alcanzable desde `arqui`.
# Nada de lo que sigue ejecuta acciones; se conserva solo para las pruebas
# unitarias que verifican que sigue inerte. El camino vivo es `repl_cerebro`.
# ==============================================================================

# -- Comandos del ecosistema (datos legacy inertes) ------------------------------

COMANDOS: dict[str, dict] = {
    "arranque": {
        "cmd": "arranque",
        "desc": "Checklist del sistema",
        "keywords": ["check", "arranque", "salud", "checklist"],
    },
    "errores": {
        "cmd": "errores",
        "desc": "Ver errores recientes",
        "keywords": ["error", "errores", "fallo", "fallos", "problema", "log"],
    },
    "castillo": {
        "cmd": "castillo",
        "desc": "Dashboard completo",
        "keywords": ["dashboard", "todo", "resumen", "castillo", "general"],
    },
    "secretos": {
        "cmd": "secretos --amenazas",
        "desc": "Escaneo de amenazas",
        "keywords": ["amenaza", "seguridad", "virus", "defender", "escanear"],
    },
    "dupes": {
        "cmd": "dupes",
        "desc": "Buscar duplicados",
        "keywords": ["duplicado", "duplicados", "repetido", "repetidos", "copia", "copias"],
    },
    "feeds": {
        "cmd": "feeds",
        "desc": "Noticias RSS",
        "keywords": ["noticia", "noticias", "feed", "feeds", "rss", "articulo", "articulos"],
    },
    "modo": {
        "cmd": "modo",
        "desc": "Lanzar modo trabajo",
        "keywords": ["trabajo", "dev", "musica", "workspace"],
        "pass_args": ["dev", "musica", "lista", "parar"],
    },
    "zen": {
        "cmd": "zen",
        "desc": "Modo zen",
        "keywords": ["zen", "silencio", "concentr", "foco", "relajar"],
    },
    "trad": {
        "cmd": "trad",
        "desc": "Traducir texto",
        "keywords": ["traduc", "translate", "ingles", "espanol"],
    },
    "rast": {
        "cmd": "rast",
        "desc": "Reiniciar/apagar/suspender PC",
        "keywords": ["reinici", "reiniciar", "apagar", "apaga", "suspender", "suspend", "reboot", "shutdown", "rast"],
    },
    "yt": {
        "cmd": "yt",
        "desc": "Descargar audio de YouTube/SoundCloud",
        "keywords": ["youtube", "soundcloud", "mp3"],
    },
    "buscar": {
        "cmd": "buscar",
        "desc": "Buscar archivos",
        "keywords": ["buscar", "encontrar", "donde"],
    },
    "abrir_carpeta": {
        "cmd": "_fs",
        "desc": "Abrir carpeta en explorador",
        "keywords": ["carpeta", "directorio", "folder", "explorador"],
    },
    "listar": {
        "cmd": "_fs",
        "desc": "Listar archivos/carpetas",
        "keywords": ["listar", "lista", "contenido", "archivos"],
    },
    "arbol": {
        "cmd": "_fs",
        "desc": "Arbol de directorios",
        "keywords": ["arbol", "árbol", "tree", "estructura"],
    },
    "informe": {
        "cmd": "informe",
        "desc": "Informe semanal",
        "keywords": ["informe", "reporte", "semanal"],
    },
    "explicar": {
        "cmd": "explicar",
        "desc": "Explicar error",
        "keywords": ["explicar", "stacktrace", "traceback", "que significa"],
    },
    "cripta": {
        "cmd": "cripta estado",
        "desc": "Estado de la boveda",
        "keywords": ["cripta", "cifr", "boveda", "gpg"],
    },
    "api": {
        "cmd": "api lista",
        "desc": "Listar APIs",
        "keywords": ["api", "credencial", "key", "token"],
    },
    "changelog": {
        "cmd": "changelog",
        "desc": "Generar changelog",
        "keywords": ["changelog", "cambios", "release", "version"],
    },
    "eventos": {
        "cmd": "eventos --status",
        "desc": "Estado de eventos",
        "keywords": ["evento", "usb", "bateria"],
    },
    "cadena": {
        "cmd": "cadena --lista",
        "desc": "Cadenas activas",
        "keywords": ["cadena", "pipeline", "encadenar"],
    },
    "red": {
        "cmd": "ping -c 3 1.1.1.1 && echo '--- Interfaces ---' && ip -br addr",
        "desc": "Estado de la red",
        "keywords": ["red", "internet", "conexion", "wifi", "ethernet", "ping", "network"],
    },
    "disco": {
        "cmd": "df -h / /home && echo '--- Top 10 carpetas ---' && du -sh ~/* 2>/dev/null | sort -rh | head -10",
        "desc": "Espacio en disco",
        "keywords": ["disco", "espacio", "almacenamiento", "storage", "lleno", "libre"],
    },
    "procesos": {
        "cmd": "ps aux --sort=-%mem | head -15",
        "desc": "Procesos que más consumen",
        "keywords": ["proceso", "memoria", "ram", "cpu", "consumo", "lento"],
    },
    "temperatura": {
        "cmd": "sensors 2>/dev/null || cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null | while read t; do echo \"$((t/1000))°C\"; done",
        "desc": "Temperatura del sistema",
        "keywords": ["temperatura", "caliente", "temp", "sensor", "grados"],
    },
    "gpu": {
        "cmd": "nvidia-smi",
        "desc": "Estado de la GPU",
        "keywords": ["gpu", "nvidia", "grafica", "vram", "cuda"],
    },
    "paquetes": {
        "cmd": "pacman -Qu 2>/dev/null && echo '--- AUR ---' && yay -Qua 2>/dev/null || echo 'Sin actualizaciones pendientes'",
        "desc": "Paquetes pendientes de actualizar",
        "keywords": ["paquete", "actualizar", "update", "pacman", "yay"],
    },
    "servicios": {
        "cmd": "systemctl --user list-timers --no-pager && echo '--- Servicios ---' && systemctl --user list-units --type=service --state=running --no-pager",
        "desc": "Timers y servicios activos",
        "keywords": ["servicio", "timer", "systemd", "daemon"],
    },
    "opera": {
        "cmd": "opera &",
        "desc": "Abrir navegador Opera",
        "keywords": ["opera", "navegador", "browser"],
    },
    "terminal": {
        "cmd": "alacritty &",
        "desc": "Abrir terminal",
        "keywords": ["terminal", "consola", "alacritty"],
    },
    "discord": {
        "cmd": "omarchy-launch-webapp https://discord.com/channels/@me &",
        "desc": "Abrir Discord",
        "keywords": ["discord", "chat"],
    },
    "whatsapp": {
        "cmd": "omarchy-launch-webapp https://web.whatsapp.com/ &",
        "desc": "Abrir WhatsApp",
        "keywords": ["whatsapp", "whats"],
    },
}

# Patron para URLs de YouTube y SoundCloud
_RE_URL_MEDIA = re.compile(
    r"(https?://(?:www\.)?(?:youtube\.com|youtu\.be|soundcloud\.com)/\S+)"
)


# -- Deteccion de estado del ecosistema -----------------------------------------

def _contar_automatizaciones() -> int:
    """Cuenta las automatizaciones implementadas (con al menos un .py)."""
    excluidos = {"comun", "images", "logs", "__pycache__", "el_arquitecto_del_castillo"}
    total = 0
    if not RUTA_ECOSISTEMA.is_dir():
        return total
    for carpeta in RUTA_ECOSISTEMA.iterdir():
        if not carpeta.is_dir():
            continue
        if carpeta.name in excluidos or carpeta.name.startswith((".","__")):
            continue
        if any(carpeta.glob("*.py")):
            total += 1
    return total


def _contar_servicios() -> int:
    """Cuenta servicios/timers systemd del usuario relacionados con el ecosistema."""
    try:
        resultado = subprocess.run(
            ["systemctl", "--user", "list-units", "--type=service,timer",
             "--state=active", "--no-pager", "--no-legend"],
            capture_output=True, text=True, timeout=5,
        )
        nombres_auto = set()
        if RUTA_ECOSISTEMA.is_dir():
            excluidos = {"comun", "images", "logs", "__pycache__", "el_arquitecto_del_castillo"}
            for carpeta in RUTA_ECOSISTEMA.iterdir():
                if carpeta.is_dir() and carpeta.name not in excluidos:
                    if any(carpeta.glob("*.py")):
                        nombres_auto.add(carpeta.name)

        total = 0
        for linea in resultado.stdout.splitlines():
            for nombre in nombres_auto:
                if nombre in linea:
                    total += 1
                    break
        return total
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return 0


# -- Splash screen ---------------------------------------------------------------

def generar_splash() -> str:
    """Genera la pantalla de bienvenida con box drawing Unicode."""
    n_auto = _contar_automatizaciones()
    n_srv = _contar_servicios()

    r = C["reset"]
    cyan = C["cyan"]
    amarillo = C["amarillo"]
    dim = C["dim"]
    bold = C["bold"]

    # Contenido interior (sin colores para calcular ancho)
    lineas_contenido = [
        "",
        f"     {bold}{amarillo}EL ARQUITECTO DEL CASTILLO{r}",
        "",
        f"     {dim}Tu asistente para gestionar{r}",
        f"     {dim}el ecosistema de automatizaciones{r}",
        "",
        f"     {dim}{n_auto} automatizaciones activas{r}",
        f"     {dim}{n_srv} servicios corriendo{r}",
        "",
        f"     {dim}Escribe lo que necesites o 'salir'{r}",
        "",
    ]

    ancho = 51  # ancho interior fijo

    resultado = []
    resultado.append(f"  {cyan}┌{'─' * ancho}┐{r}")
    for linea in lineas_contenido:
        resultado.append(f"  {cyan}│{r} {linea.ljust(ancho + _len_ansi_extra(linea) - 1)}{cyan}│{r}")
    resultado.append(f"  {cyan}└{'─' * ancho}┘{r}")

    return "\n".join(resultado)


def _len_ansi_extra(texto: str) -> int:
    """Calcula los bytes extra de las secuencias ANSI en un texto."""
    sin_ansi = re.sub(r"\033\[[0-9;]*m", "", texto)
    return len(texto) - len(sin_ansi)


# -- Matching de intenciones -----------------------------------------------------

def match_por_keyword(entrada: str) -> str | None:
    """Busca match entre la entrada del usuario y las keywords de los comandos.

    Devuelve el nombre del comando (clave de COMANDOS) o None.
    """
    entrada_lower = entrada.lower().strip()
    if not entrada_lower:
        return None

    # Puntuacion por comando: cuantas keywords coinciden
    mejor_cmd = None
    mejor_score = 0

    palabras_entrada = set(entrada_lower.split())
    for nombre, info in COMANDOS.items():
        score = 0
        if nombre in palabras_entrada:
            score += 5
        for kw in info["keywords"]:
            if kw in palabras_entrada:
                score += 2
            elif kw in entrada_lower:
                score += 1
        if score > mejor_score:
            mejor_score = score
            mejor_cmd = nombre

    return mejor_cmd if mejor_score >= 2 else None


def match_por_url(entrada: str) -> str | None:
    """Detecta URLs de YouTube/SoundCloud y devuelve el comando yt con la URL."""
    m = _RE_URL_MEDIA.search(entrada)
    if m:
        return f"yt {m.group(1)}"
    return None


def construir_comando(nombre_cmd: str, entrada: str) -> str:
    """Construye el comando final combinando el cmd base con argumentos del usuario."""
    info = COMANDOS.get(nombre_cmd)
    if info is None:
        return nombre_cmd

    cmd_base = info["cmd"]

    if " " in cmd_base:
        return cmd_base

    palabras = entrada.split()

    palabras_ruido = {
        "quiero", "necesito", "dame", "muestrame", "muestra", "ver", "hay",
        "como", "cómo", "que", "qué", "el", "la", "los", "las", "un", "una",
        "de", "del", "por", "favor", "me", "puedes", "podrias", "ejecuta",
        "lanza", "abre", "haz", "pon", "a", "en", "con", "tipo", "errores",
        "archivo", "archivos", "carpeta", "sistema", "cancion", "canción",
        "cuéntame", "cuentame", "dime", "están", "estan", "son", "es",
        "tiene", "tienen", "puede", "pueden", "hay", "se", "y", "o", "si",
        "no", "pero", "también", "tambien", "al", "lo", "le", "busca",
        "encuentra", "abrelo", "ábrelo", "mira", "activa", "activar",
        "inicia", "iniciar", "cierra", "cerrar", "modo", "reinicia",
        "reiniciar", "analisis", "análisis", "descarga", "descargar",
        "instala", "instalar", "visual", "code", "studio", "programa",
        "aplicacion", "aplicación", "app", "pantalla", "estado",
        "hazme", "dime", "muestrame", "dame", "como", "cómo",
    }

    todas_keywords = set()
    for cmd_info in COMANDOS.values():
        todas_keywords.update(cmd_info["keywords"])
    # Anadir los nombres de los propios comandos
    todas_keywords.update(COMANDOS.keys())

    import re as _re
    quoted = _re.findall(r'"([^"]+)"', entrada)
    if quoted:
        return f"{cmd_base} {' '.join(quoted)}"

    pass_args = set(info.get("pass_args", []))

    todas_keywords = set()
    for cmd_info in COMANDOS.values():
        todas_keywords.update(cmd_info["keywords"])
    todas_keywords.update(COMANDOS.keys())

    args = []
    for palabra in palabras:
        p = palabra.strip(".,!?;:\"'")
        p_lower = p.lower()
        if p_lower in pass_args:
            args.append(p_lower)
        elif p.startswith("http") or p.startswith("/") or p.startswith("~/") or p.startswith("--"):
            args.append(p)
        elif p_lower not in palabras_ruido and p_lower not in todas_keywords and len(p) > 2:
            if not any(kw in p_lower for kw in todas_keywords):
                args.append(p)

    if args:
        return f"{cmd_base} {' '.join(args)}"

    return cmd_base




# -- Texto de ayuda ---------------------------------------------------------------

def _generar_comando_fs(entrada: str, nombre_cmd: str, config: dict) -> str | None:
    """[LEGACY INERTE] Generador de comandos shell por LLM, ELIMINADO.

    En la arquitectura actual ningun LLM genera comandos shell
    ejecutables. La firma se conserva solo porque las pruebas P0
    verifican que sigue inerte; nunca es alcanzable desde `arqui`.
    Siempre devuelve None.
    """
    logger.warning(
        "_generar_comando_fs: ruta legacy eliminada; no se genera shell"
    )
    return None


def generar_ayuda() -> str:
    """Genera el texto de ayuda con todos los comandos disponibles."""
    r = C["reset"]
    bold = C["bold"]
    dim = C["dim"]
    cyan = C["cyan"]
    amarillo = C["amarillo"]

    lineas = [
        f"\n  {bold}{cyan}Comandos disponibles{r}\n",
        f"  {dim}{'=' * 45}{r}\n",
    ]

    for nombre, info in COMANDOS.items():
        nombre_pad = nombre.ljust(14)
        lineas.append(f"  {amarillo}{nombre_pad}{r} {dim}{info['desc']}{r}")

    lineas.append(f"\n  {dim}{'=' * 45}{r}")
    lineas.append(f"  {bold}Comandos del REPL{r}\n")
    lineas.append(f"  {amarillo}{'ayuda':14}{r} {dim}Mostrar esta ayuda{r}")
    lineas.append(f"  {amarillo}{'limpiar':14}{r} {dim}Limpiar pantalla{r}")
    lineas.append(f"  {amarillo}{'salir':14}{r} {dim}Salir del Arquitecto{r}")
    lineas.append("")

    return "\n".join(lineas)


# -- Ejecucion de comandos -------------------------------------------------------

def ejecutar_comando(cmd: str):
    """[NEUTRALIZADO — Fase P0] Ejecutor del bucle legacy, DESACTIVADO.

    Antes ejecutaba el comando por shell directo sobre cadenas crudas o
    generadas por un LLM. Esa primitiva queda ELIMINADA: el Castillo no
    ejecuta shell libre. La firma se conserva solo porque las pruebas P0
    verifican que sigue inerte; NUNCA lanza ningun proceso.
    """
    logger.warning(
        "ejecutar_comando: ejecucion shell legacy desactivada (P0); "
        "comando ignorado: %s", cmd,
    )
    print(
        f"\n  {C['rojo']}Modo legacy desactivado: no se ejecuta shell libre."
        f"{C['reset']}\n"
    )


# -- REPL principal ---------------------------------------------------------------

def repl():
    """[LEGACY INERTE] Antiguo bucle por keywords + Qwen + shell.

    Su cuerpo fue ELIMINADO en P2. Ya no es alcanzable desde `main()` (el
    unico camino es el gobernado por el cerebro, `repl_cerebro`). La firma se
    conserva solo porque las pruebas P0 verifican que sigue inerte: si se
    invoca directamente, no hace nada salvo informar.
    """
    logger.warning("repl(): bucle legacy desactivado (P0); no se ejecuta")
    print(
        f"\n  {C['rojo']}El modo legacy esta desactivado.{C['reset']} "
        f"El Arquitecto solo opera por el camino gobernado.\n"
    )
    return


# -- Main / CLI ----------------------------------------------------------------

def _quiere_dry_run() -> bool:
    return "--dry-run" in sys.argv[1:]


# Mensaje canonico del fallo seguro: cuando el cerebro (OpenCode) no esta
# disponible, el Arquitecto NO ejecuta nada fuera del camino gobernado.
# P0: jamas se cae al bucle legacy (Qwen + shell directo).
_MSG_FALLO_SEGURO = (
    "OpenCode no está disponible; no se ejecutará ninguna acción fuera del "
    "camino gobernado."
)


def _registrar_fallo_seguro(motivo: str) -> None:
    """Deja traza estructurada del fallo seguro de arranque (best-effort).

    Usa la infraestructura de trazas del Arquitecto si esta disponible; si
    el paquete `arquitecto` no se puede importar, el logger ya ha dejado
    constancia. Nunca lanza.
    """
    try:
        from arquitecto import trazas  # import diferido: puede no estar disponible
        trazas.registrar_turno(
            peticion_usuario="<arranque>",
            decision="rechazar_peligro",
            valida=False,
            turno_id="arranque",
            motivo_invalidez=motivo,
        )
    except Exception as exc:  # noqa: BLE001 - la traza es best-effort
        logger.debug("No se pudo registrar traza del fallo seguro: %s", exc)


def _fallo_seguro(motivo: str) -> None:
    """Muestra el fallo seguro y deja constancia. NO ejecuta nada."""
    logger.error("Fallo seguro de arranque: %s", motivo)
    _registrar_fallo_seguro(motivo)
    print(f"\n  {C['rojo']}{_MSG_FALLO_SEGURO}{C['reset']}\n", file=sys.stderr)


def main():
    """Punto de entrada principal.

    Arranca UNICAMENTE el camino gobernado por el cerebro (OpenCode):
    `repl_cerebro` -> validador -> seguridad -> ejecutor (shell=False) ->
    trazas + confirmacion humana.

    Si OpenCode no esta disponible (o no se puede cargar el paquete
    `arquitecto`), el Arquitecto degrada a un FALLO SEGURO: informa y no
    ejecuta ninguna accion. NUNCA cae al bucle legacy (Qwen + shell directo).
    """
    logger.info("Iniciando El Arquitecto del Castillo (camino gobernado)")
    try:
        try:
            from arquitecto.repl import repl_cerebro
        except ImportError as e:
            logger.critical("No se pudo cargar el camino gobernado: %s", e)
            _fallo_seguro(f"no se pudo cargar el paquete arquitecto: {e}")
            return
        if repl_cerebro(dry_run=_quiere_dry_run()):
            return  # Sesion completada por el camino gobernado.
        # repl_cerebro devolvio False: OpenCode no disponible -> fallo seguro.
        _fallo_seguro("el cerebro OpenCode no esta disponible")
    except Exception as e:
        logger.critical("Error fatal en el arranque: %s", e)
        print(f"\n  {C['rojo']}Error fatal: {e}{C['reset']}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
