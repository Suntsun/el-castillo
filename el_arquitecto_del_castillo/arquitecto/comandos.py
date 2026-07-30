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

REDISEÑO (whitelist de FLAGS, no solo de binarios/subcomandos): dos auditorias
adversariales demostraron que una denylist de flags "encuentro un flag
peligroso -> lo añado a la lista" no converge:

    - `getopt_long` acepta cualquier ABREVIATURA unica de un flag largo
      (`--fil=X` == `--file`, `--roo=X` == `--root`): una denylist que compara
      nombres exactos no la ve.
    - los FLAGS CORTOS se pueden AGRUPAR y llevar el valor PEGADO sin
      separador (`-if/etc/passwd` es `-i` + `-f /etc/passwd` para grep); una
      denylist que solo mira los 2 primeros caracteres del token no lo ve.
    - las RUTAS RELATIVAS pegadas a un flag (`--flag=ruta_relativa`,
      `-Xruta_relativa`) nunca se confinaban.
    - un flag_forzado (p. ej. `--user` de journalctl) se podia ANULAR con un
      flag posterior de sentido contrario (`--system`, `-m/--merge`) que la
      denylist no cubria.
    - `comando_puede_escribir()` (la etiqueta de la confirmacion humana) era
      una LISTA APARTE que divergia de la denylist real en ambos sentidos.

La correccion estructural: ALLOWLIST de flags por binario (y, si el binario
tiene subcomandos, por subcomando), default-deny tambien en flags. Antes de
decidir si un flag esta permitido, el token se NORMALIZA:

    1. se desagrupan los flags cortos pegados (`-abc` -> `-a -b -c`;
       `-if/ruta` -> `-i` + `-f` con valor `/ruta`), usando la tabla del
       binario/subcomando para saber que flags cortos llevan valor;
    2. se expanden abreviaturas de flags largos contra la lista de flags
       largos PERMITIDOS de ese binario/subcomando (nunca contra el catalogo
       completo del binario real): si el prefijo no identifica un unico
       flag permitido, se rechaza (tanto si no matchea ninguno como si
       matchea mas de uno: getopt_long tambien rechazaria una abreviatura
       ambigua);
    3. el par flag/valor se separa siempre de forma uniforme, venga como
       `--flag=valor`, `--flag valor` o `-fvalor`.

Cualquier flag que no figure en la tabla del binario/subcomando (exacto o via
abreviatura no ambigua) se RECHAZA: un flag no listado ya no puede colarse
por no habersele ocurrido a nadie ańadirlo a una denylist. La tabla, ademas,
marca que flags llevan un valor que es una RUTA: esos valores se confinan a
las RAICES_AUTORIZADAS exactamente igual que un argumento posicional, INCLUSO
si son relativos (se resuelven contra el directorio del comando).

`comando_puede_escribir()` queda DERIVADA de esta misma tabla en vez de
mantenerse como lista aparte: vuelve a correr `validar_forma_comando` sobre el
argv (asi no hay dos estructuras que puedan divergir); si no validara con la
tabla real, se marca como posible escritura. Es deliberadamente redundante con
la validacion normal (defensa en profundidad, mismo principio que el resto del
modulo), pero LEE la misma fuente de datos.

ALCANCE v1 (read-only / inspeccion), sin cambios de fondo:
    - Solo binarios de LECTURA/INSPECCION (ver `COMANDOS_PERMITIDOS`).
    - `git` SOLO con subcomandos de lectura: NUNCA push/clean/reset/commit/...
      Los subcomandos DUALES (tag/branch/remote: listan sin argumentos pero
      mutan con ellos) se resuelven con un limite EXPLICITO de argumentos
      posicionales por subcomando (`max_posicionales`), dato de la MISMA
      tabla: 0 para tag/branch/remote y reflog (ninguna mutacion posible sin
      un argumento posicional que nunca se admite), 1 para symbolic-ref
      (lee con un argumento, muta con dos). `help` NO esta en la tabla: git
      lanza `man` (u otro navegador via `help.format=web`/`web.browser` del
      repo) como subproceso ajeno a la allowlist para mostrarla, violando la
      premisa "solo estos binarios" (tercera auditoria, H-3).
    - `systemctl`/`journalctl` SOLO lectura, modo `--user` forzado e
      imposible de anular: ni los flags que cambiarian de ambito (`--system`,
      `-M`/`--machine`, `-H`/`--host`, `-m`/`--merge`) figuran en ninguna
      tabla permitida, ni un `--user` que el cerebro cuele como VALOR de otro
      flag o como POSICIONAL (p. ej. tras `--`) puede hacer que el Arquitecto
      omita la inyeccion: la deduplicacion de `flags_forzados` compara contra
      los flags REALMENTE EMITIDOS en posicion de flag (`_Analisis.
      flags_vistos`), nunca contra la secuencia plana de tokens (que tambien
      contiene valores y posicionales) — corregido tras la tercera auditoria
      (H-1), que demostro que un `--user` colado como dato bastaba para que
      nunca se inyectara el forzado real.
    - Sin red en v1 (ningun binario marca `requiere_red`).
    - Lote de 1..MAX_COMANDOS; se validan TODOS antes de ejecutar ninguno.

Se acepta perder funcionalidad marginal a cambio de seguridad (p. ej.
`git tag -l 'v1.*'`, `git remote show origin`, `git reflog <ref-concreto>`):
mınimo privilegio, ante la duda un flag/subcomando se deja fuera de la tabla.

Este modulo NO ejecuta nada. Resuelve el directorio y las rutas EXPLICITAS de
los argumentos reutilizando las RAICES_AUTORIZADAS y la denylist de SENSIBLES
de `ingenieria.py` (misma frontera que `delegar_ingenieria`); esa resolucion
SI seguia symlinks (realpath, no estricto) para reconfinar DESPUES de
resolverlos: un enlace simbolico creado dentro de una raiz autorizada que
apunte fuera de ella no debe pasar la validacion (ver
`ingenieria.resolver_directorio_autorizado`). Los tokens relativos (sin
explicitar ruta) tambien se comprueban por si son (o contienen) un symlink
que escape, aunque no se les aplica la heuristica de nombres sensibles
(pensada solo para rutas explicitas), para no rechazar por error un
argumento que simplemente se LLAME como un fichero sensible.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from arquitecto import ingenieria


# -- Limites del lote y de cada comando ----------------------------------------

# Maximo de comandos por decision `ejecutar_comandos`.
MAX_COMANDOS = 5
# Maximo de argumentos (tras el binario) que puede llevar un comando, sobre el
# argv FINAL ya normalizado (flags expandidos + valores separados + forzados).
MAX_ARGS_POR_COMANDO = 24
# Longitud maxima de un argumento individual (medido sobre el token CRUDO tal
# como lo propone el cerebro, antes de normalizar).
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

# Red de seguridad adicional, redundante a proposito con la whitelist: si por
# error algun dia se añadiera uno de estos nombres a la tabla de un binario,
# esta comprobacion (independiente de la tabla) lo seguiria bloqueando. Cubre
# la familia de flags de `find` que ejecutan subprocesos o borran (ningun
# binario permitido los necesita: `find` ni siquiera esta en la allowlist).
_FLAGS_CATASTROFICOS_GLOBAL: frozenset[str] = frozenset({
    "-exec", "-execdir", "--exec", "-delete",
    "-ok", "-okdir", "-fprintf", "-fprint", "-fprint0",
})


# -- Especificacion de flags (nucleo de la whitelist) --------------------------


@dataclass(frozen=True)
class EspecFlag:
    """Especificacion de UN flag admitido.

    Attributes:
        lleva_valor: True si el flag consume un valor, ya sea pegado
            (`-oVALOR`, `--flag=VALOR`) o en el siguiente token
            (`-o VALOR`, `--flag VALOR`).
        es_ruta: True si, cuando `lleva_valor` es True, ese valor es una ruta
            de fichero/directorio: se confina a las RAICES_AUTORIZADAS
            exactamente igual que un argumento posicional (incluidas rutas
            RELATIVAS, resueltas contra el directorio del comando).
    """

    lleva_valor: bool = False
    es_ruta: bool = False


# Atajo para flags booleanos (el caso mas comun con diferencia).
_BOOL = EspecFlag()


def _valor(*, es_ruta: bool = False) -> EspecFlag:
    """Atajo para un flag que lleva un valor (no booleano)."""
    return EspecFlag(lleva_valor=True, es_ruta=es_ruta)


@dataclass(frozen=True)
class TablaFlags:
    """Flags admitidos para un binario (o para UN subcomando concreto de un
    binario que los tiene) y limite de argumentos posicionales.

    Attributes:
        largos: Flags largos permitidos (`--foo`, incluyendo el prefijo) ->
            su especificacion. Es tambien el espacio de busqueda de
            abreviaturas: `--fo` se resuelve contra ESTAS claves, nunca
            contra el catalogo completo de opciones del binario real.
        cortos: Flags cortos permitidos (`-x`, incluyendo el prefijo) -> su
            especificacion. Se usa tanto para un flag corto suelto como para
            resolver un grupo de flags cortos pegados (`-abc`).
        max_posicionales: Limite de argumentos NO-flag admitidos. `None` =
            sin limite (la mayoria de binarios de inspeccion: los
            posicionales son ficheros/patrones/refs a leer, confinados
            igualmente). Un entero acota subcomandos DUALES de git (listan
            sin argumentos, mutan con ellos): 0 impide cualquier posicional,
            evitando por construccion la mutacion sin necesitar deteccion
            especifica por subcomando.
        acepta_numero_corto: True si este binario/subcomando admite el
            idioma historico `-NUM` (`head -20`, `git log -5`) como flag
            independiente, SIN desagrupar digito a digito. Sin este marcador,
            un token `-20` se interpretaria (incorrectamente) como el grupo
            de flags cortos `-2 -0`, rechazado salvo que '2' y '0' sean por
            casualidad flags cortos reales de ese binario (regresion
            detectada en la tercera auditoria, H-2). El token se emite tal
            cual (no lleva valor propio: para el binario real es equivalente
            a su forma larga, p. ej. `-n 20`).
    """

    largos: dict[str, EspecFlag] = field(default_factory=dict)
    cortos: dict[str, EspecFlag] = field(default_factory=dict)
    max_posicionales: int | None = None
    acepta_numero_corto: bool = False


# Patron del idioma historico `-NUM` (un guion seguido de solo digitos).
_NUMERO_CORTO_RE = re.compile(r"^-[0-9]+$")


@dataclass(frozen=True)
class PoliticaComando:
    """Politica de un binario permitido.

    Attributes:
        tabla: Tabla de flags para binarios SIN subcomandos (journalctl, ls,
            grep, ...). `None` si el binario usa `subcomandos`.
        subcomandos: Si no es `None`, el PRIMER token de `argumentos` debe
            ser exactamente una de estas claves (sin abreviar: los
            subcomandos son un conjunto cerrado y pequeño que el cerebro
            debe escribir tal cual), y NINGUN flag puede aparecer antes de
            el (bloquea `git -c x=y status`). El resto de argumentos se
            valida contra la `TablaFlags` de ESE subcomando.
        flags_forzados: Flags que el Arquitecto INYECTA tras el binario (y
            antes del subcomando, si lo hay) para endurecer la ejecucion
            (p. ej. `--no-pager`, `--user`). Se deduplican si el cerebro ya
            los incluyo.
        flags_incompatibles_forzados: Defensa en profundidad EXPLICITA y
            redundante con la propia tabla: flags que contradirian el
            AMBITO fijado por `flags_forzados` (p. ej. `--system` frente a
            `--user`). Ninguno de estos deberia estar nunca en `tabla`/
            `subcomandos` (por eso ya se rechazan alli como "desconocidos"),
            pero esta comprobacion independiente sigue bloqueando aunque
            algun dia se colara uno por error en la tabla.
        requiere_red: True si el binario necesita conectividad. En v1 ningun
            binario lo marca (sin red).
    """

    tabla: TablaFlags | None = None
    subcomandos: dict[str, TablaFlags] | None = None
    flags_forzados: tuple[str, ...] = ()
    flags_incompatibles_forzados: frozenset[str] = frozenset()
    requiere_red: bool = False


# -- Tablas de flags por binario ------------------------------------------------


# -- git: una TablaFlags por subcomando de SOLO LECTURA ------------------------
#
# Subcomandos DUALES (tag/branch/remote): SIN argumentos solo LISTAN, pero con
# el argumento adecuado CREAN/BORRAN/RENOMBRAN (comprobado en vivo en un repo
# de usar-y-tirar). Con `max_posicionales=0` ningun argumento posicional pasa
# nunca, asi que la mutacion es imposible por construccion (no hace falta
# detectar el patron "nombre de tag/rama/remoto" caso por caso). Se pierde
# `git tag -l 'v1.*'` y `git remote show origin` (marginal, ya asumido).
#
# symbolic-ref: CON un argumento LEE (`HEAD`), con DOS MUEVE HEAD (comprobado
# en vivo); `max_posicionales=1` permite la lectura y bloquea la mutacion. Sin
# `-d`/`--delete` en la tabla (tambien mutaria) no hace falta nada mas.
#
# reflog: `expire`/`delete`/`drop` PURGAN el reflog; en vez de listar esas
# sub-acciones de escritura, se pone `max_posicionales=0`: ningun argumento
# posicional (ni sub-accion de escritura ni referencia concreta) pasa nunca.
# Se pierde `git reflog <ref>` (marginal); `git reflog` a secas sigue OK.
_GIT_SUBCOMANDOS: dict[str, TablaFlags] = {
    "status": TablaFlags(
        cortos={"-s": _BOOL, "-b": _BOOL},
        largos={
            "--short": _BOOL, "--branch": _BOOL, "--ignored": _BOOL,
            "--porcelain": _BOOL,
        },
    ),
    "diff": TablaFlags(
        cortos={"-p": _BOOL, "-u": _BOOL, "-w": _BOOL, "-M": _BOOL, "-C": _BOOL},
        largos={
            "--stat": _BOOL, "--numstat": _BOOL, "--shortstat": _BOOL,
            "--name-only": _BOOL, "--name-status": _BOOL,
            "--no-color": _BOOL, "--color": _BOOL,
            "--cached": _BOOL, "--staged": _BOOL, "--patch": _BOOL,
        },
    ),
    "log": TablaFlags(
        cortos={"-p": _BOOL, "-n": _valor()},
        largos={
            "--oneline": _BOOL, "--graph": _BOOL, "--decorate": _BOOL,
            "--stat": _BOOL, "--all": _BOOL, "--abbrev-commit": _BOOL,
            "--no-merges": _BOOL, "--merges": _BOOL,
            "--max-count": _valor(), "--since": _valor(), "--until": _valor(),
            "--author": _valor(), "--grep": _valor(),
            "--format": _valor(), "--pretty": _valor(),
        },
        # `git log -5` (equivalente a `-n 5`) es idioma historico habitual;
        # sin este marcador el desagrupador de flags cortos lo trocea en
        # '-5' '-nada' (regresion H-2, tercera auditoria).
        acepta_numero_corto=True,
    ),
    "show": TablaFlags(
        cortos={"-p": _BOOL},
        largos={
            "--stat": _BOOL, "--name-only": _BOOL,
            "--format": _valor(), "--pretty": _valor(),
        },
    ),
    "branch": TablaFlags(
        cortos={"-a": _BOOL, "-r": _BOOL, "-v": _BOOL},
        largos={"--list": _BOOL, "--no-color": _BOOL, "--color": _BOOL},
        max_posicionales=0,
    ),
    "remote": TablaFlags(
        cortos={"-v": _BOOL},
        largos={"--verbose": _BOOL},
        max_posicionales=0,
    ),
    "tag": TablaFlags(
        cortos={"-l": _BOOL},
        largos={"--list": _BOOL, "--sort": _valor()},
        max_posicionales=0,
    ),
    "describe": TablaFlags(
        largos={
            "--tags": _BOOL, "--all": _BOOL, "--long": _BOOL,
            "--dirty": _BOOL, "--abbrev": _valor(),
        },
    ),
    "rev-parse": TablaFlags(
        cortos={"-q": _BOOL},
        largos={
            "--abbrev-ref": _BOOL, "--show-toplevel": _BOOL,
            "--show-cdup": _BOOL, "--is-inside-work-tree": _BOOL,
            "--short": _BOOL, "--verify": _BOOL, "--quiet": _BOOL,
        },
    ),
    "ls-files": TablaFlags(
        cortos={
            "-c": _BOOL, "-o": _BOOL, "-m": _BOOL, "-d": _BOOL,
            "-k": _BOOL, "-i": _BOOL, "-z": _BOOL,
        },
        largos={
            "--others": _BOOL, "--cached": _BOOL, "--deleted": _BOOL,
            "--modified": _BOOL, "--ignored": _BOOL,
            "--exclude-standard": _BOOL,
        },
    ),
    "ls-tree": TablaFlags(
        cortos={"-r": _BOOL, "-d": _BOOL, "-l": _BOOL, "-z": _BOOL},
        largos={"--name-only": _BOOL, "--abbrev": _valor()},
    ),
    "shortlog": TablaFlags(
        cortos={"-n": _BOOL, "-s": _BOOL, "-e": _BOOL},
        largos={"--format": _valor()},
    ),
    "blame": TablaFlags(
        cortos={"-w": _BOOL, "-e": _BOOL, "-L": _valor()},
        largos={"--line-porcelain": _BOOL, "--show-email": _BOOL},
    ),
    "reflog": TablaFlags(
        largos={"--all": _BOOL},
        max_posicionales=0,
    ),
    "whatchanged": TablaFlags(
        cortos={"-p": _BOOL},
        largos={"--oneline": _BOOL},
    ),
    "for-each-ref": TablaFlags(
        largos={"--format": _valor(), "--sort": _valor(), "--count": _valor()},
    ),
    "cat-file": TablaFlags(
        cortos={"-t": _BOOL, "-s": _BOOL, "-p": _BOOL},
        largos={"--textconv": _BOOL},
    ),
    "show-ref": TablaFlags(
        cortos={"-d": _BOOL, "-q": _BOOL},
        largos={"--tags": _BOOL, "--heads": _BOOL, "--verify": _BOOL},
    ),
    "symbolic-ref": TablaFlags(
        cortos={"-q": _BOOL},
        largos={"--short": _BOOL},
        max_posicionales=1,
    ),
    "rev-list": TablaFlags(
        largos={"--count": _BOOL, "--all": _BOOL},
    ),
    "count-objects": TablaFlags(
        cortos={"-v": _BOOL},
    ),
    "var": TablaFlags(
        cortos={"-l": _BOOL},
        max_posicionales=1,
    ),
    # NOTA (H-3, tercera auditoria): `help` se dejo FUERA a proposito. `git
    # help <topic>` lanza `man` (o un navegador, si el repo tiene
    # `help.format=web`/`web.browser` en su config) como subproceso ajeno a
    # la allowlist: rompe la premisa "solo estos binarios" en la que se apoya
    # toda la arquitectura. No aporta nada que `--help` (que ya imprime por
    # stdout sin subproceso) no cubra, y `--help` tampoco figura aqui salvo
    # que se necesite explicitamente.
}

# systemctl: la misma tabla para todos los subcomandos de lectura permitidos
# (ninguno necesita flags distintos de los demas). Se fuerza `--user` (nunca
# a nivel de sistema) y `--no-pager`; los flags que cambiarian el ambito a
# sistema/maquina remota (`--system`, `-H`/`--host`, `-M`/`--machine`)
# sencillamente NO figuran aqui. Ademas, la deduplicacion de `--user` YA NO
# compara contra la secuencia plana de argumentos (que incluiria valores y
# posicionales), sino contra los flags REALMENTE emitidos en posicion de
# flag (`_Analisis.flags_vistos`): un `--user` colado como dato (p. ej.
# `systemctl cat sshd -- --user`) ya no cuenta como si el flag ya estuviera
# puesto (corregido tras H-1, tercera auditoria; ver docstring del modulo).
_SYSTEMCTL_TABLA_COMPARTIDA = TablaFlags(
    cortos={"-l": _BOOL, "-a": _BOOL, "-p": _valor()},
    largos={
        "--all": _BOOL, "--full": _BOOL, "--plain": _BOOL,
        "--no-legend": _BOOL, "--user": _BOOL, "--no-pager": _BOOL,
        "--type": _valor(), "--state": _valor(),
    },
)
_SYSTEMCTL_SUBCOMANDOS: dict[str, TablaFlags] = {
    nombre: _SYSTEMCTL_TABLA_COMPARTIDA
    for nombre in (
        "status", "is-active", "is-enabled", "is-failed", "show", "cat",
        "list-units", "list-timers", "list-unit-files", "list-dependencies",
        "list-sockets", "get-default", "show-environment",
    )
}

# journalctl no usa subcomandos (todo por flags). Se fuerza `--user`/
# `--no-pager` (la deduplicacion mira `flags_vistos`, no la secuencia plana:
# ver nota junto a `_SYSTEMCTL_TABLA_COMPARTIDA` y el docstring del modulo,
# H-1 de la tercera auditoria). `-f/--follow` (sigue indefinidamente) y toda
# la familia de mantenimiento del journal (`--flush`, `--rotate`, `--sync`,
# `--relinquish-var`, `--vacuum-*`, `--setup-keys`) quedan fuera. Igual
# `-i/--file`, `-D/--directory`, `--root`, `--image`, `--cursor-file` (leen/
# escriben en una ruta arbitraria, bypasseando el confinamiento) y
# `-m/--merge` (fusiona el journal de TODOS los namespaces, incluido el de
# sistema: anularia `--user` igual que `--system`). `acepta_numero_corto`
# habilita `-b -1` (boot anterior): sin el, `-1` se rechazaba como flag
# corto desconocido tras `-b` (regresion H-2, tercera auditoria).
_JOURNALCTL_TABLA = TablaFlags(
    cortos={
        "-r": _BOOL, "-a": _BOOL, "-e": _BOOL, "-x": _BOOL, "-q": _BOOL,
        "-k": _BOOL, "-b": _BOOL,
        "-n": _valor(), "-u": _valor(), "-p": _valor(), "-o": _valor(),
        "-g": _valor(), "-t": _valor(),
    },
    largos={
        "--reverse": _BOOL, "--all": _BOOL, "--pager-end": _BOOL,
        "--catalog": _BOOL, "--quiet": _BOOL, "--dmesg": _BOOL,
        "--boot": _BOOL, "--utc": _BOOL, "--no-hostname": _BOOL,
        "--case-sensitive": _BOOL, "--user": _BOOL, "--no-pager": _BOOL,
        "--lines": _valor(), "--unit": _valor(), "--user-unit": _valor(),
        "--priority": _valor(), "--output": _valor(), "--grep": _valor(),
        "--since": _valor(), "--until": _valor(), "--identifier": _valor(),
        "--field": _valor(),
    },
    acepta_numero_corto=True,
)

# -- Resto de binarios: sin subcomandos, todos con flags booleanos salvo los
#    explicitamente marcados como `_valor()`. Ninguno lleva flags cuyo valor
#    sea una ruta: en todos los binarios de este bloque "lo que hay que
#    inspeccionar" viaja como argumento POSICIONAL (ya confinado igual que
#    cualquier otro), nunca como valor de un flag; por eso no aparece ningun
#    `_valor(es_ruta=True)` en v1 (el mecanismo existe y esta cubierto por un
#    test dedicado, pero minimo privilegio no encuentra un caso real que lo
#    necesite: los flags historicamente peligrosos -o/--output, --files0-from,
#    -D/--directory, --root, --image... son precisamente los que se excluyen).
_LS_TABLA = TablaFlags(cortos={
    f: _BOOL for f in (
        "-l", "-a", "-A", "-h", "-R", "-t", "-S", "-r", "-1", "-d",
        "-F", "-i", "-p", "-n", "-g", "-o",
    )
})
_CAT_TABLA = TablaFlags(cortos={
    f: _BOOL for f in ("-n", "-A", "-E", "-T", "-s", "-b", "-v")
})
# `acepta_numero_corto` habilita `head -20`/`tail -50` (idioma historico
# equivalente a `-n 20`/`-n 50`): sin el, el desagrupador de flags cortos
# trocea '-20' en '-2'+'-0', ninguno de los cuales es un flag real de head/
# tail (regresion H-2, tercera auditoria).
_HEAD_TABLA = TablaFlags(cortos={
    "-n": _valor(), "-c": _valor(), "-q": _BOOL, "-v": _BOOL, "-z": _BOOL,
}, acepta_numero_corto=True)
_TAIL_TABLA = TablaFlags(cortos={
    "-n": _valor(), "-c": _valor(), "-q": _BOOL, "-v": _BOOL,
}, acepta_numero_corto=True)
_NL_TABLA = TablaFlags(cortos={
    "-b": _valor(), "-n": _valor(), "-w": _valor(), "-s": _valor(),
    "-v": _valor(), "-i": _valor(),
})
_FILE_TABLA = TablaFlags(cortos={
    f: _BOOL for f in ("-b", "-i", "-L", "-k", "-N", "-z")
})
_STAT_TABLA = TablaFlags(cortos={
    "-L": _BOOL, "-f": _BOOL, "-t": _BOOL, "-c": _valor(),
})
_REALPATH_TABLA = TablaFlags(cortos={
    f: _BOOL for f in ("-e", "-m", "-s", "-z")
})
_BASENAME_TABLA = TablaFlags(cortos={
    "-a": _BOOL, "-z": _BOOL, "-s": _valor(),
})
_DIRNAME_TABLA = TablaFlags(cortos={"-z": _BOOL})
_TREE_TABLA = TablaFlags(cortos={
    "-a": _BOOL, "-d": _BOOL, "-f": _BOOL, "-i": _BOOL, "-A": _BOOL,
    "-C": _BOOL, "-x": _BOOL, "-n": _BOOL, "-p": _BOOL, "-u": _BOOL,
    "-g": _BOOL, "-s": _BOOL, "-q": _BOOL, "-h": _BOOL, "-L": _valor(),
})
_DU_TABLA = TablaFlags(
    cortos={
        "-h": _BOOL, "-s": _BOOL, "-c": _BOOL, "-a": _BOOL, "-x": _BOOL,
        "-d": _valor(),
    },
    largos={"--max-depth": _valor()},
)
_WC_TABLA = TablaFlags(cortos={
    f: _BOOL for f in ("-l", "-w", "-c", "-m", "-L")
})
_GREP_TABLA = TablaFlags(
    cortos={
        "-i": _BOOL, "-n": _BOOL, "-r": _BOOL, "-R": _BOOL, "-v": _BOOL,
        "-w": _BOOL, "-x": _BOOL, "-l": _BOOL, "-L": _BOOL, "-c": _BOOL,
        "-o": _BOOL, "-E": _BOOL, "-F": _BOOL, "-G": _BOOL, "-P": _BOOL,
        "-H": _BOOL, "-z": _BOOL,
        "-A": _valor(), "-B": _valor(), "-C": _valor(),
        "-m": _valor(), "-e": _valor(),
    },
    largos={
        "--color": _BOOL,
        "--include": _valor(), "--exclude": _valor(), "--exclude-dir": _valor(),
    },
)
_RG_TABLA = TablaFlags(
    cortos={
        "-i": _BOOL, "-n": _BOOL, "-H": _BOOL, "-u": _BOOL, "-l": _BOOL,
        "-c": _BOOL, "-v": _BOOL, "-w": _BOOL, "-x": _BOOL, "-S": _BOOL,
        "-F": _BOOL,
        "-e": _valor(), "-g": _valor(), "-t": _valor(),
        "-A": _valor(), "-B": _valor(), "-C": _valor(), "-m": _valor(),
    },
    largos={
        "--hidden": _BOOL, "--no-ignore": _BOOL, "--smart-case": _BOOL,
        "--fixed-strings": _BOOL, "--line-number": _BOOL, "--count": _BOOL,
        "--glob": _valor(), "--type": _valor(), "--max-count": _valor(),
        "--max-depth": _valor(),
    },
)
_FD_TABLA = TablaFlags(
    cortos={
        "-H": _BOOL, "-I": _BOOL, "-a": _BOOL, "-L": _BOOL, "-p": _BOOL,
        "-i": _BOOL, "-s": _BOOL, "-1": _BOOL,
        "-t": _valor(), "-e": _valor(), "-d": _valor(),
    },
    largos={
        "--hidden": _BOOL, "--no-ignore": _BOOL, "--absolute-path": _BOOL,
        "--follow": _BOOL, "--full-path": _BOOL, "--ignore-case": _BOOL,
        "--case-sensitive": _BOOL,
        "--type": _valor(), "--extension": _valor(), "--max-depth": _valor(),
    },
)
_DF_TABLA = TablaFlags(
    cortos={
        "-h": _BOOL, "-H": _BOOL, "-T": _BOOL, "-i": _BOOL, "-a": _BOOL,
        "-k": _BOOL,
        "-x": _valor(), "-t": _valor(),
    },
    largos={"--total": _BOOL},
)
_FREE_TABLA = TablaFlags(
    cortos={
        "-h": _BOOL, "-m": _BOOL, "-g": _BOOL, "-k": _BOOL, "-t": _BOOL,
        "-w": _BOOL, "-l": _BOOL,
        "-s": _valor(), "-c": _valor(),
    },
    max_posicionales=0,
)
_UPTIME_TABLA = TablaFlags(
    cortos={"-p": _BOOL, "-s": _BOOL}, max_posicionales=0,
)
_UNAME_TABLA = TablaFlags(cortos={
    f: _BOOL for f in ("-a", "-s", "-n", "-r", "-v", "-m", "-p", "-i", "-o")
}, max_posicionales=0)
_WHOAMI_TABLA = TablaFlags(max_posicionales=0)
_ID_TABLA = TablaFlags(
    cortos={f: _BOOL for f in ("-u", "-g", "-n", "-G", "-a")},
    max_posicionales=1,
)
_DATE_TABLA = TablaFlags(
    cortos={"-u": _BOOL, "-I": _BOOL, "-R": _BOOL},
    largos={"--utc": _BOOL, "--date": _valor()},
    max_posicionales=1,
)
_HOSTNAME_TABLA = TablaFlags(cortos={
    f: _BOOL for f in ("-f", "-s", "-d", "-i")
}, max_posicionales=0)
_NPROC_TABLA = TablaFlags(largos={"--all": _BOOL}, max_posicionales=0)
_ARCH_TABLA = TablaFlags(max_posicionales=0)
_LSBLK_TABLA = TablaFlags(cortos={
    "-a": _BOOL, "-f": _BOOL, "-d": _BOOL, "-p": _BOOL, "-l": _BOOL,
    "-n": _BOOL, "-o": _valor(),
}, max_posicionales=0)
_LSCPU_TABLA = TablaFlags(cortos={
    f: _BOOL for f in ("-a", "-e", "-p")
}, max_posicionales=0)
# Sin `max_posicionales`: a diferencia de los subcomandos DUALES de git, `ps`
# nunca muta nada con un argumento posicional (listas de PIDs o la forma BSD
# sin guion, p. ej. `ps aux`); limitarlo a 0 solo rompia el uso mas comun de
# `ps` sin cerrar ningun riesgo real (regresion H-2, tercera auditoria).
_PS_TABLA = TablaFlags(cortos={
    "-e": _BOOL, "-f": _BOOL, "-a": _BOOL, "-u": _BOOL, "-x": _BOOL,
    "-o": _valor(), "-p": _valor(),
}, largos={"--sort": _valor()})


# Allowlist default-deny. Cualquier binario que no este aqui se RECHAZA.
COMANDOS_PERMITIDOS: dict[str, PoliticaComando] = {
    # -- Inspeccion de ficheros (rutas confinadas a raices) --
    "ls": PoliticaComando(tabla=_LS_TABLA),
    "cat": PoliticaComando(tabla=_CAT_TABLA),
    "head": PoliticaComando(tabla=_HEAD_TABLA),
    "tail": PoliticaComando(tabla=_TAIL_TABLA),
    "nl": PoliticaComando(tabla=_NL_TABLA),
    "file": PoliticaComando(tabla=_FILE_TABLA),
    "stat": PoliticaComando(tabla=_STAT_TABLA),
    "realpath": PoliticaComando(tabla=_REALPATH_TABLA),
    "basename": PoliticaComando(tabla=_BASENAME_TABLA),
    "dirname": PoliticaComando(tabla=_DIRNAME_TABLA),
    "tree": PoliticaComando(tabla=_TREE_TABLA),
    "du": PoliticaComando(tabla=_DU_TABLA),
    "wc": PoliticaComando(tabla=_WC_TABLA),
    # -- Busqueda (confinada) --
    "grep": PoliticaComando(tabla=_GREP_TABLA),
    "rg": PoliticaComando(tabla=_RG_TABLA),
    "fd": PoliticaComando(tabla=_FD_TABLA),
    # -- Info del sistema (sin rutas o rutas confinadas) --
    "df": PoliticaComando(tabla=_DF_TABLA),
    "free": PoliticaComando(tabla=_FREE_TABLA),
    "uptime": PoliticaComando(tabla=_UPTIME_TABLA),
    "uname": PoliticaComando(tabla=_UNAME_TABLA),
    "whoami": PoliticaComando(tabla=_WHOAMI_TABLA),
    "id": PoliticaComando(tabla=_ID_TABLA),
    "date": PoliticaComando(tabla=_DATE_TABLA),
    "hostname": PoliticaComando(tabla=_HOSTNAME_TABLA),
    "nproc": PoliticaComando(tabla=_NPROC_TABLA),
    "arch": PoliticaComando(tabla=_ARCH_TABLA),
    "lsblk": PoliticaComando(tabla=_LSBLK_TABLA),
    "lscpu": PoliticaComando(tabla=_LSCPU_TABLA),
    "ps": PoliticaComando(tabla=_PS_TABLA),
    # -- Control de versiones (SOLO lectura) --
    "git": PoliticaComando(
        subcomandos=_GIT_SUBCOMANDOS,
        flags_forzados=("--no-pager",),
        flags_incompatibles_forzados=frozenset({"-p", "--paginate"}),
    ),
    # -- Servicios systemd / journal (SOLO lectura, modo --user) --
    "systemctl": PoliticaComando(
        subcomandos=_SYSTEMCTL_SUBCOMANDOS,
        flags_forzados=("--user", "--no-pager"),
        flags_incompatibles_forzados=frozenset({
            "--system", "-H", "--host", "-M", "--machine",
        }),
    ),
    "journalctl": PoliticaComando(
        tabla=_JOURNALCTL_TABLA,
        flags_forzados=("--user", "--no-pager"),
        flags_incompatibles_forzados=frozenset({
            "--system", "-M", "--machine", "-H", "--host", "-m", "--merge",
        }),
    ),
}


def binario_permitido(binario: str) -> PoliticaComando | None:
    """Politica del binario, o None si NO esta en la allowlist."""
    if not isinstance(binario, str):
        return None
    return COMANDOS_PERMITIDOS.get(binario)


# -- Helpers puros de saneamiento (sobre tokens CRUDOS) ------------------------


def _es_flag(token: str) -> bool:
    """True si `token` DEBE tratarse como un flag (y no como un argumento
    posicional). Un solo '-' es el idioma habitual para stdin/stdout y se
    trata como posicional."""
    return isinstance(token, str) and token.startswith("-") and token != "-"


def _tiene_dotdot(token: str) -> bool:
    segmentos = token.replace("\\", "/").split("/")
    return ".." in segmentos


def _es_token_ruta_explicita(token: str) -> bool:
    """True si el token es una ruta absoluta o de HOME ('/...' o '~...').

    Los tokens relativos sin '..' resuelven dentro del directorio autorizado
    (cwd del comando) y se consideran seguros sin mas comprobacion (aunque
    igualmente se comprueba que no escapen por symlink, ver
    `ingenieria.ruta_relativa_escapa_por_symlink`).
    """
    return token.startswith("/") or token.startswith("~")


def _validar_token_crudo(binario: str, token: str) -> str | None:
    """Motivo de rechazo si el token CRUDO (previo a cualquier normalizacion
    de flags) viola los limites basicos: tipo, longitud, NUL, metacaracteres
    de shell o un '..' ya visible en el token tal cual llego. None si pasa.
    """
    if len(token) > MAX_LEN_ARG:
        return f"{binario}: argumento demasiado largo (>{MAX_LEN_ARG})"
    if "\x00" in token:
        return f"{binario}: argumento con caracter NUL"
    for ch in token:
        if ch in _METACARACTERES_SHELL:
            return (
                f"{binario}: argumento '{token}' contiene metacaracter "
                f"shell {ch!r}"
            )
    if _tiene_dotdot(token):
        return f"{binario}: argumento '{token}' contiene '..'"
    return None


# -- Normalizacion y validacion de flags (nucleo del rediseño) -----------------


@dataclass(frozen=True)
class _Analisis:
    """Resultado de normalizar los argumentos de un comando (o de la parte
    posterior al subcomando, si el binario tiene) contra su `TablaFlags`.

    Attributes:
        tokens: Secuencia COMPLETA normalizada (flags en forma canonica +
            valores + posicionales), en el mismo orden logico que traian los
            argumentos originales, lista para anteponerle el binario (y los
            flags forzados) y ejecutar.
        rutas_a_confinar: Subconjunto de `tokens` que debe pasar por el
            confinamiento a las raices autorizadas: TODOS los argumentos
            posicionales y, ademas, el valor de cualquier flag con
            `es_ruta=True`.
        flags_vistos: Nombres CANONICOS de los flags efectivamente emitidos
            EN POSICION DE FLAG (nunca como valor de otro flag, ni como
            argumento posicional, ni como dato tras `--`). Es la fuente
            correcta para deduplicar `flags_forzados`: `tokens` es la
            secuencia PLANA de todo (nombres, valores, posicionales) y
            comparar contra ella deja que un literal como `--user` colado
            como VALOR (`--grep --user`) o como posicional (`-- --user`)
            cuente como si el flag ya estuviera puesto, impidiendo que el
            Arquitecto lo inyecte (H-1, tercera auditoria).
    """

    tokens: list[str]
    rutas_a_confinar: list[str]
    n_posicionales: int
    flags_vistos: frozenset[str] = frozenset()


def _expandir_largo(
    nombre_parte: str, largos: dict[str, EspecFlag],
) -> tuple[str | None, str | None]:
    """Resuelve `nombre_parte` (p. ej. '--fil') al nombre CANONICO completo
    de un flag largo permitido, expandiendo abreviaturas SOLO contra
    `largos` (la lista de flags YA permitidos para este binario/subcomando,
    nunca contra el catalogo completo del binario real): un flag no incluido
    aqui no se puede alcanzar ni siquiera con el nombre completo, y mucho
    menos con una abreviatura.

    Returns:
        (nombre_canonico, None) si se resuelve sin ambiguedad; (None, motivo)
        si no coincide con ninguno o si coincide con mas de uno (abreviatura
        ambigua: se rechaza igual que lo haria `getopt_long`).
    """
    if len(nombre_parte) <= 2:
        return None, f"flag largo vacio o invalido '{nombre_parte}'"
    if nombre_parte in largos:
        return nombre_parte, None
    candidatos = sorted(k for k in largos if k.startswith(nombre_parte))
    if not candidatos:
        return None, f"flag desconocido '{nombre_parte}'"
    if len(candidatos) > 1:
        return None, (
            f"abreviatura ambigua '{nombre_parte}' (coincide con {candidatos})"
        )
    return candidatos[0], None


def _analizar_flags(
    binario: str, argumentos: list, tabla: TablaFlags,
) -> tuple[_Analisis | None, str | None]:
    """Normaliza y valida `argumentos` contra `tabla`. Ver `_Analisis`.

    Desagrupa flags cortos pegados, expande abreviaturas de flags largos,
    separa flag/valor de forma uniforme y comprueba el limite de argumentos
    posicionales. Cualquier flag que no figure en `tabla` (ni exacto ni por
    abreviatura no ambigua) se rechaza: default-deny tambien en flags.
    Soporta el separador POSIX `--` (fin de flags): todo lo posterior se
    trata como posicional aunque empiece por '-'. Registra ademas, en
    `flags_vistos`, el nombre canonico de cada flag que se emite EN POSICION
    DE FLAG (nunca un valor ni un posicional): es la base para deduplicar
    `flags_forzados` sin el fallo de la tercera auditoria (H-1).
    """
    tokens = list(argumentos)
    salida: list[str] = []
    rutas: list[str] = []
    vistos: set[str] = set()
    n_pos = 0
    solo_posicionales = False
    i = 0
    while i < len(tokens):
        tok = tokens[i]

        if not solo_posicionales and tok == "--":
            salida.append(tok)
            solo_posicionales = True
            i += 1
            continue

        if (
            not solo_posicionales
            and tabla.acepta_numero_corto
            and _NUMERO_CORTO_RE.match(tok)
        ):
            # Idioma historico `-NUM` (`head -20`, `git log -5`): se emite
            # integro, SIN desagrupar digito a digito (H-2, tercera
            # auditoria). No lleva valor propio ni cuenta como posicional.
            salida.append(tok)
            vistos.add(tok)
            i += 1
            continue

        if not solo_posicionales and tok.startswith("--") and len(tok) > 2:
            if "=" in tok:
                nombre_parte, valor_pegado = tok.split("=", 1)
            else:
                nombre_parte, valor_pegado = tok, None
            nombre, motivo = _expandir_largo(nombre_parte, tabla.largos)
            if motivo is not None:
                return None, f"{binario}: {motivo}"
            spec = tabla.largos[nombre]
            vistos.add(nombre)
            if spec.lleva_valor:
                if valor_pegado is not None:
                    valor = valor_pegado
                else:
                    i += 1
                    if i >= len(tokens):
                        return None, (
                            f"{binario}: el flag '{nombre}' requiere un valor"
                        )
                    valor = tokens[i]
                salida.append(nombre)
                salida.append(valor)
                if spec.es_ruta:
                    rutas.append(valor)
            else:
                if valor_pegado is not None:
                    return None, (
                        f"{binario}: el flag '{nombre}' no admite valor "
                        f"('{tok}')"
                    )
                salida.append(nombre)
            i += 1
            continue

        if not solo_posicionales and tok.startswith("-") and len(tok) > 1 and tok[1] != "-":
            j = 1
            while j < len(tok):
                corto = "-" + tok[j]
                spec = tabla.cortos.get(corto)
                if spec is None:
                    return None, (
                        f"{binario}: flag corto desconocido '{corto}' "
                        f"(en '{tok}')"
                    )
                vistos.add(corto)
                if spec.lleva_valor:
                    resto = tok[j + 1:]
                    if resto:
                        valor = resto
                    else:
                        i += 1
                        if i >= len(tokens):
                            return None, (
                                f"{binario}: el flag '{corto}' requiere un "
                                f"valor"
                            )
                        valor = tokens[i]
                    salida.append(corto)
                    salida.append(valor)
                    if spec.es_ruta:
                        rutas.append(valor)
                    break
                salida.append(corto)
                j += 1
            i += 1
            continue

        # Argumento posicional.
        salida.append(tok)
        rutas.append(tok)
        n_pos += 1
        i += 1

    if tabla.max_posicionales is not None and n_pos > tabla.max_posicionales:
        return None, (
            f"{binario}: no se admiten {n_pos} argumento(s) posicional(es) "
            f"aqui (maximo {tabla.max_posicionales})"
        )

    return _Analisis(
        tokens=salida, rutas_a_confinar=rutas, n_posicionales=n_pos,
        flags_vistos=frozenset(vistos),
    ), None


def _analizar_comando(
    binario: str, argumentos: list, politica: PoliticaComando,
) -> tuple[_Analisis | None, str | None]:
    """Resuelve el subcomando (si `politica` lo exige) y delega el resto en
    `_analizar_flags`. Fuente UNICA que comparten `validar_forma_comando`
    (forma + argv final) y `preparar_comando` (rutas a confinar): ambos
    llaman a esta misma funcion sobre la MISMA tabla, nunca a dos
    implementaciones distintas.
    """
    if politica.subcomandos is not None:
        if not argumentos:
            return None, f"{binario}: falta el subcomando"
        primero = argumentos[0]
        if not isinstance(primero, str):
            return None, f"{binario}: el subcomando debe ser string"
        if _es_flag(primero):
            return None, (
                f"{binario}: no se admiten flags antes del subcomando "
                f"(visto '{primero}')"
            )
        if primero not in politica.subcomandos:
            return None, (
                f"{binario}: subcomando '{primero}' no permitido (solo "
                f"lectura: {sorted(politica.subcomandos)})"
            )
        tabla = politica.subcomandos[primero]
        contexto = f"{binario} {primero}"
        analisis_resto, motivo = _analizar_flags(contexto, argumentos[1:], tabla)
        if motivo is not None:
            return None, motivo
        return _Analisis(
            tokens=[primero, *analisis_resto.tokens],
            rutas_a_confinar=analisis_resto.rutas_a_confinar,
            n_posicionales=analisis_resto.n_posicionales,
            flags_vistos=analisis_resto.flags_vistos,
        ), None

    tabla = politica.tabla if politica.tabla is not None else TablaFlags()
    return _analizar_flags(binario, argumentos, tabla)


def validar_forma_comando(
    binario: str, argumentos: list,
) -> tuple[bool, str, list[str] | None]:
    """Valida la FORMA de un comando (sin tocar el FS ni necesitar cwd).

    Comprueba: binario en la allowlist; argumentos string sin metacaracteres,
    sin NUL, dentro de longitud y numero; subcomando valido (si aplica) y sin
    flags antes de el; CADA flag pertenece a la tabla permitida de ese
    binario/subcomando (exacto o por abreviatura NO ambigua, tras desagrupar
    flags cortos pegados); limite de argumentos posicionales respetado; sin
    segmentos '..' (tanto en el token crudo como, de forma independiente, en
    cada valor/posicional ya separado, para cerrar el hueco de
    `--flag=../x`); flags forzados no anulables por uno incompatible.

    Returns:
        (ok, motivo, argv_final). `argv_final` incluye el binario y los flags
        forzados (deduplicados) seguidos de los argumentos YA normalizados
        (flags expandidos a su forma canonica, valores separados). None si
        no valida.
    """
    politica = binario_permitido(binario)
    if politica is None:
        return False, f"binario '{binario}' no esta en la allowlist", None

    if not isinstance(argumentos, list):
        return False, f"{binario}: 'argumentos' debe ser una lista", None

    for tok in argumentos:
        if not isinstance(tok, str):
            return False, f"{binario}: cada argumento debe ser string", None
        motivo = _validar_token_crudo(binario, tok)
        if motivo is not None:
            return False, motivo, None

    analisis, motivo = _analizar_comando(binario, argumentos, politica)
    if motivo is not None:
        return False, motivo, None

    # Segunda pasada de '..' sobre los valores YA separados de sus flags:
    # cierra el hueco de `--flag=../x` (el token crudo completo no siempre
    # produce un segmento '..' exacto tras el '=', pero el valor aislado si).
    for tok in analisis.rutas_a_confinar:
        if _tiene_dotdot(tok):
            return False, f"{binario}: valor '{tok}' contiene '..'", None

    if politica.flags_incompatibles_forzados:
        for tok in analisis.tokens:
            if tok in politica.flags_incompatibles_forzados:
                return False, (
                    f"{binario}: flag '{tok}' es incompatible con los "
                    f"flags forzados de este binario"
                ), None
    for tok in analisis.tokens:
        if tok in _FLAGS_CATASTROFICOS_GLOBAL:
            return False, f"{binario}: flag prohibido '{tok}'", None

    # Dedup contra `flags_vistos` (flags REALMENTE emitidos en posicion de
    # flag), NUNCA contra `analisis.tokens` (la secuencia plana, que tambien
    # contiene valores y posicionales): comparar contra `tokens` permitia que
    # un `--user` colado como VALOR de otro flag (`--grep --user`) o como
    # posicional tras `--` hiciera creer que el flag forzado ya estaba
    # puesto, y el Arquitecto dejaba de inyectarlo (H-1, tercera auditoria).
    forzados = [f for f in politica.flags_forzados if f not in analisis.flags_vistos]
    argv_final = [binario, *forzados, *analisis.tokens]

    # El limite se comprueba sobre el argv FINAL (normalizado y con los flags
    # forzados ya inyectados), no solo sobre lo que propuso el cerebro.
    n_args_final = len(argv_final) - 1
    if n_args_final > MAX_ARGS_POR_COMANDO:
        return False, (
            f"{binario}: demasiados argumentos ({n_args_final} > "
            f"{MAX_ARGS_POR_COMANDO})"
        ), None

    return True, "", argv_final


def _confinar_rutas(tokens: list[str], *, cwd: Path) -> tuple[bool, str]:
    """Confina a las raices autorizadas cada token de `tokens` (ya extraidos
    por `_analizar_comando`: argumentos posicionales y valores de flags con
    `es_ruta=True`), siguiendo symlinks (ver
    `ingenieria.resolver_directorio_autorizado`), para que un enlace
    simbolico dentro de una raiz autorizada que apunte fuera de ella no pase
    la validacion:

      - rutas EXPLICITAS ('/...' o '~...'): se resuelven contra las raices
        autorizadas (sigue symlinks, denylist de sensibles incluida);
      - tokens RELATIVOS (sin '..', ya garantizado por la forma): no se les
        aplica la heuristica de nombres sensibles (pensada solo para rutas
        explicitas, para no rechazar por error un argumento que simplemente
        se LLAME como un fichero sensible), pero SI se comprueba que no sean
        (ni contengan) un symlink que escape de las raices autorizadas.

    Esto cubre TANTO argumentos posicionales COMO el valor de un flag que lo
    lleve pegado, con '=', separado por espacio, absoluto o relativo: todos
    llegan aqui ya identificados y separados por `_analizar_comando`.
    """
    for tok in tokens:
        if _es_token_ruta_explicita(tok):
            _resuelto, motivo = ingenieria.resolver_directorio_autorizado(tok, cwd)
            if motivo is not None:
                return False, f"ruta '{tok}': {motivo}"
            continue
        motivo = ingenieria.ruta_relativa_escapa_por_symlink(tok, cwd)
        if motivo is not None:
            return False, motivo
    return True, ""


# -- Etiqueta honesta de confirmacion (defensa en profundidad, PI-2) -----------


def comando_puede_escribir(argv: list[str]) -> bool:
    """True si `argv` (ya preparado: incluye el binario en la posicion 0 y,
    si los tuviera, los flags forzados) puede escribir/mutar algo.

    DERIVADA de la MISMA tabla que `validar_forma_comando` (nunca una lista
    aparte que pueda divergir de ella): vuelve a correr la validacion
    completa sobre `argv[1:]` con `argv[0]` como binario. Si el binario no
    esta en la allowlist, o CUALQUIER flag/subcomando/limite de posicionales
    no encaja con su tabla, se considera "puede escribir" (fallo seguro: no
    se afirma "solo lectura" de algo que no se pudo verificar). Si valida
    limpio, por construccion de la whitelist (ningun flag de escritura
    figura en ninguna tabla, y los subcomandos DUALES tienen su limite de
    posicionales en la MISMA tabla) no puede escribir.

    Uso: SOLO para no mentir en el texto de confirmacion humana
    (`seguridad.evaluar_comandos`). No sustituye a la validacion real
    (`validar_forma_comando` ya bloquea cualquier cosa que esta funcion
    marcara como escritura antes de que llegue al ejecutor): es una segunda
    comprobacion, deliberadamente redundante con ella, pero LEYENDO LA
    MISMA estructura de datos.

    Nota de implementacion: `argv` ya trae los flags forzados (`--no-pager`,
    `--user`, ...) inyectados ANTES del subcomando (asi los prepara
    `validar_forma_comando`). Para un binario con subcomandos, esa MISMA
    validacion exige que el primer token tras el binario sea el subcomando
    (sin flags delante); si se re-validara `argv[1:]` tal cual, un flag
    forzado legitimo haria fallar la re-validacion de un comando que en
    realidad es de solo lectura. Por eso se filtran aqui ANTES de
    re-validar: se pregunta "¿validaria esto si el cerebro lo hubiera
    propuesto sin los flags que el Arquitecto inyecta por su cuenta?".
    """
    if not argv:
        return True
    binario, *resto = argv
    politica = binario_permitido(binario)
    if politica is not None and politica.flags_forzados:
        resto = [tok for tok in resto if tok not in politica.flags_forzados]
    ok, _motivo, _argv = validar_forma_comando(binario, resto)
    return not ok


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
    autorizadas y confina las rutas (posicionales y valores de flags
    `es_ruta=True`) de los argumentos, reutilizando el MISMO analisis de
    `validar_forma_comando` (no re-implementa la deteccion de flags/valores
    por su cuenta).

    Returns:
        (argv, directorio, None) si es ejecutable; (None, None, motivo) si se
        bloquea.
    """
    base = cwd_base if cwd_base is not None else directorio_base_por_defecto()

    binario = cmd.get("binario")
    argumentos = cmd.get("argumentos") or []

    politica = binario_permitido(binario)
    ok, motivo, argv = validar_forma_comando(binario, argumentos)
    if not ok or argv is None:
        return None, None, motivo

    directorio, motivo = ingenieria.resolver_directorio_autorizado(
        cmd.get("directorio"), base,
    )
    if motivo is not None:
        return None, None, motivo

    # `politica` no puede ser None aqui: `validar_forma_comando` ya confirmo
    # que el binario esta en la allowlist. Se re-analiza (misma funcion que
    # ya uso `validar_forma_comando`) solo para obtener las rutas a confinar.
    analisis, motivo_interno = _analizar_comando(binario, argumentos, politica)
    if motivo_interno is not None or analisis is None:
        return None, None, motivo_interno or f"{binario}: analisis inesperado"

    ok, motivo = _confinar_rutas(analisis.rutas_a_confinar, cwd=directorio)
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
