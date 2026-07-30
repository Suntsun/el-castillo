"""
Politica del modo de ingenieria gobernada del Arquitecto (decision
`delegar_ingenieria`).

Define las RAICES_AUTORIZADAS bajo las que el modo puede actuar, la denylist
de rutas SENSIBLES (que se bloquean SIEMPRE, aunque caigan bajo una raiz), el
mapeo perfil -> agente OpenCode y los resolutores PUROS que comparten
`seguridad` (para el veredicto) y `ejecutor` (para el lanzamiento), de modo
que ambos usen exactamente la misma resolucion.

ALCANCE PI-0/PI-1: dos perfiles activos.
  - 'explorar' (PI-0, solo lectura): OpenCode lee, busca y lista dentro de un
    directorio autorizado con el agente `ingeniero-lectura`, SIN bash, SIN
    edicion, SIN red de sistema y SIN skills.
  - 'editar' (PI-1, lectura + edicion confinada): OpenCode lee y ademas
    edita/crea ficheros dentro del directorio autorizado con el agente
    `ingeniero-codigo`, igualmente SIN bash, SIN red de sistema y SIN skills.
    La escritura queda confinada al mismo directorio autorizado (--dir) que la
    lectura, con el mismo modelo de confinamiento logico que 'explorar'.
El perfil 'comandos' (PI-2, bash) NO existe todavia: mientras no este en
PERFIL_AGENTE, el validador lo rechaza (fallo seguro).

Este modulo NO ejecuta nada. La normalizacion de '..' sigue siendo LEXICA
(sin tocar el FS), igual que `seguridad._ruta_dentro_de`. Pero la resolucion
final SI sigue symlinks (realpath, no estricto) y RECONFINA el resultado
contra las raices autorizadas: normalizar solo '..' no basta, porque un
enlace simbolico creado DENTRO de una raiz autorizada puede apuntar a
cualquier destino FUERA de ella sin que ningun '..' aparezca en la ruta. La
resolucion es tolerante con rutas que aun no existen (no falla si el destino
final no esta creado todavia), para no depender de que existan en tiempo de
evaluacion.
"""

from __future__ import annotations

import os
import re
from pathlib import Path


# -- Raices autorizadas --------------------------------------------------------

# Unicas carpetas bajo las que el modo de ingenieria puede mirar. NO se
# autoriza `~/.config` entero: solo subcarpetas concretas.
_RAICES_CRUDAS: tuple[str, ...] = (
    "~/Escritorio/automatizaciones",
    "~/Escritorio/proyectos",
    "~/repos",
    "~/.config/omarchy",
    "~/.config/waybar",
    "~/.config/hypr",
    "~/.config/walker",
)


def raices_autorizadas() -> tuple[Path, ...]:
    """Raices autorizadas como rutas absolutas expandidas.

    No se resuelven symlinks: la comparacion es lexica, coherente con el
    resto de la capa de seguridad.
    """
    return tuple(Path(r).expanduser() for r in _RAICES_CRUDAS)


# -- Denylist de rutas sensibles ----------------------------------------------

# Carpetas cuyo contenido es sensible (credenciales, claves, config de
# OpenCode). Se bloquean aunque cuelguen de una raiz autorizada.
_DIRS_SENSIBLES: tuple[str, ...] = (
    "~/.ssh",
    "~/.gnupg",
    "~/.config/opencode",
    "~/.aws",
    "~/.config/gh",
    "/etc",
)
# Sufijos de nombre que delatan secretos.
_SUFIJOS_SENSIBLES: tuple[str, ...] = (".env", ".key", ".pem")
# Nombres base (o prefijos) de claves privadas habituales.
_NOMBRES_SENSIBLES: tuple[str, ...] = (
    "id_rsa", "id_ed25519", "id_dsa", "id_ecdsa",
)


def _dirs_sensibles() -> tuple[Path, ...]:
    return tuple(Path(d).expanduser() for d in _DIRS_SENSIBLES)


# -- Perfiles -> agente OpenCode ----------------------------------------------

# Agente OpenCode de SOLO LECTURA para el perfil 'explorar'. Definido en
# ~/.config/opencode/agent/ingeniero-lectura.md (bash/edit/write/red/skill
# denegados).
AGENTE_INGENIERIA_LECTURA = "ingeniero-lectura"

# Agente OpenCode de LECTURA + EDICION confinada para el perfil 'editar'
# (PI-1). Definido en ~/.config/opencode/agent/ingeniero-codigo.md: read +
# edit/write/patch permitidos; bash/red/skill DENEGADOS. La escritura queda
# acotada al directorio autorizado via --dir, igual que la lectura.
AGENTE_INGENIERIA_CODIGO = "ingeniero-codigo"

# PI-0: 'explorar'. PI-1: 'editar'. 'comandos' (PI-2, bash) se anadira aqui
# cuando exista su agente; hasta entonces el validador lo rechaza.
PERFIL_AGENTE: dict[str, str] = {
    "explorar": AGENTE_INGENIERIA_LECTURA,
    "editar": AGENTE_INGENIERIA_CODIGO,
}

# Indica si un perfil puede ESCRIBIR (editar/crear ficheros) dentro del
# directorio autorizado. Fuente unica para que seguridad/ejecutor/repl
# describan los permisos sin hardcodear "solo lectura". 'editar' escribe;
# 'explorar' no.
PERFIL_ESCRIBE: dict[str, bool] = {
    "explorar": False,
    "editar": True,
}


def perfil_escribe(perfil: str | None) -> bool:
    """True si el perfil delega ESCRITURA confinada (no solo lectura)."""
    return bool(PERFIL_ESCRIBE.get(perfil, False)) if isinstance(perfil, str) else False


# -- Helpers puros -------------------------------------------------------------


def _es_subruta(candidata: Path, base: Path) -> bool:
    """True si `candidata` es `base` o cuelga de ella (lexico, absoluto)."""
    if not candidata.is_absolute() or not base.is_absolute():
        return False
    cand = candidata.parts
    bas = base.parts
    if len(cand) < len(bas):
        return False
    return cand[: len(bas)] == bas


def _normalizar(ruta: Path) -> Path:
    """Normaliza '..' y '.' de forma lexica, SIN tocar el filesystem."""
    return Path(os.path.normpath(str(ruta)))


def _resolver_real(ruta: Path) -> Path:
    """Resuelve symlinks (realpath) de forma NO estricta.

    Sigue todo enlace simbolico que exista a lo largo de la ruta, pero no
    falla si la ruta (o su tramo final) no existe todavia: eso permite
    seguir soportando el caso legitimo de una ruta que se va a crear.

    Sin esto, `_normalizar` (puramente lexica) no detecta que un enlace
    simbolico DENTRO de una raiz autorizada apunta a un destino FUERA de
    ella: normaliza '..' pero nunca sigue el enlace. Tolerante: si la
    resolucion falla (p. ej. un bucle de symlinks o un error de permisos),
    se devuelve la normalizacion lexica; el resultado sigue pasando por la
    comprobacion de raices igualmente (fallo seguro: en el peor caso se
    trata como si no hubiera symlink que seguir, igual que antes de este
    endurecimiento).
    """
    try:
        return ruta.resolve(strict=False)
    except (OSError, RuntimeError):
        return _normalizar(ruta)


def ruta_es_sensible(ruta: Path) -> bool:
    """True si `ruta` cae en una carpeta sensible o su nombre delata un
    secreto (`.env`, `.key`, `.pem`, claves privadas)."""
    abs_ruta = _normalizar(Path(ruta).expanduser())
    for sens in _dirs_sensibles():
        if _es_subruta(abs_ruta, _normalizar(sens)):
            return True
    nombre = abs_ruta.name.lower()
    if any(nombre.endswith(suf) for suf in _SUFIJOS_SENSIBLES):
        return True
    if any(nombre == n or nombre.startswith(n + ".") for n in _NOMBRES_SENSIBLES):
        return True
    return False


def rutas_sensibles_en_texto(texto: str) -> list[str]:
    """Tokens path-like de `texto` (lenguaje natural) que apuntan a rutas
    sensibles. Heuristica conservadora: solo mira tokens que parecen ruta
    absoluta ('/') o de HOME ('~')."""
    ofensivas: list[str] = []
    for crudo in re.split(r"\s+", texto or ""):
        token = crudo.strip("\"'`,;:!?()[]{}<>")
        if not token:
            continue
        if not (token.startswith("/") or token.startswith("~")):
            continue
        if ruta_es_sensible(Path(token)):
            ofensivas.append(token)
    return ofensivas


# -- Resolutores (fuente unica para seguridad y ejecutor) ----------------------


def resolver_directorio_autorizado(
    directorio: str | None, cwd: Path,
) -> tuple[Path | None, str | None]:
    """Resuelve el directorio objetivo de una delegacion de ingenieria.

    Si `directorio` es None se usa `cwd`. La ruta (expandida, '..' colapsado
    de forma lexica) debe:
      - ser absoluta;
      - NO ser una ruta sensible (tanto la forma lexica como, tras seguir
        symlinks, su destino real);
      - caer dentro de alguna RAIZ autorizada, TANTO en su forma lexica
        COMO tras resolver symlinks (realpath no estricto). Un enlace
        simbolico creado dentro de una raiz autorizada que apunte fuera de
        ella se bloquea aqui: normalizar '..' no lo detecta porque el
        enlace no necesita ningun '..' en su ruta.

    Returns:
        (dir_resuelto, None) si es valido; (None, motivo) si se bloquea.
        `dir_resuelto` es la forma lexica (no la resuelta), para que el
        resultado sea legible y estable de cara al usuario/trazas.
    """
    if directorio is not None:
        base = Path(directorio).expanduser()
        if not base.is_absolute():
            base = cwd / base
        candidata = base
    else:
        candidata = cwd

    candidata = _normalizar(candidata)

    if not candidata.is_absolute():
        return None, f"directorio no resoluble a ruta absoluta: {directorio!r}"

    if ruta_es_sensible(candidata):
        return None, f"directorio sensible, acceso denegado: {candidata}"

    real = _resolver_real(candidata)
    if ruta_es_sensible(real):
        return None, (
            f"directorio sensible tras resolver symlinks: {real} "
            f"(enlace en {candidata})"
        )

    for raiz in raices_autorizadas():
        raiz_norm = _normalizar(raiz)
        if _es_subruta(candidata, raiz_norm) and _es_subruta(real, raiz_norm):
            return candidata, None

    if any(_es_subruta(candidata, _normalizar(r)) for r in raices_autorizadas()):
        # Cae dentro de una raiz de forma lexica, pero su destino REAL (tras
        # symlinks) escapa de todas las raices: escape por enlace simbolico.
        return None, (
            f"directorio '{candidata}' escapa de las raices autorizadas via "
            f"symlink (destino real: {real})"
        )

    return None, f"directorio fuera de las raices autorizadas: {candidata}"


def ruta_relativa_escapa_por_symlink(token: str, cwd: Path) -> str | None:
    """Para un token RELATIVO (ya sin '..', y que NO es una ruta explicita
    '/'/'~'), comprueba que no sea (ni cuelgue de) un enlace simbolico que
    escape de las raices autorizadas al resolverlo bajo `cwd`.

    A proposito NO aplica `ruta_es_sensible` (la heuristica de nombres:
    `.env`, `id_rsa`, etc.) a este token: esa heuristica esta pensada para
    rutas explicitas, y aplicarla aqui rechazaria por error argumentos que
    simplemente se LLAMEN como un fichero sensible sin serlo (p. ej. un
    patron de busqueda `grep id_rsa .`). Solo se rechaza si de verdad hay un
    symlink cuyo destino real escapa de todas las raices.

    Returns:
        motivo (str) si escapa; None si es seguro. Un token que no exista en
        el filesystem no tiene symlink que seguir y por tanto se considera
        seguro (se comporta igual que antes de este endurecimiento).
    """
    candidata = _normalizar(cwd / token)
    real = _resolver_real(candidata)
    if real == candidata:
        return None
    for raiz in raices_autorizadas():
        if _es_subruta(real, _normalizar(raiz)):
            return None
    return (
        f"argumento '{token}': es (o cuelga de) un enlace simbolico que "
        f"escapa de las raices autorizadas (destino real: {real})"
    )


def resolver_ejecucion_ingenieria(
    decision_norm: dict, cwd: Path,
) -> tuple[str | None, Path | None, str | None]:
    """Fuente UNICA de (agente, directorio, motivo_bloqueo) para una decision
    `delegar_ingenieria` ya validada. La usan `seguridad` (veredicto) y
    `ejecutor` (lanzamiento) para no divergir.

    Returns:
        (agente, directorio, None) si es ejecutable; (None, None, motivo) si
        se bloquea (perfil no soportado o directorio no autorizado).
    """
    perfil = decision_norm.get("perfil")
    agente = PERFIL_AGENTE.get(perfil) if isinstance(perfil, str) else None
    if agente is None:
        return None, None, f"perfil de ingenieria no soportado: {perfil!r}"

    directorio, motivo = resolver_directorio_autorizado(
        decision_norm.get("directorio"), cwd,
    )
    if motivo is not None:
        return None, None, motivo
    return agente, directorio, None
