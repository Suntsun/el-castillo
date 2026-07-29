#!/usr/bin/env python3
"""
Oráculo de Errores
Explica stacktraces y mensajes de error con pattern matching y base de conocimiento.
Con fallback a LLM local (Ollama) cuando no hay patrón conocido.
Parte del ecosistema: herramientas bajo demanda.
"""

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from comun import notificar, configurar_logger, cargar_config, consultar_llm, llm_disponible

RUTA_AUTO = Path(__file__).resolve().parent
logger = configurar_logger("oraculo_errores")

# ── Colores ANSI ─────────────────────────────────────────────────

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
MAGENTA = "\033[35m"


def _color(texto: str, color: str) -> str:
    """Envuelve texto con código de color ANSI."""
    return f"{color}{texto}{RESET}"


# ── Base de conocimiento ─────────────────────────────────────────

# Cada entrada: (patrón regex, causa, fix)
# Los patrones se evalúan en orden; el primero que coincida gana.

PATRONES_PYTHON: list[tuple[str, str, str]] = [
    (
        r"ModuleNotFoundError:\s*No module named ['\"]?([^'\"]+)['\"]?",
        "El módulo '{0}' no está instalado en este entorno.",
        "pip install {0}  (o verifica que el virtualenv correcto esté activo)",
    ),
    (
        r"ImportError:\s*cannot import name '([^']+)' from '([^']+)'",
        "No se puede importar '{0}' desde '{1}'. El nombre no existe en ese módulo.",
        "Verifica el nombre exacto con: python3 -c \"import {1}; print(dir({1}))\"",
    ),
    (
        r"ImportError",
        "No se puede importar el módulo o nombre solicitado.",
        "Revisa el nombre del módulo e instala la dependencia si falta.",
    ),
    (
        r"FileNotFoundError:.*'([^']+)'",
        "El archivo o ruta '{0}' no existe.",
        "Verifica la ruta con: ls -la {0}",
    ),
    (
        r"FileNotFoundError",
        "Archivo o ruta no encontrada.",
        "Verifica que la ruta existe y está bien escrita.",
    ),
    (
        r"PermissionError:.*'([^']+)'",
        "Sin permisos para acceder a '{0}'.",
        "chmod +x {0}  o ejecuta con sudo si es necesario",
    ),
    (
        r"PermissionError",
        "Sin permisos para realizar la operación.",
        "Revisa los permisos del archivo/directorio con ls -la",
    ),
    (
        r"TypeError:.*NoneType",
        "Una variable es None cuando se esperaba otro tipo.\n"
        "          Esto pasa cuando una función devuelve None inesperadamente\n"
        "          o un dato no llegó como esperabas.",
        "Añade un guard: if variable is not None: ...  o revisa el flujo de datos",
    ),
    (
        r"TypeError",
        "Tipo de dato incorrecto para la operación.",
        "Revisa los tipos de los argumentos. Usa type() o isinstance() para depurar.",
    ),
    (
        r"KeyError:\s*'?([^'\"]+)'?",
        "La clave '{0}' no existe en el diccionario.",
        "Usa .get('{0}', valor_defecto) en vez de acceso directo con []",
    ),
    (
        r"IndentationError",
        "Error de indentación. Posible mezcla de tabs y espacios.",
        "Configura tu editor para usar solo espacios (4 por nivel). Revisa con: cat -A archivo.py",
    ),
    (
        r"SyntaxError",
        "Error de sintaxis en el código Python.",
        "Revisa paréntesis, comillas, dos puntos y comas. El error suele estar en la línea anterior.",
    ),
    (
        r"RecursionError",
        "Recursión infinita. La función se llama a sí misma sin condición de parada.",
        "Añade o revisa la condición base (base case) de la recursión.",
    ),
    (
        r"ConnectionRefusedError",
        "El servicio no está corriendo o el puerto es incorrecto.",
        "Verifica que el servicio esté activo: systemctl status <servicio>  o  ss -tlnp | grep <puerto>",
    ),
    (
        r"ValueError",
        "Valor incorrecto para la operación (tipo correcto, valor inválido).",
        "Revisa el rango o formato del valor pasado a la función.",
    ),
    (
        r"AttributeError:.*'([^']+)'.*'([^']+)'",
        "El objeto de tipo '{0}' no tiene el atributo '{1}'.",
        "Verifica el tipo del objeto con type() y consulta sus atributos con dir()",
    ),
    (
        r"ZeroDivisionError",
        "División entre cero.",
        "Añade un guard: if divisor != 0: ...  antes de la operación",
    ),
    (
        r"IndexError",
        "Índice fuera de rango en una lista o secuencia.",
        "Verifica la longitud con len() antes de acceder por índice.",
    ),
    (
        r"OSError.*Errno 28",
        "Disco lleno. No queda espacio en el dispositivo.",
        "Libera espacio: du -sh /* | sort -h  y limpia con pacman -Scc o journal --vacuum-size=100M",
    ),
    (
        r"UnicodeDecodeError",
        "Error de codificación al leer texto.",
        "Especifica el encoding: open(archivo, encoding='utf-8', errors='replace')",
    ),
]

PATRONES_NODE: list[tuple[str, str, str]] = [
    (
        r"ReferenceError:\s*(\w+) is not defined",
        "La variable '{0}' no está declarada en este scope.",
        "Declárala con let/const antes de usarla, o verifica el import.",
    ),
    (
        r"TypeError: Cannot read propert(?:y|ies) of undefined",
        "Accediendo a una propiedad de un valor undefined.\n"
        "          Esto pasa cuando un dato no llega como esperabas\n"
        "          (API que devuelve null, variable sin inicializar).",
        "Usa optional chaining: obj?.prop  o un guard: if (obj) { obj.prop }",
    ),
    (
        r"TypeError: Cannot read propert(?:y|ies) of null",
        "Accediendo a una propiedad de null.",
        "Verifica que el valor no sea null antes de acceder: val?.prop ?? fallback",
    ),
    (
        r"SyntaxError: Unexpected token",
        "JSON mal formado o error de sintaxis en el código.",
        "Si es JSON: valida con jq. Si es código: revisa llaves, paréntesis y comas.",
    ),
    (
        r"ENOENT",
        "Archivo o directorio no encontrado.",
        "Verifica que la ruta existe con: ls -la <ruta>",
    ),
    (
        r"EADDRINUSE.*?(\d+)",
        "El puerto {0} ya está en uso.",
        "Encuentra el proceso: lsof -i :{0}  o  ss -tlnp | grep {0}",
    ),
    (
        r"EADDRINUSE",
        "Puerto ya en uso por otro proceso.",
        "Encuentra el proceso: lsof -i :<puerto>  o cambia el puerto en la config.",
    ),
    (
        r"EACCES",
        "Permiso denegado para la operación.",
        "Revisa permisos del archivo/directorio o usa un puerto > 1024.",
    ),
    (
        r"ERR_MODULE_NOT_FOUND",
        "Módulo de Node no encontrado.",
        "Ejecuta: npm install  (o verifica que node_modules existe)",
    ),
    (
        r"ENOMEM",
        "Sin memoria suficiente para la operación.",
        "Aumenta el heap: NODE_OPTIONS='--max-old-space-size=4096'",
    ),
]

PATRONES_RUST: list[tuple[str, str, str]] = [
    (
        r"borrow of moved value",
        "El ownership del valor ya fue transferido (moved).",
        "Usa .clone() si necesitas una copia, o reestructura con referencias (&).",
    ),
    (
        r"cannot borrow .* as mutable",
        "Ya existe un borrow inmutable activo.",
        "Rust no permite mutabilidad mientras hay referencias inmutables. Reorganiza el scope.",
    ),
    (
        r"mismatched types.*expected `([^`]+)`.*found `([^`]+)`",
        "Tipos incompatibles: se esperaba '{0}' pero se encontró '{1}'.",
        "Convierte explícitamente con .into(), as, o From/Into trait.",
    ),
    (
        r"mismatched types",
        "Tipos incompatibles en la expresión.",
        "Revisa los tipos esperados vs los proporcionados. Usa el compilador como guía.",
    ),
    (
        r"lifetime .* does not live long enough",
        "La referencia no vive lo suficiente para el contexto donde se usa.",
        "Revisa los lifetimes. A veces la solución es clonar o reestructurar el ownership.",
    ),
]

PATRONES_JAVA: list[tuple[str, str, str]] = [
    (
        r"NullPointerException",
        "Se intentó usar una referencia null.",
        "Añade null checks o usa Optional<T> para manejar valores ausentes.",
    ),
    (
        r"ClassNotFoundException:\s*(\S+)",
        "La clase '{0}' no se encuentra en el classpath.",
        "Verifica las dependencias en pom.xml/build.gradle y reconstruye el proyecto.",
    ),
    (
        r"OutOfMemoryError",
        "La JVM se quedó sin memoria heap.",
        "Aumenta el heap: java -Xmx2g  o revisa memory leaks con jvisualvm.",
    ),
    (
        r"StackOverflowError",
        "Recursión infinita o stack demasiado profundo.",
        "Revisa la condición de parada de la recursión o convierte a iterativo.",
    ),
]

PATRONES_PACMAN: list[tuple[str, str, str]] = [
    (
        r"failed to commit transaction",
        "Conflicto de paquetes al intentar la transacción.",
        "Actualiza primero: sudo pacman -Syu  y reintenta.",
    ),
    (
        r"could not open file.*No such file",
        "Base de datos de pacman corrupta o desactualizada.",
        "Sincroniza: sudo pacman -Sy",
    ),
    (
        r"db\.lck",
        "Otra instancia de pacman está corriendo (o quedó un lock).",
        "Si no hay otro pacman: sudo rm /var/lib/pacman/db.lck",
    ),
    (
        r"target not found:\s*(\S+)",
        "El paquete '{0}' no existe en los repositorios oficiales.",
        "Búscalo en AUR: yay -Ss {0}  o  paru -Ss {0}",
    ),
    (
        r"conflicting files",
        "Un archivo del paquete ya existe en el sistema.",
        "Fuerza: sudo pacman -S --overwrite '*' <paquete>  (revisa qué sobreescribe antes)",
    ),
    (
        r"unable to lock database",
        "No se puede bloquear la base de datos de pacman.",
        "Espera a que termine otro pacman o elimina el lock: sudo rm /var/lib/pacman/db.lck",
    ),
    (
        r"error: failed to retrieve some files",
        "No se pudieron descargar algunos paquetes.",
        "Actualiza los mirrors: sudo reflector --latest 10 --sort rate --save /etc/pacman.d/mirrorlist",
    ),
]

PATRONES_SYSTEMD: list[tuple[str, str, str]] = [
    (
        r"Failed to start (.+)",
        "El servicio '{0}' no pudo arrancar.",
        "Revisa los logs: journalctl -u {0} -n 50 --no-pager",
    ),
    (
        r"code=exited,\s*status=(\d+)",
        "El proceso terminó con código de salida {0}.",
        "Revisa los logs del servicio: journalctl -xe  (el código indica el tipo de error)",
    ),
    (
        r"Main process exited",
        "El proceso principal del servicio murió.",
        "Revisa los logs: journalctl -xe  y verifica la configuración del servicio.",
    ),
    (
        r"Unit .+ entered failed state",
        "El servicio entró en estado fallido.",
        "Reinicia: systemctl restart <servicio>  y revisa: journalctl -u <servicio>",
    ),
]

PATRONES_GIT: list[tuple[str, str, str]] = [
    (
        r"fatal: not a git repository",
        "No estás dentro de un repositorio git.",
        "Inicializa uno: git init  o navega al directorio correcto.",
    ),
    (
        r"CONFLICT.*Merge conflict in (.+)",
        "Conflicto de merge en el archivo '{0}'.",
        "Edita {0}, resuelve los marcadores <<<<< ===== >>>>>, luego: git add {0}",
    ),
    (
        r"CONFLICT",
        "Conflicto de merge. Hay cambios incompatibles.",
        "Resuelve los conflictos manualmente, luego: git add . && git commit",
    ),
    (
        r"rejected.*non-fast-forward",
        "Tu rama está desactualizada respecto al remoto.",
        "Actualiza primero: git pull --rebase  y luego push de nuevo.",
    ),
    (
        r"fatal: remote origin already exists",
        "El remoto 'origin' ya está configurado.",
        "Cámbialo: git remote set-url origin <nueva-url>",
    ),
    (
        r"error: failed to push some refs",
        "No se pudieron subir los cambios al remoto.",
        "Actualiza: git pull --rebase origin <rama>  y reintenta el push.",
    ),
]

PATRONES_GENERAL: list[tuple[str, str, str]] = [
    (
        r"Permission denied",
        "Sin permisos para acceder al recurso.",
        "Revisa permisos con: ls -la <ruta>  o ejecuta con sudo si es necesario.",
    ),
    (
        r"No space left on device",
        "Disco lleno. No queda espacio disponible.",
        "Libera espacio: du -sh /* | sort -h  y  sudo pacman -Scc  o  journalctl --vacuum-size=100M",
    ),
    (
        r"Connection refused",
        "El servicio no responde en ese puerto.",
        "Verifica que el servicio esté activo: ss -tlnp | grep <puerto>",
    ),
    (
        r"Segmentation fault",
        "Acceso a memoria inválido (bug en el programa o librería).",
        "Si es tu código: usa valgrind o AddressSanitizer. Si es un paquete: reporta el bug.",
    ),
    (
        r"Killed$",
        "El sistema mató el proceso (probablemente Out Of Memory).",
        "Revisa: dmesg | grep -i oom  y reduce el consumo de memoria del proceso.",
    ),
    (
        r"command not found:\s*(\S+)",
        "El comando '{0}' no está instalado o no está en el PATH.",
        "Instálalo: pacman -F {0}  (busca qué paquete lo provee)",
    ),
    (
        r"Name or service not known",
        "No se puede resolver el nombre de host (DNS).",
        "Verifica la conexión: ping 8.8.8.8  y revisa /etc/resolv.conf",
    ),
    (
        r"Connection timed out",
        "La conexión agotó el tiempo de espera.",
        "Verifica la conectividad de red y que el host/puerto sean correctos.",
    ),
]

# Mapa lenguaje → lista de patrones
PATRONES_POR_LENGUAJE: dict[str, list[tuple[str, str, str]]] = {
    "Python": PATRONES_PYTHON,
    "JavaScript/Node": PATRONES_NODE,
    "Rust": PATRONES_RUST,
    "Java": PATRONES_JAVA,
    "Arch/pacman": PATRONES_PACMAN,
    "systemd": PATRONES_SYSTEMD,
    "Git": PATRONES_GIT,
    "General": PATRONES_GENERAL,
}


# ── Detección de lenguaje ────────────────────────────────────────


def detectar_lenguaje(texto: str) -> str:
    """
    Detecta el lenguaje/framework por patrones del stacktrace.

    Args:
        texto: Texto del error o stacktrace completo.

    Returns:
        Nombre del lenguaje detectado o "General" como fallback.
    """
    # Orden importa: patrones más específicos primero
    if re.search(r'Traceback \(most recent call last\)|File ".*\.py"', texto):
        return "Python"
    if re.search(r"panicked at|thread '(main|.*)'.*panicked", texto):
        return "Rust"
    if re.search(r"Exception in thread|\.java:\d+|at \w+\.\w+\([\w.]+\.java:", texto):
        return "Java"
    if re.search(r"at Object\.|node_modules|at Module\.|\.js:\d+:\d+", texto):
        return "JavaScript/Node"
    if re.search(r"pacman|yay |paru ", texto):
        return "Arch/pacman"
    if re.search(r"systemctl|systemd|journalctl|\.service", texto):
        return "systemd"
    if re.search(r"fatal:.*git|git .*(error|fatal)|CONFLICT.*Merge", texto):
        return "Git"

    # Detección por tipos de error sin contexto claro
    if re.search(r"TypeError:|ReferenceError:|SyntaxError: Unexpected token", texto):
        return "JavaScript/Node"

    return "General"


# ── Extracción de información ────────────────────────────────────


def extraer_info(texto: str, lenguaje: str) -> dict[str, str | None]:
    """
    Extrae información clave del stacktrace según el lenguaje.

    Args:
        texto: Texto del error o stacktrace completo.
        lenguaje: Lenguaje detectado.

    Returns:
        Dict con claves: error_tipo, mensaje, archivo, linea.
    """
    info: dict[str, str | None] = {
        "error_tipo": None,
        "mensaje": None,
        "archivo": None,
        "linea": None,
    }

    if lenguaje == "Python":
        # Tipo de error: última línea que comienza con un nombre de excepción
        match = re.search(r"^(\w+Error|\w+Exception|\w+Warning):\s*(.+)$", texto, re.MULTILINE)
        if match:
            info["error_tipo"] = match.group(1)
            info["mensaje"] = match.group(2).strip()
        # Archivo y línea: última ocurrencia de File "...", line N
        matches = re.findall(r'File "([^"]+)", line (\d+)', texto)
        if matches:
            info["archivo"] = matches[-1][0]
            info["linea"] = matches[-1][1]

    elif lenguaje == "JavaScript/Node":
        # TypeError: mensaje
        match = re.search(r"^(\w+Error):\s*(.+)$", texto, re.MULTILINE)
        if match:
            info["error_tipo"] = match.group(1)
            info["mensaje"] = match.group(2).strip()
        # at funcion (archivo:linea:col) o archivo:linea:col
        match_file = re.search(r"(?:at .+?\(|at )(.+?\.(?:js|ts|mjs|cjs)):(\d+):\d+", texto)
        if not match_file:
            match_file = re.search(r"(.+?\.(?:js|ts|mjs|cjs)):(\d+):\d+", texto)
        if match_file:
            info["archivo"] = match_file.group(1).strip()
            info["linea"] = match_file.group(2)

    elif lenguaje == "Rust":
        match = re.search(r"thread '.*' panicked at '(.+)',\s*(.+):(\d+):\d+", texto)
        if not match:
            match = re.search(r"thread '.*' panicked at '(.+)',\s*(.+):(\d+)", texto)
        if match:
            info["error_tipo"] = "panic"
            info["mensaje"] = match.group(1)
            info["archivo"] = match.group(2)
            info["linea"] = match.group(3)
        else:
            # Error del compilador
            match = re.search(r"error\[E\d+\]:\s*(.+)", texto)
            if match:
                info["error_tipo"] = "error de compilación"
                info["mensaje"] = match.group(1)
            match_file = re.search(r"--> (.+):(\d+):\d+", texto)
            if match_file:
                info["archivo"] = match_file.group(1)
                info["linea"] = match_file.group(2)

    elif lenguaje == "Java":
        match = re.search(r"^(?:Exception in thread .+\s)?(\S+(?:Error|Exception))(?::\s*(.*))?$", texto, re.MULTILINE)
        if match:
            info["error_tipo"] = match.group(1)
            info["mensaje"] = match.group(2).strip() if match.group(2) else None
        match_file = re.search(r"at .+\((\w+\.java):(\d+)\)", texto)
        if match_file:
            info["archivo"] = match_file.group(1)
            info["linea"] = match_file.group(2)

    elif lenguaje == "Arch/pacman":
        match = re.search(r"error:\s*(.+)", texto, re.IGNORECASE)
        if match:
            info["error_tipo"] = "pacman error"
            info["mensaje"] = match.group(1).strip()

    elif lenguaje == "systemd":
        match = re.search(r"(?:Failed to start|code=exited|Main process exited).*", texto)
        if match:
            info["error_tipo"] = "servicio fallido"
            info["mensaje"] = match.group(0).strip()

    elif lenguaje == "Git":
        match = re.search(r"(?:fatal|error):\s*(.+)", texto, re.IGNORECASE)
        if match:
            info["error_tipo"] = "git error"
            info["mensaje"] = match.group(1).strip()

    else:
        # General: intenta extraer cualquier patrón de error
        match = re.search(r"^(?:error|fatal|Error):\s*(.+)$", texto, re.MULTILINE | re.IGNORECASE)
        if match:
            info["error_tipo"] = "error"
            info["mensaje"] = match.group(1).strip()

    # Si no se encontró tipo, usa la primera línea significativa
    if not info["error_tipo"]:
        for linea in texto.strip().splitlines():
            linea = linea.strip()
            if linea and not linea.startswith(("#", "//")):
                info["error_tipo"] = linea[:80]
                break

    return info


# ── Búsqueda en base de conocimiento ────────────────────────────


def buscar_explicacion(texto: str, lenguaje: str) -> tuple[str, str] | None:
    """
    Busca en la base de conocimiento un patrón que coincida.

    Args:
        texto: Texto del error o stacktrace.
        lenguaje: Lenguaje detectado.

    Returns:
        Tupla (causa, fix) con los grupos del regex sustituidos,
        o None si no se encontró coincidencia.
    """
    # Buscar primero en patrones del lenguaje específico, luego en general
    listas = []
    if lenguaje in PATRONES_POR_LENGUAJE and lenguaje != "General":
        listas.append(PATRONES_POR_LENGUAJE[lenguaje])
    listas.append(PATRONES_GENERAL)

    for lista in listas:
        for patron, causa_tpl, fix_tpl in lista:
            match = re.search(patron, texto, re.MULTILINE | re.IGNORECASE)
            if match:
                grupos = match.groups()
                causa = causa_tpl
                fix = fix_tpl
                for i, grupo in enumerate(grupos):
                    causa = causa.replace(f"{{{i}}}", grupo)
                    fix = fix.replace(f"{{{i}}}", grupo)
                return (causa, fix)

    return None


# ── Fallback LLM ────────────────────────────────────────────────


_LLM_SISTEMA = "Eres un asistente técnico. Responde en español, máximo 3 frases. Sé directo."
_LLM_TIMEOUT = 15


def _fallback_llm(texto: str) -> tuple[str, str] | None:
    """
    Consulta al LLM local cuando no hay patrón conocido.

    Args:
        texto: Texto del error o stacktrace.

    Returns:
        Tupla (causa, fix) desde el LLM, o None si no está disponible o falla.
    """
    if not llm_disponible():
        return None

    prompt = f"Explica este error y cómo solucionarlo:\n\n{texto}"
    try:
        respuesta = consultar_llm(
            prompt,
            timeout=_LLM_TIMEOUT,
            sistema=_LLM_SISTEMA,
        )
    except Exception:
        logger.debug("Excepción inesperada al consultar LLM", exc_info=True)
        return None

    if not respuesta:
        return None

    # Intentar separar explicación de sugerencia de fix
    # El LLM puede devolver texto libre; partimos en causa y fix heurísticamente
    partes = respuesta.split("\n", 1)
    causa = partes[0].strip()
    fix = partes[1].strip() if len(partes) > 1 else "Revisa el contexto completo del error."

    return (causa, fix)


# ── Mostrar resultado ────────────────────────────────────────────


def formatear_resultado(info: dict[str, str | None], lenguaje: str,
                        causa: str | None, fix: str | None,
                        desde_llm: bool = False) -> str:
    """
    Formatea el resultado del análisis con colores ANSI.

    Args:
        info: Diccionario con datos extraídos del error.
        lenguaje: Lenguaje detectado.
        causa: Explicación de la causa (o None).
        fix: Sugerencia de solución (o None).
        desde_llm: Si True, las etiquetas CAUSA/FIX muestran sufijo [LLM].

    Returns:
        String formateado listo para imprimir.
    """
    lineas = [""]

    # ERROR
    error_texto = info.get("error_tipo") or "Error desconocido"
    if info.get("mensaje"):
        error_texto += f": {info['mensaje']}"
    lineas.append(f"  {_color('ERROR:', RED + BOLD)}  {_color(error_texto, RED)}")
    lineas.append("")

    # LENGUAJE
    lineas.append(f"  {_color('LENGUAJE:', CYAN + BOLD)}  {lenguaje}")

    # ARCHIVO
    if info.get("archivo"):
        archivo_texto = info["archivo"]
        if info.get("linea"):
            archivo_texto += f" (linea {info['linea']})"
        lineas.append(f"  {_color('ARCHIVO:', CYAN + BOLD)}   {archivo_texto}")

    lineas.append("")

    sufijo_llm = " [LLM]" if desde_llm else ""

    # CAUSA
    if causa:
        etiqueta_causa = f"CAUSA{sufijo_llm}:"
        # Indenta líneas adicionales de la causa
        lineas_causa = causa.split("\n")
        lineas.append(f"  {_color(etiqueta_causa, YELLOW + BOLD)}  {_color(lineas_causa[0], YELLOW)}")
        for lc in lineas_causa[1:]:
            lineas.append(f"  {_color(lc, YELLOW)}")
    else:
        lineas.append(f"  {_color('CAUSA:', YELLOW + BOLD)}  {_color('No se encontró un patrón conocido para este error.', YELLOW)}")

    lineas.append("")

    # FIX
    if fix:
        etiqueta_fix = f"FIX{sufijo_llm}:"
        lineas_fix = fix.split("\n")
        lineas.append(f"  {_color(etiqueta_fix, GREEN + BOLD)}    {_color(lineas_fix[0], GREEN)}")
        for lf in lineas_fix[1:]:
            lineas.append(f"  {_color(lf, GREEN)}")
    else:
        lineas.append(f"  {_color('FIX:', GREEN + BOLD)}    {_color('Busca el mensaje de error exacto en la documentación del proyecto.', GREEN)}")

    lineas.append("")
    return "\n".join(lineas)


def mostrar_error(mensaje: str):
    """Muestra un mensaje de error con formato."""
    print(f"\n  {_color('Error:', RED + BOLD)} {mensaje}\n", file=sys.stderr)


# ── Entrada de texto ─────────────────────────────────────────────


def leer_portapapeles() -> str | None:
    """Lee el contenido del portapapeles con wl-paste."""
    if not shutil.which("wl-paste"):
        logger.error("wl-paste no encontrado (instalar wl-clipboard)")
        return None
    try:
        resultado = subprocess.run(
            ["wl-paste", "--no-newline"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if resultado.returncode != 0:
            logger.error(f"wl-paste falló: {resultado.stderr.strip()}")
            return None
        contenido = resultado.stdout.strip()
        return contenido if contenido else None
    except subprocess.TimeoutExpired:
        logger.error("Timeout al leer portapapeles")
        return None


def leer_archivo(ruta: str) -> str | None:
    """Lee el contenido de un archivo."""
    try:
        path = Path(ruta)
        if not path.exists():
            logger.error(f"Archivo no encontrado: {ruta}")
            return None
        contenido = path.read_text(encoding="utf-8", errors="replace").strip()
        return contenido if contenido else None
    except OSError as e:
        logger.error(f"Error al leer {ruta}: {e}")
        return None


def leer_stdin() -> str | None:
    """Lee texto desde stdin si hay datos disponibles."""
    if sys.stdin.isatty():
        return None
    try:
        contenido = sys.stdin.read().strip()
        return contenido if contenido else None
    except Exception as e:
        logger.error(f"Error al leer stdin: {e}")
        return None


# ── Análisis principal ───────────────────────────────────────────


def analizar(texto: str) -> tuple[dict[str, str | None], str, str | None, str | None, bool]:
    """
    Analiza un texto de error/stacktrace completo.

    Primero busca en la base de conocimiento por pattern matching.
    Si no encuentra coincidencia, intenta un fallback al LLM local.

    Args:
        texto: El stacktrace o mensaje de error.

    Returns:
        Tupla (info, lenguaje, causa, fix, desde_llm).
    """
    lenguaje = detectar_lenguaje(texto)
    info = extraer_info(texto, lenguaje)
    resultado = buscar_explicacion(texto, lenguaje)
    desde_llm = False

    if resultado:
        causa, fix = resultado
    else:
        # Fallback a LLM si no hay patrón conocido
        logger.info("Sin patrón conocido, intentando fallback LLM")
        resultado_llm = _fallback_llm(texto)
        if resultado_llm:
            causa, fix = resultado_llm
            desde_llm = True
            logger.info("Respuesta obtenida del LLM")
        else:
            causa, fix = None, None
            logger.info("LLM no disponible o sin respuesta")

    return info, lenguaje, causa, fix, desde_llm


# ── CLI ──────────────────────────────────────────────────────────


def construir_parser() -> argparse.ArgumentParser:
    """Construye el parser de argumentos."""
    parser = argparse.ArgumentParser(
        prog="explicar",
        description="Explica errores y stacktraces al instante.",
        epilog=(
            "Ejemplos:\n"
            "  explicar                     Lee del portapapeles y analiza\n"
            "  explicar error.log           Analiza un fichero\n"
            "  cat trace.txt | explicar     Desde stdin/pipe\n"
            "  explicar --clipboard         Explicitamente desde portapapeles\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "archivo",
        nargs="?",
        help="Archivo con el error/stacktrace a analizar",
    )
    parser.add_argument(
        "--clipboard", "-c",
        action="store_true",
        help="Leer del portapapeles (comportamiento por defecto sin argumentos)",
    )
    parser.add_argument(
        "--silent", "-s",
        action="store_true",
        help="Sin notificacion de escritorio",
    )
    return parser


def main():
    parser = construir_parser()
    args = parser.parse_args()

    # ── Cargar configuracion ─────────────────────────────────────
    config = cargar_config(RUTA_AUTO)
    cfg_notif = config.get("notificacion", {})

    # ── Obtener texto ────────────────────────────────────────────
    texto = None

    # 1. Desde stdin (pipe)
    texto = leer_stdin()

    # 2. Desde archivo
    if texto is None and args.archivo:
        texto = leer_archivo(args.archivo)
        if texto is None:
            mostrar_error(f"No se pudo leer el archivo: {args.archivo}")
            sys.exit(1)

    # 3. Desde portapapeles (explícito o por defecto)
    if texto is None:
        texto = leer_portapapeles()
        if texto is None:
            mostrar_error("No hay texto para analizar.\n"
                          "         Copia un error al portapapeles, pasa un archivo, o usa pipe.")
            sys.exit(1)

    logger.info(f"Analizando texto ({len(texto)} chars)")

    # ── Analizar ─────────────────────────────────────────────────
    info, lenguaje, causa, fix, desde_llm = analizar(texto)

    logger.info(f"Lenguaje detectado: {lenguaje}")
    if info.get("error_tipo"):
        logger.info(f"Error: {info['error_tipo']}")

    # ── Mostrar resultado ────────────────────────────────────────
    salida = formatear_resultado(info, lenguaje, causa, fix, desde_llm=desde_llm)
    print(salida)

    # ── Notificacion ─────────────────────────────────────────────
    if not args.silent:
        resumen = info.get("error_tipo") or "Error analizado"
        notificar(
            "oraculo_errores",
            f"{resumen} - revisa la terminal",
            cfg_notif.get("severidad", "info"),
            cfg_notif.get("duracion", 3000),
        )

    logger.info("Analisis completado")


if __name__ == "__main__":
    main()
