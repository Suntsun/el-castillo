#!/usr/bin/env python3
"""
Guardián de las Sombras — Defensor del Sistema
Escanea commits, historial git y el sistema en busca de secretos,
credenciales expuestas y amenazas de seguridad.
Parte del ecosistema: seguridad.
"""

import argparse
import os
import re
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from comun import notificar, configurar_logger, cargar_config

RUTA_AUTO = Path(__file__).resolve().parent
RUTA_HOOK = RUTA_AUTO / "hook_pre_commit.sh"
CONSEJERO = "guardian_sombras"

logger = configurar_logger("guardian_sombras")


# -- Patrones de deteccion ----------------------------------------------------

PATRONES: dict[str, tuple[str, re.Pattern]] = {}


def _compilar_patrones(config: dict) -> dict[str, tuple[str, re.Pattern]]:
    """Compila los patrones regex segun la configuracion."""
    cfg_patrones = config.get("patrones", {})

    definiciones: list[tuple[str, str, str, int]] = [
        ("aws_keys", "Clave AWS", r"AKIA[0-9A-Z]{16}", 0),
        (
            "tokens_genericos",
            "Token/password generico",
            r"(token|secret|password|passwd|api_key|apikey|secret_key)\s*[=:]\s*['\"]?\S{8,}",
            re.IGNORECASE,
        ),
        ("claves_privadas", "Clave privada", r"-----BEGIN.*PRIVATE KEY-----", 0),
        (
            "jwt",
            "JSON Web Token",
            r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
            0,
        ),
        (
            "credenciales_url",
            "Credenciales en URL",
            r"https?://[^:@\s]+:[^:@\s]+@",
            0,
        ),
        (
            "github_tokens",
            "GitHub token",
            r"gh[pousr]_[A-Za-z0-9_]{36,}",
            0,
        ),
        (
            "slack_tokens",
            "Slack token",
            r"xox[baprs]-[0-9a-zA-Z\-]{10,}",
            0,
        ),
        (
            "discord_webhooks",
            "Discord webhook",
            r"https://discord(?:app)?\.com/api/webhooks/\d+/[\w-]+",
            0,
        ),
        (
            "stripe_keys",
            "Stripe key",
            r"[sr]k_(live|test)_[0-9a-zA-Z]{20,}",
            0,
        ),
        (
            "db_connection_strings",
            "Connection string de BD",
            r"(postgres|mysql|mongodb|redis)://[^\s'\"]{10,}",
            re.IGNORECASE,
        ),
        (
            "google_api_keys",
            "Google API key",
            r"AIza[0-9A-Za-z\-_]{35}",
            0,
        ),
        (
            "telegram_tokens",
            "Telegram bot token",
            r"\d{8,10}:[A-Za-z0-9_-]{35}",
            0,
        ),
    ]

    patrones: dict[str, tuple[str, re.Pattern]] = {}
    for clave, descripcion, regex, flags in definiciones:
        if cfg_patrones.get(clave, True):
            patrones[clave] = (descripcion, re.compile(regex, flags))

    return patrones


# -- Modelo de hallazgos ------------------------------------------------------

@dataclass
class Hallazgo:
    """Un secreto detectado en un archivo."""
    archivo: str
    linea: int | None
    texto_coincidente: str
    tipo: str


@dataclass
class ResultadoEscaneo:
    """Resultado agregado del escaneo."""
    hallazgos: list[Hallazgo] = field(default_factory=list)

    @property
    def tiene_secretos(self) -> bool:
        return len(self.hallazgos) > 0


# -- Whitelist -----------------------------------------------------------------

def cargar_whitelist(ruta_repo: Path) -> list[str]:
    """Carga la whitelist de falsos positivos desde .secretos-whitelist."""
    ruta_whitelist = ruta_repo / ".secretos-whitelist"
    if not ruta_whitelist.exists():
        return []

    try:
        lineas = ruta_whitelist.read_text(encoding="utf-8").splitlines()
        return [l.strip() for l in lineas if l.strip() and not l.strip().startswith("#")]
    except OSError:
        return []


def esta_en_whitelist(texto: str, whitelist: list[str]) -> bool:
    """Comprueba si el texto coincidente esta en la whitelist."""
    for entrada in whitelist:
        if entrada in texto:
            return True
    return False


# -- Escaneo de contenido -----------------------------------------------------

def escanear_contenido(
    contenido: str,
    archivo: str,
    patrones: dict[str, tuple[str, re.Pattern]],
    whitelist: list[str],
) -> list[Hallazgo]:
    """Escanea el contenido de un archivo buscando secretos."""
    hallazgos: list[Hallazgo] = []

    for num_linea, linea in enumerate(contenido.splitlines(), start=1):
        for _clave, (descripcion, patron) in patrones.items():
            for match in patron.finditer(linea):
                texto = match.group(0)
                if not esta_en_whitelist(texto, whitelist):
                    hallazgos.append(
                        Hallazgo(
                            archivo=archivo,
                            linea=num_linea,
                            texto_coincidente=texto,
                            tipo=descripcion,
                        )
                    )

    return hallazgos


# -- Obtener archivos de git ---------------------------------------------------

def obtener_raiz_repo() -> Path | None:
    """Obtiene la raiz del repositorio git actual."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return Path(result.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def obtener_archivos_staged() -> list[str]:
    """Obtiene la lista de archivos staged en git."""
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return [f.strip() for f in result.stdout.splitlines() if f.strip()]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return []


def obtener_archivos_working_tree() -> list[str]:
    """Obtiene todos los archivos tracked en el working tree."""
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return [f.strip() for f in result.stdout.splitlines() if f.strip()]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return []


def obtener_contenido_staged(archivo: str) -> str | None:
    """Obtiene el contenido staged de un archivo (no el del working tree).

    Lee en binario y decodifica con errors="replace" para no reventar ante
    ficheros binarios staged (ej. .png, .jpg): un binario produce texto basura
    que simplemente no casa con los patrones de secretos, en lugar de lanzar
    UnicodeDecodeError y abortar el escaneo entero.
    """
    try:
        result = subprocess.run(
            ["git", "show", f":{archivo}"],
            capture_output=True, timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.decode("utf-8", errors="replace")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


# -- Escaneo principal ---------------------------------------------------------

def escanear_staged(config: dict) -> ResultadoEscaneo:
    """Escanea los archivos staged en busca de secretos."""
    patrones = _compilar_patrones(config)
    cfg_patrones = config.get("patrones", {})
    detectar_env = cfg_patrones.get("archivos_env", True)

    raiz = obtener_raiz_repo()
    whitelist = cargar_whitelist(raiz) if raiz else []

    archivos = obtener_archivos_staged()
    resultado = ResultadoEscaneo()

    for archivo in archivos:
        # Deteccion de archivos .env
        nombre = Path(archivo).name
        if detectar_env and (nombre == ".env" or nombre.startswith(".env.")):
            resultado.hallazgos.append(
                Hallazgo(
                    archivo=archivo,
                    linea=None,
                    texto_coincidente="",
                    tipo="Archivo .env en el commit",
                )
            )
            continue

        contenido = obtener_contenido_staged(archivo)
        if contenido is None:
            continue

        resultado.hallazgos.extend(
            escanear_contenido(contenido, archivo, patrones, whitelist)
        )

    return resultado


def escanear_working_tree(config: dict) -> ResultadoEscaneo:
    """Escanea todo el working tree en busca de secretos."""
    patrones = _compilar_patrones(config)
    cfg_patrones = config.get("patrones", {})
    detectar_env = cfg_patrones.get("archivos_env", True)

    raiz = obtener_raiz_repo()
    if raiz is None:
        # Fallo explícito: --scan requiere un repo git
        print(f"{_ROJO}Error: no se encuentra un repositorio git en el directorio actual{_RESET}")
        print(f"{_GRIS}Ejecuta desde dentro de un repositorio git.{_RESET}")
        logger.error("--scan requiere repo git; no se encontró ninguno")
        sys.exit(1)

    whitelist = cargar_whitelist(raiz)
    archivos = obtener_archivos_working_tree()
    resultado = ResultadoEscaneo()

    for archivo in archivos:
        nombre = Path(archivo).name
        if detectar_env and (nombre == ".env" or nombre.startswith(".env.")):
            resultado.hallazgos.append(
                Hallazgo(
                    archivo=archivo,
                    linea=None,
                    texto_coincidente="",
                    tipo="Archivo .env en el commit",
                )
            )
            continue

        ruta_completa = raiz / archivo
        try:
            contenido = ruta_completa.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        resultado.hallazgos.extend(
            escanear_contenido(contenido, archivo, patrones, whitelist)
        )

    return resultado


# -- Escaneo de historial git --------------------------------------------------

def escanear_historial(config: dict, max_commits: int = 100) -> ResultadoEscaneo:
    """Escanea el historial de git buscando secretos en commits anteriores."""
    patrones = _compilar_patrones(config)
    raiz = obtener_raiz_repo()
    if raiz is None:
        # Fallo explícito: --historial requiere un repo git
        print(f"{_ROJO}Error: no se encuentra un repositorio git en el directorio actual{_RESET}")
        print(f"{_GRIS}Ejecuta desde dentro de un repositorio git.{_RESET}")
        logger.error("--historial requiere repo git; no se encontró ninguno")
        sys.exit(1)

    whitelist = cargar_whitelist(raiz)
    resultado = ResultadoEscaneo()

    try:
        log_result = subprocess.run(
            ["git", "log", f"--max-count={max_commits}", "--pretty=format:%H"],
            capture_output=True, text=True, timeout=30,
        )
        if log_result.returncode != 0:
            return resultado
        commits = [c.strip() for c in log_result.stdout.splitlines() if c.strip()]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return resultado

    vistos: set[str] = set()
    for commit_hash in commits:
        try:
            diff_result = subprocess.run(
                ["git", "diff-tree", "--no-commit-id", "-r", "-p", commit_hash],
                capture_output=True, text=True, timeout=30,
            )
            if diff_result.returncode != 0:
                continue
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue

        archivo_actual = "desconocido"
        for linea in diff_result.stdout.splitlines():
            if linea.startswith("+++ b/"):
                archivo_actual = linea[6:]
                continue
            if not linea.startswith("+") or linea.startswith("+++"):
                continue

            contenido_linea = linea[1:]
            for _clave, (descripcion, patron) in patrones.items():
                for match in patron.finditer(contenido_linea):
                    texto = match.group(0)
                    clave_unica = f"{archivo_actual}:{texto}"
                    if clave_unica not in vistos and not esta_en_whitelist(texto, whitelist):
                        vistos.add(clave_unica)
                        resultado.hallazgos.append(
                            Hallazgo(
                                archivo=f"{archivo_actual} (commit {commit_hash[:8]})",
                                linea=None,
                                texto_coincidente=texto,
                                tipo=descripcion,
                            )
                        )
    return resultado


# -- Escaneo de amenazas del sistema ------------------------------------------

@dataclass
class Amenaza:
    """Una amenaza detectada en el sistema."""
    categoria: str
    descripcion: str
    ruta: str
    severidad: str  # "aviso" o "error"


def escanear_amenazas() -> list[Amenaza]:
    """Escanea el sistema buscando amenazas de seguridad."""
    amenazas: list[Amenaza] = []
    amenazas.extend(_check_scripts_sospechosos())
    amenazas.extend(_check_permisos_peligrosos())
    amenazas.extend(_check_ssh_autorizado())
    amenazas.extend(_check_cron_sospechoso())
    amenazas.extend(_check_ejecutables_recientes())
    return amenazas


def _check_scripts_sospechosos() -> list[Amenaza]:
    """Busca scripts con patrones peligrosos (curl|bash, reverse shells, etc)."""
    amenazas: list[Amenaza] = []
    patrones_peligro = [
        (r"curl\s+.*\|\s*(?:ba)?sh", "curl piped a shell"),
        (r"wget\s+.*\|\s*(?:ba)?sh", "wget piped a shell"),
        (r"bash\s+-i\s+>& /dev/tcp/", "Reverse shell bash"),
        (r"nc\s+-[elp].*\s+-e\s+/bin/", "Reverse shell netcat"),
        (r"python.*socket.*connect.*subprocess", "Reverse shell python"),
        (r"eval\s*\(\s*base64", "Ejecucion de base64 ofuscado"),
        (r"base64\s+--decode\s*\|\s*(?:ba)?sh", "base64 decoded a shell"),
        (r"xmrig|cryptonight|stratum\+tcp://", "Posible cryptominer"),
    ]
    patrones_compilados = [(re.compile(p, re.IGNORECASE), desc) for p, desc in patrones_peligro]

    dirs_a_escanear = [
        Path.home() / ".local" / "bin",
        Path.home() / "Escritorio",
        Path.home() / "Descargas",
        Path("/tmp"),
    ]
    dirs_excluidos = {
        str(Path.home() / "Escritorio" / "automatizaciones"),
    }

    for directorio in dirs_a_escanear:
        if not directorio.exists():
            continue
        try:
            for ruta in directorio.rglob("*"):
                if any(str(ruta).startswith(exc) for exc in dirs_excluidos):
                    continue
                if not ruta.is_file() or ruta.stat().st_size > 1_000_000:
                    continue
                if ruta.suffix not in {"", ".sh", ".bash", ".py", ".pl", ".rb", ".js"}:
                    continue
                try:
                    contenido = ruta.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for patron, desc in patrones_compilados:
                    if patron.search(contenido):
                        amenazas.append(Amenaza(
                            categoria="Script sospechoso",
                            descripcion=desc,
                            ruta=str(ruta),
                            severidad="error",
                        ))
                        break
        except PermissionError:
            continue

    return amenazas


def _check_permisos_peligrosos() -> list[Amenaza]:
    """Busca archivos con setuid/setgid o world-writable en sitios sensibles."""
    amenazas: list[Amenaza] = []
    dirs_sensibles = [
        Path.home() / ".local" / "bin",
        Path.home() / ".config",
    ]

    for directorio in dirs_sensibles:
        if not directorio.exists():
            continue
        try:
            for ruta in directorio.rglob("*"):
                if not ruta.is_file():
                    continue
                try:
                    st = ruta.stat()
                except OSError:
                    continue
                if st.st_mode & stat.S_ISUID:
                    amenazas.append(Amenaza(
                        categoria="Permisos peligrosos",
                        descripcion="Archivo con bit SETUID",
                        ruta=str(ruta),
                        severidad="error",
                    ))
                if st.st_mode & stat.S_ISGID:
                    amenazas.append(Amenaza(
                        categoria="Permisos peligrosos",
                        descripcion="Archivo con bit SETGID",
                        ruta=str(ruta),
                        severidad="aviso",
                    ))
                if st.st_mode & stat.S_IWOTH:
                    amenazas.append(Amenaza(
                        categoria="Permisos peligrosos",
                        descripcion="Archivo escribible por cualquiera",
                        ruta=str(ruta),
                        severidad="aviso",
                    ))
        except PermissionError:
            continue

    return amenazas


def _check_ssh_autorizado() -> list[Amenaza]:
    """Verifica claves SSH autorizadas desconocidas."""
    amenazas: list[Amenaza] = []
    auth_keys = Path.home() / ".ssh" / "authorized_keys"
    if not auth_keys.exists():
        return amenazas

    try:
        lineas = auth_keys.read_text(encoding="utf-8").splitlines()
        n_keys = sum(1 for l in lineas if l.strip() and not l.strip().startswith("#"))
        if n_keys > 0:
            amenazas.append(Amenaza(
                categoria="SSH",
                descripcion=f"{n_keys} clave(s) SSH autorizadas — verifica que las conoces",
                ruta=str(auth_keys),
                severidad="aviso",
            ))
    except OSError:
        pass

    return amenazas


def _check_cron_sospechoso() -> list[Amenaza]:
    """Revisa cron del usuario buscando entradas sospechosas."""
    amenazas: list[Amenaza] = []
    patrones_peligro = [
        re.compile(r"curl.*\|.*sh", re.IGNORECASE),
        re.compile(r"wget.*\|.*sh", re.IGNORECASE),
        re.compile(r"/dev/tcp/", re.IGNORECASE),
        re.compile(r"base64.*--decode", re.IGNORECASE),
    ]

    try:
        result = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return amenazas
        for linea in result.stdout.splitlines():
            if linea.strip().startswith("#") or not linea.strip():
                continue
            for patron in patrones_peligro:
                if patron.search(linea):
                    amenazas.append(Amenaza(
                        categoria="Cron sospechoso",
                        descripcion=f"Cron con patron peligroso: {linea.strip()[:60]}",
                        ruta="crontab -l",
                        severidad="error",
                    ))
                    break
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # systemd user timers — solo verificar nombres sospechosos
    try:
        result = subprocess.run(
            ["systemctl", "--user", "list-timers", "--no-pager", "--plain"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            for linea in result.stdout.splitlines():
                for sospechoso in ["miner", "xmrig", "cryptonight", "payload"]:
                    if sospechoso in linea.lower():
                        amenazas.append(Amenaza(
                            categoria="Timer sospechoso",
                            descripcion=f"Timer con nombre sospechoso: {linea.strip()[:60]}",
                            ruta="systemctl --user list-timers",
                            severidad="error",
                        ))
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return amenazas


def _check_ejecutables_recientes() -> list[Amenaza]:
    """Busca ejecutables modificados recientemente en PATH del usuario."""
    amenazas: list[Amenaza] = []
    import time
    ahora = time.time()
    hace_24h = ahora - 86400

    dirs_path = [
        Path.home() / ".local" / "bin",
    ]

    for directorio in dirs_path:
        if not directorio.exists():
            continue
        try:
            for ruta in directorio.iterdir():
                if not ruta.is_file():
                    continue
                try:
                    st = ruta.stat()
                    if st.st_mtime > hace_24h and (st.st_mode & stat.S_IXUSR):
                        amenazas.append(Amenaza(
                            categoria="Ejecutable reciente",
                            descripcion=f"Modificado en las ultimas 24h",
                            ruta=str(ruta),
                            severidad="aviso",
                        ))
                except OSError:
                    continue
        except PermissionError:
            continue

    return amenazas


_VERDE = "\033[32m"


def mostrar_amenazas(amenazas: list[Amenaza]):
    """Muestra las amenazas detectadas en terminal con colores."""
    if not amenazas:
        print(f"\n{_BOLD}{_VERDE}  Sistema limpio — sin amenazas detectadas{_RESET}\n")
        return

    errores = [a for a in amenazas if a.severidad == "error"]
    avisos = [a for a in amenazas if a.severidad == "aviso"]

    if errores:
        print(f"\n{_BOLD}{_ROJO}  Amenazas detectadas: {len(errores)} criticas, {len(avisos)} avisos{_RESET}\n")
    else:
        print(f"\n{_BOLD}{_AMARILLO}  {len(avisos)} punto(s) a revisar{_RESET}\n")

    for a in amenazas:
        color = _ROJO if a.severidad == "error" else _AMARILLO
        tag = "!!" if a.severidad == "error" else "??"
        print(f"  {color}[{tag}]{_RESET} {a.categoria}: {a.descripcion}")
        print(f"       {_GRIS}{a.ruta}{_RESET}")
        print()


# -- Salida con colores ANSI ---------------------------------------------------

_ROJO = "\033[31m"
_AMARILLO = "\033[33m"
_GRIS = "\033[90m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


def mostrar_resultado(resultado: ResultadoEscaneo, es_hook: bool = False):
    """Muestra los hallazgos en terminal con colores ANSI.

    Args:
        resultado: Resultado del escaneo con los hallazgos.
        es_hook: True cuando se invoca desde el pre-commit hook (sin flags);
                 False en escaneos manuales (--scan, --completo, --historial).
    """
    if not resultado.tiene_secretos:
        return

    if es_hook:
        titulo = "COMMIT BLOQUEADO -- Secreto detectado"
    else:
        titulo = "SECRETO DETECTADO"
    print(f"\n{_BOLD}{_ROJO}  {titulo}{_RESET}\n")

    for h in resultado.hallazgos:
        if h.linea is not None:
            print(f"  {_AMARILLO}Archivo:{_RESET}  {h.archivo}  (linea {h.linea})")
            print(f"  {_AMARILLO}Patron:{_RESET}   {h.texto_coincidente}")
            print(f"  {_AMARILLO}Tipo:{_RESET}     {h.tipo}")
        else:
            print(f"  {_AMARILLO}Archivo:{_RESET}  {h.archivo}")
            print(f"  {_AMARILLO}Tipo:{_RESET}     {h.tipo}")
        print()

    print(f"{_GRIS}Usa variables de entorno o un gestor de secretos.")
    print(f"Para ignorar un falso positivo: secretos --whitelist \"texto_exacto\"{_RESET}\n")


# -- Instalacion del hook ------------------------------------------------------

def instalar_hook_local():
    """Instala el hook pre-commit en el repositorio actual."""
    raiz = obtener_raiz_repo()
    if raiz is None:
        print(f"{_ROJO}Error: no se encuentra un repositorio git en el directorio actual{_RESET}")
        sys.exit(1)

    hooks_dir = raiz / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    destino = hooks_dir / "pre-commit"

    if destino.exists():
        print(f"{_AMARILLO}Ya existe un hook pre-commit en {destino}{_RESET}")
        print(f"Haz backup manual si quieres conservarlo.")
        sys.exit(1)

    shutil.copy2(RUTA_HOOK, destino)
    destino.chmod(destino.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    logger.info(f"Hook pre-commit instalado en {destino}")
    print(f"Hook pre-commit instalado en {destino}")


def instalar_hook_global():
    """Configura git para usar el hook en todos los repos."""
    hooks_dir = Path.home() / ".config" / "git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    destino = hooks_dir / "pre-commit"

    if destino.exists():
        print(f"{_AMARILLO}Ya existe un hook pre-commit global en {destino}{_RESET}")
        print(f"Haz backup manual si quieres conservarlo.")
        sys.exit(1)

    shutil.copy2(RUTA_HOOK, destino)
    destino.chmod(destino.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    try:
        subprocess.run(
            ["git", "config", "--global", "core.hooksPath", str(hooks_dir)],
            check=True, capture_output=True, timeout=5,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        logger.error(f"Error configurando core.hooksPath: {e}")
        print(f"{_ROJO}Error configurando git core.hooksPath{_RESET}")
        sys.exit(1)

    logger.info(f"Hook global instalado en {destino}, core.hooksPath = {hooks_dir}")
    print(f"Hook global instalado en {destino}")
    print(f"git core.hooksPath configurado a {hooks_dir}")


# -- Whitelist CLI -------------------------------------------------------------

def agregar_whitelist(patron: str):
    """Agrega un patron a la whitelist del repo actual."""
    raiz = obtener_raiz_repo()
    if raiz is None:
        print(f"{_ROJO}Error: no se encuentra un repositorio git en el directorio actual{_RESET}")
        sys.exit(1)

    ruta_whitelist = raiz / ".secretos-whitelist"

    # Verificar que no existe ya
    existentes = cargar_whitelist(raiz)
    if patron in existentes:
        print(f"El patron ya esta en la whitelist")
        return

    with open(ruta_whitelist, "a", encoding="utf-8") as f:
        f.write(f"{patron}\n")

    logger.info(f"Patron agregado a whitelist: {patron}")
    print(f"Patron agregado a {ruta_whitelist}")


# -- Main ----------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Guardian de las Sombras — Defensor del sistema"
    )
    parser.add_argument(
        "--scan", action="store_true",
        help="Escanea todo el working tree, no solo archivos staged",
    )
    parser.add_argument(
        "--historial", action="store_true",
        help="Escanea el historial de git buscando secretos filtrados",
    )
    parser.add_argument(
        "--amenazas", action="store_true",
        help="Escanea el sistema buscando scripts sospechosos, permisos peligrosos, etc.",
    )
    parser.add_argument(
        "--completo", action="store_true",
        help="Escaneo total: staged + working tree + historial + amenazas",
    )
    parser.add_argument(
        "--instalar", action="store_true",
        help="Instala el hook pre-commit en el repo actual",
    )
    parser.add_argument(
        "--global", dest="hook_global", action="store_true",
        help="Configura git core.hooksPath para usar el hook en todos los repos",
    )
    parser.add_argument(
        "--whitelist", metavar="PATRON", type=str, default=None,
        help="Agrega un patron a la whitelist de falsos positivos",
    )
    args = parser.parse_args()

    if args.instalar:
        instalar_hook_local()
        return

    if args.hook_global:
        instalar_hook_global()
        return

    if args.whitelist is not None:
        agregar_whitelist(args.whitelist)
        return

    config = cargar_config(RUTA_AUTO)
    cfg_notif = config.get("notificacion", {})
    duracion = cfg_notif.get("duracion", 5000)

    if args.amenazas:
        logger.info("Escaneo de amenazas del sistema")
        amenazas = escanear_amenazas()
        mostrar_amenazas(amenazas)
        criticas = [a for a in amenazas if a.severidad == "error"]
        if criticas:
            notificar(CONSEJERO, f"{len(criticas)} amenaza(s) detectada(s) en el sistema", "error", duracion)
            sys.exit(1)
        return

    if args.completo:
        logger.info("Escaneo completo: secretos + amenazas")
        problemas = False

        # Secretos en working tree
        raiz = obtener_raiz_repo()
        if raiz:
            print(f"{_BOLD}  Secretos en el repo...{_RESET}")
            resultado_wt = escanear_working_tree(config)
            mostrar_resultado(resultado_wt)
            if resultado_wt.tiene_secretos:
                problemas = True

            print(f"{_BOLD}  Historial git (ultimos 50 commits)...{_RESET}")
            resultado_hist = escanear_historial(config, max_commits=50)
            mostrar_resultado(resultado_hist)
            if resultado_hist.tiene_secretos:
                problemas = True
        else:
            print(f"{_GRIS}  No hay repo git, saltando escaneo de secretos{_RESET}\n")

        print(f"{_BOLD}  Amenazas del sistema...{_RESET}")
        amenazas = escanear_amenazas()
        mostrar_amenazas(amenazas)
        criticas = [a for a in amenazas if a.severidad == "error"]
        if criticas:
            problemas = True

        if problemas:
            notificar(CONSEJERO, "Escaneo completo: se encontraron problemas", "error", duracion)
            sys.exit(1)
        else:
            notificar(CONSEJERO, "Escaneo completo: todo limpio", "exito", duracion)
        return

    if args.historial:
        logger.info("Escaneo del historial git")
        resultado = escanear_historial(config)
        if resultado.tiene_secretos:
            print(f"\n{_BOLD}{_ROJO}  Secretos encontrados en el historial git{_RESET}\n")
            for h in resultado.hallazgos:
                print(f"  {_AMARILLO}Archivo:{_RESET}  {h.archivo}")
                print(f"  {_AMARILLO}Patron:{_RESET}   {h.texto_coincidente}")
                print(f"  {_AMARILLO}Tipo:{_RESET}     {h.tipo}")
                print()
            print(f"{_GRIS}Usa 'git filter-branch' o 'git-filter-repo' para limpiar el historial.{_RESET}\n")
            notificar(CONSEJERO, f"{len(resultado.hallazgos)} secreto(s) en el historial git", "error", duracion)
            sys.exit(1)
        else:
            print(f"\n{_BOLD}{_VERDE}  Historial limpio — sin secretos{_RESET}\n")
        return

    # Modo por defecto: escaneo de staged (hook pre-commit)
    if args.scan:
        logger.info("Escaneo completo del working tree")
        resultado = escanear_working_tree(config)
        mostrar_resultado(resultado, es_hook=False)
    else:
        logger.info("Escaneo de archivos staged")
        resultado = escanear_staged(config)
        mostrar_resultado(resultado, es_hook=True)

    if resultado.tiene_secretos:
        primer_archivo = resultado.hallazgos[0].archivo
        mensaje = f"Secreto detectado en {primer_archivo}, commit bloqueado"
        notificar(CONSEJERO, mensaje, "error", duracion)
        logger.warning(f"Secretos detectados: {len(resultado.hallazgos)} hallazgo(s)")
        sys.exit(1)
    else:
        logger.info("Escaneo limpio, sin secretos detectados")


if __name__ == "__main__":
    main()
