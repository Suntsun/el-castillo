"""
Politica de la decision `ejecutar_comandos` (PI-2).

Modelo de seguridad (decidido tras descartar PI-2-con-bash):

    OpenCode NO ejecuta nada. El cerebro solo PROPONE comandos estructurados
    (binario + lista de argumentos). El Arquitecto los valida contra ESTA
    allowlist propia y, si pasan, los ejecuta el mismo con
    `subprocess.run([...], shell=False)`, con confirmacion humana y trazas.

Por que aqui y no en un agente OpenCode con bash: la prueba
`bash-permission-tester` demostro que la allowlist/denylist de bash de OpenCode
NO es una frontera de confianza fiable. Por tanto la unica frontera valida es
esta: una whitelist default-deny aplicada por el propio Arquitecto. Nada de
shell, nada de `bash`/`sh`/`python -c`, nada de mutadores.

ALCANCE v1 (read-only / inspeccion):
    - Solo binarios de LECTURA/INSPECCION (ver `COMANDOS_PERMITIDOS`).
    - `git` SOLO con subcomandos de lectura (status/diff/log/...): NUNCA
      push/clean/reset/commit/... (no estan en la whitelist de subcomandos).
    - `systemctl --user` SOLO status/is-active/list-...: NUNCA
      start/stop/restart/enable/disable (no estan en la whitelist).
    - Sin red en v1 (ningun binario marca `requiere_red`).
    - Lote de 1..MAX_COMANDOS; se validan TODOS antes de ejecutar ninguno.

Este modulo NO ejecuta nada y resuelve rutas de forma LEXICA (sin tocar el FS,
sin seguir symlinks), reutilizando las RAICES_AUTORIZADAS y la denylist de
SENSIBLES de `ingenieria.py` (misma frontera que `delegar_ingenieria`).
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from arquitecto import ingenieria


# -- Limites del lote y de cada comando ----------------------------------------

# Maximo de comandos por decision `ejecutar_comandos`.
MAX_COMANDOS = 5
# Maximo de argumentos (tras el binario) que puede llevar un comando.
MAX_ARGS_POR_COMANDO = 24
# Longitud maxima de un argumento individual.
MAX_LEN_ARG = 256
# Timeout duro por comando (segundos). Los comandos de lectura son rapidos;
# un binario que se quede colgado (p. ej. esperando stdin) muere aqui.
TIMEOUT_COMANDO_S = 30

# PATH controlado para resolver binarios. Excluye $HOME y rutas del usuario a
# proposito: ningun binario propuesto se resuelve desde un directorio
# escribible por el usuario (anti-shadowing).
PATH_SEGURO = "/usr/local/sbin:/usr/local/bin:/usr/bin:/usr/sbin:/sbin:/bin"


# -- Metacaracteres de shell (defensa en profundidad) --------------------------

# Aunque ejecutamos con shell=False (y por tanto los metacaracteres son
# literales e inofensivos para la shell), seguimos la "regla de oro" del
# proyecto: un argumento con metacaracteres de shell delata un cerebro
# confundido o manipulado, asi que se RECHAZA. Mismo conjunto que validador y
# seguridad (se repite a proposito para no acoplar modulos).
_METACARACTERES_SHELL: frozenset[str] = frozenset({
    ";", "|", "&", "`", "$", ">", "<", "\n", "\r", "\\",
})


# -- Flags prohibidos transversales (cualquier binario) ------------------------

# Flags peligrosos con independencia del binario. Cubren inyeccion via
# ejecucion de subprocesos (find -exec, etc.) y "follow" infinito. Se aplican
# a TODOS los binarios como red de seguridad adicional a la whitelist.
_FLAGS_PROHIBIDOS_GLOBAL: frozenset[str] = frozenset({
    "-exec", "-execdir", "--exec", "-delete",
    "-ok", "-okdir", "-fprintf", "-fprint", "-fprint0",
    "--follow",
})


# -- Politica por binario ------------------------------------------------------


@dataclass(frozen=True)
class PoliticaComando:
    """Politica de un binario permitido.

    Attributes:
        subcomandos: Si no es None, el primer token que NO es flag debe ser un
            subcomando de este conjunto (p. ej. git/systemctl). Ademas, ningun
            flag puede aparecer ANTES del subcomando (bloquea `git -c x=y ...`).
        flags_prohibidos: Flags vetados para ESTE binario (ademas de los
            globales). Se comparan exactos y por la parte previa a '=' en la
            forma `--flag=valor`.
        flags_forzados: Flags que el Arquitecto INYECTA tras el binario (antes
            de los argumentos del cerebro) para endurecer la ejecucion
            (p. ej. `--no-pager`, `--user`). Se deduplican si el cerebro ya los
            incluyo.
        requiere_red: True si el binario necesita conectividad. En v1 ningun
            binario lo marca (sin red).
    """

    subcomandos: frozenset[str] | None = None
    flags_prohibidos: frozenset[str] = frozenset()
    flags_forzados: tuple[str, ...] = ()
    requiere_red: bool = False


# Subcomandos de SOLO LECTURA de git. Cualquier subcomando que MUTE el repo
# (commit, add, push, pull, fetch, reset, checkout, switch, restore, clean, rm,
# mv, merge, rebase, stash, config, clone, init, apply, am, cherry-pick,
# revert, gc, submodule, worktree, bisect, filter-branch, ...) queda FUERA de
# este conjunto y por tanto RECHAZADO.
_GIT_SUBCOMANDOS_LECTURA: frozenset[str] = frozenset({
    "status", "diff", "log", "show", "branch", "remote", "describe",
    "rev-parse", "ls-files", "ls-tree", "shortlog", "blame", "tag", "reflog",
    "whatchanged", "for-each-ref", "cat-file", "show-ref", "symbolic-ref",
    "rev-list", "count-objects", "var", "help",
})

# Flags globales de git que permiten escapar del confinamiento o inyectar
# configuracion/ejecucion. Se vetan aunque la posicion ya los bloquee.
_GIT_FLAGS_PROHIBIDOS: frozenset[str] = frozenset({
    "-c", "-C", "--exec-path", "--git-dir", "--work-tree", "--namespace",
    "--ext-diff", "--open-files-in-pager", "-p", "--paginate",
    "--no-replace-objects",
})

# Subcomandos de SOLO LECTURA de systemctl (modo --user). start/stop/restart/
# enable/disable/mask/daemon-reload/... NO estan aqui -> rechazados.
_SYSTEMCTL_SUBCOMANDOS_LECTURA: frozenset[str] = frozenset({
    "status", "is-active", "is-enabled", "is-failed", "show", "cat",
    "list-units", "list-timers", "list-unit-files", "list-dependencies",
    "list-sockets", "get-default", "show-environment",
})

# systemctl: forzamos --user (nunca a nivel de sistema) y --no-pager. Vetamos
# los flags que cambian el ambito a sistema/maquina remota.
_SYSTEMCTL_FLAGS_PROHIBIDOS: frozenset[str] = frozenset({
    "--system", "-H", "--host", "-M", "--machine",
})

# journalctl no usa subcomandos (todo por flags). Forzamos --user/--no-pager y
# vetamos follow y cualquier operacion de mantenimiento del journal.
_JOURNALCTL_FLAGS_PROHIBIDOS: frozenset[str] = frozenset({
    "-f", "--follow", "--flush", "--rotate", "--sync", "--relinquish-var",
    "--vacuum-size", "--vacuum-time", "--vacuum-files", "--setup-keys",
})

# rg/fd pueden EJECUTAR comandos externos via flags; se vetan esos vectores.
_RG_FLAGS_PROHIBIDOS: frozenset[str] = frozenset({"--pre", "--pre-glob", "--hostname-bin"})
_FD_FLAGS_PROHIBIDOS: frozenset[str] = frozenset({"-x", "--exec", "-X", "--exec-batch"})

# tail -f y similares "siguen" indefinidamente -> bloquean la terminal.
_TAIL_FLAGS_PROHIBIDOS: frozenset[str] = frozenset({"-f", "-F", "--follow", "--retry"})


# Allowlist default-deny. Cualquier binario que no este aqui se RECHAZA.
COMANDOS_PERMITIDOS: dict[str, PoliticaComando] = {
    # -- Inspeccion de ficheros (rutas confinadas a raices) --
    "ls": PoliticaComando(),
    "cat": PoliticaComando(),
    "head": PoliticaComando(),
    "tail": PoliticaComando(flags_prohibidos=_TAIL_FLAGS_PROHIBIDOS),
    "wc": PoliticaComando(),
    "nl": PoliticaComando(),
    "file": PoliticaComando(),
    "stat": PoliticaComando(),
    "realpath": PoliticaComando(),
    "basename": PoliticaComando(),
    "dirname": PoliticaComando(),
    "tree": PoliticaComando(),
    "du": PoliticaComando(),
    # -- Busqueda (confinada) --
    "grep": PoliticaComando(),
    "rg": PoliticaComando(flags_prohibidos=_RG_FLAGS_PROHIBIDOS),
    "fd": PoliticaComando(flags_prohibidos=_FD_FLAGS_PROHIBIDOS),
    # -- Info del sistema (sin rutas o rutas confinadas) --
    "df": PoliticaComando(),
    "free": PoliticaComando(),
    "uptime": PoliticaComando(),
    "uname": PoliticaComando(),
    "whoami": PoliticaComando(),
    "id": PoliticaComando(),
    "date": PoliticaComando(),
    "hostname": PoliticaComando(),
    "nproc": PoliticaComando(),
    "arch": PoliticaComando(),
    "lsblk": PoliticaComando(),
    "lscpu": PoliticaComando(),
    "ps": PoliticaComando(),
    # -- Control de versiones (SOLO lectura) --
    "git": PoliticaComando(
        subcomandos=_GIT_SUBCOMANDOS_LECTURA,
        flags_prohibidos=_GIT_FLAGS_PROHIBIDOS,
        flags_forzados=("--no-pager",),
    ),
    # -- Servicios systemd / journal (SOLO lectura, modo --user) --
    "systemctl": PoliticaComando(
        subcomandos=_SYSTEMCTL_SUBCOMANDOS_LECTURA,
        flags_prohibidos=_SYSTEMCTL_FLAGS_PROHIBIDOS,
        flags_forzados=("--user", "--no-pager"),
    ),
    "journalctl": PoliticaComando(
        flags_prohibidos=_JOURNALCTL_FLAGS_PROHIBIDOS,
        flags_forzados=("--user", "--no-pager"),
    ),
}


def binario_permitido(binario: str) -> PoliticaComando | None:
    """Politica del binario, o None si NO esta en la allowlist."""
    if not isinstance(binario, str):
        return None
    return COMANDOS_PERMITIDOS.get(binario)


# -- Helpers puros -------------------------------------------------------------


def _es_flag(token: str) -> bool:
    return token.startswith("-")


def _nombre_flag(token: str) -> str:
    """Parte previa a '=' de un flag (`--unit=x` -> `--unit`)."""
    return token.split("=", 1)[0]


def _tiene_dotdot(token: str) -> bool:
    segmentos = token.replace("\\", "/").split("/")
    return ".." in segmentos


def _es_token_ruta_explicita(token: str) -> bool:
    """True si el token es una ruta absoluta o de HOME ('/...' o '~...').

    Los tokens relativos sin '..' resuelven dentro del directorio autorizado
    (cwd del comando) y se consideran seguros sin mas comprobacion.
    """
    return token.startswith("/") or token.startswith("~")


def validar_forma_comando(
    binario: str, argumentos: list,
) -> tuple[bool, str, list[str] | None]:
    """Valida la FORMA de un comando (sin tocar el FS ni necesitar cwd).

    Comprueba: binario en la allowlist; argumentos string sin metacaracteres,
    sin NUL, dentro de longitud y numero; sin flags prohibidos; subcomando
    valido (si aplica) y sin flags antes del subcomando; sin segmentos '..'.

    Returns:
        (ok, motivo, argv_final). `argv_final` incluye el binario y los flags
        forzados (deduplicados) seguidos de los argumentos del cerebro. None si
        no valida.
    """
    politica = binario_permitido(binario)
    if politica is None:
        return False, f"binario '{binario}' no esta en la allowlist", None

    if not isinstance(argumentos, list):
        return False, f"{binario}: 'argumentos' debe ser una lista", None
    if len(argumentos) > MAX_ARGS_POR_COMANDO:
        return False, (
            f"{binario}: demasiados argumentos ({len(argumentos)} > "
            f"{MAX_ARGS_POR_COMANDO})"
        ), None

    prohibidos = _FLAGS_PROHIBIDOS_GLOBAL | politica.flags_prohibidos

    for tok in argumentos:
        if not isinstance(tok, str):
            return False, f"{binario}: cada argumento debe ser string", None
        if len(tok) > MAX_LEN_ARG:
            return False, f"{binario}: argumento demasiado largo (>{MAX_LEN_ARG})", None
        if "\x00" in tok:
            return False, f"{binario}: argumento con caracter NUL", None
        for ch in tok:
            if ch in _METACARACTERES_SHELL:
                return False, (
                    f"{binario}: argumento '{tok}' contiene metacaracter "
                    f"shell {ch!r}"
                ), None
        if _tiene_dotdot(tok):
            return False, f"{binario}: argumento '{tok}' contiene '..'", None
        if _es_flag(tok):
            nombre = _nombre_flag(tok)
            if nombre in prohibidos or tok in prohibidos:
                return False, f"{binario}: flag prohibido '{tok}'", None

    # Subcomando (git/systemctl): el primer token NO-flag debe estar permitido,
    # y no puede haber flags antes de el (bloquea `git -c x=y status`).
    if politica.subcomandos is not None:
        sub = None
        for tok in argumentos:
            if _es_flag(tok):
                return False, (
                    f"{binario}: no se admiten flags antes del subcomando "
                    f"(visto '{tok}')"
                ), None
            sub = tok
            break
        if sub is None:
            return False, f"{binario}: falta el subcomando", None
        if sub not in politica.subcomandos:
            return False, (
                f"{binario}: subcomando '{sub}' no permitido (solo lectura: "
                f"{sorted(politica.subcomandos)})"
            ), None

    forzados = [f for f in politica.flags_forzados if f not in argumentos]
    argv_final = [binario, *forzados, *argumentos]
    return True, "", argv_final


def confinar_rutas_comando(
    argumentos: list[str], *, cwd: Path,
) -> tuple[bool, str]:
    """Confina las rutas EXPLICITAS de un comando a las raices autorizadas.

    Solo se comprueban los tokens que son ruta absoluta o de HOME ('/...'/'~').
    Los tokens relativos (sin '..', ya garantizado por la forma) resuelven
    dentro de `cwd` (que ya es un directorio autorizado) y se consideran
    seguros. Reutiliza la misma resolucion que `delegar_ingenieria`.
    """
    for tok in argumentos:
        if _es_flag(tok) or not _es_token_ruta_explicita(tok):
            continue
        _resuelto, motivo = ingenieria.resolver_directorio_autorizado(tok, cwd)
        if motivo is not None:
            return False, f"ruta '{tok}': {motivo}"
    return True, ""


def directorio_base_por_defecto() -> Path:
    """Directorio autorizado por defecto cuando el comando no indica uno.

    Es la raiz del ecosistema de automatizaciones (siempre autorizada). Sirve
    de cwd inocuo para los comandos sin ruta (df, uname, ...).
    """
    return ingenieria.raices_autorizadas()[0]


def preparar_comando(
    cmd: dict, *, cwd_base: Path | None = None,
) -> tuple[list[str] | None, Path | None, str | None]:
    """Resuelve UN comando a (argv_final, directorio, motivo).

    Fuente UNICA que comparten `seguridad` (veredicto) y `ejecutor`
    (lanzamiento): valida forma, resuelve el directorio contra las raices
    autorizadas y confina las rutas explicitas de los argumentos.

    Returns:
        (argv, directorio, None) si es ejecutable; (None, None, motivo) si se
        bloquea.
    """
    base = cwd_base if cwd_base is not None else directorio_base_por_defecto()

    binario = cmd.get("binario")
    argumentos = cmd.get("argumentos")
    ok, motivo, argv = validar_forma_comando(binario, argumentos or [])
    if not ok or argv is None:
        return None, None, motivo

    directorio, motivo = ingenieria.resolver_directorio_autorizado(
        cmd.get("directorio"), base,
    )
    if motivo is not None:
        return None, None, motivo

    ok, motivo = confinar_rutas_comando(list(argumentos or []), cwd=directorio)
    if not ok:
        return None, None, motivo

    return argv, directorio, None


def preparar_lote(
    comandos: list[dict], *, cwd_base: Path | None = None,
) -> tuple[list[tuple[list[str], Path, dict]], str | None]:
    """Prepara TODOS los comandos antes de ejecutar ninguno (all-or-nothing).

    Returns:
        (preparados, None) donde `preparados` es una lista de
        (argv, directorio, cmd_original); o ([], motivo) si CUALQUIER comando
        no valida (no se ejecuta nada del lote).
    """
    if not isinstance(comandos, list) or not comandos:
        return [], "no hay comandos que ejecutar"
    if len(comandos) > MAX_COMANDOS:
        return [], f"demasiados comandos ({len(comandos)} > {MAX_COMANDOS})"

    preparados: list[tuple[list[str], Path, dict]] = []
    for i, cmd in enumerate(comandos):
        if not isinstance(cmd, dict):
            return [], f"comando #{i + 1}: no es un objeto"
        argv, directorio, motivo = preparar_comando(cmd, cwd_base=cwd_base)
        if motivo is not None or argv is None or directorio is None:
            return [], f"comando #{i + 1}: {motivo}"
        preparados.append((argv, directorio, cmd))
    return preparados, None


# -- Resolucion del binario y entorno seguro (lo usa el ejecutor) --------------


def resolver_binario(binario: str) -> tuple[str | None, str | None]:
    """Resuelve el binario a una ruta absoluta de un directorio del sistema.

    Usa un PATH controlado (sin $HOME) para evitar shadowing y verifica que la
    ruta resuelta no cuelga de $HOME. No comprueba la allowlist (eso ya lo hizo
    `validar_forma_comando`): aqui solo se localiza el ejecutable.

    Returns:
        (ruta_absoluta, None) si se encontro; (None, motivo) si no.
    """
    if binario_permitido(binario) is None:
        return None, f"binario '{binario}' no permitido"
    ruta = shutil.which(binario, path=PATH_SEGURO)
    if ruta is None:
        return None, f"binario '{binario}' no encontrado en el PATH seguro"
    try:
        real = Path(ruta).resolve()
    except (OSError, RuntimeError, ValueError):
        return None, f"no se pudo resolver la ruta de '{binario}'"
    try:
        real.relative_to(Path.home())
        # Si llega aqui, el ejecutable cuelga de HOME -> sospechoso.
        return None, f"ejecutable de '{binario}' bajo HOME (shadowing): {real}"
    except ValueError:
        pass
    return str(real), None


def entorno_seguro() -> dict[str, str]:
    """Entorno minimo y saneado para los subprocesos de comandos.

    Reemplaza por completo el entorno heredado: fija un PATH controlado y
    desactiva paginadores/prompts interactivos. No propaga variables como
    LD_PRELOAD u otras que pudieran alterar la ejecucion.
    """
    return {
        "PATH": PATH_SEGURO,
        "HOME": str(Path.home()),
        "USER": os.environ.get("USER", ""),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", os.environ.get("LANG", "C.UTF-8")),
        "TERM": "dumb",
        "PAGER": "cat",
        "GIT_PAGER": "cat",
        "SYSTEMD_PAGER": "",
        "SYSTEMD_COLORS": "0",
        "GIT_TERMINAL_PROMPT": "0",
    }
