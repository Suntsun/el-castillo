"""
Capa de seguridad del Arquitecto del Castillo (Fase 3).

Esta unidad decide SI una invocacion ya validada por `validador.py` puede
ejecutarse y BAJO QUE condiciones. NO ejecuta nada: solo emite un
`Veredicto`. El `ejecutor.py` lo consume y obedece.

Division de responsabilidades:

    validador.py   -> ¿la decision del cerebro cumple el contrato JSON y
                      resuelve contra el registro? (forma)
    seguridad.py   -> dada una invocacion YA valida, ¿es seguro lanzarla
                      ahora y necesita confirmacion? (politica)
    ejecutor.py    -> lanzar el subprocess de forma aislada. (accion)

Las comprobaciones de `seguridad.py` son en buena parte DEFENSA EN
PROFUNDIDAD: el validador ya bloquea sudo, `bloquea_terminal` y
metacaracteres de shell, pero esta capa vuelve a comprobarlo porque es la
ultima barrera antes de tocar el sistema. Si el validador y esta capa
discrepan, gana el rechazo: nunca se ejecuta algo dudoso.

API publica:
    evaluar_invocacion(invocacion_norm, manifiesto) -> Veredicto

Nunca lanza desde la API publica; los problemas se devuelven en el
`Veredicto` (campo `permitido` + `motivo_bloqueo`).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from arquitecto import ingenieria
from arquitecto import comandos

if TYPE_CHECKING:
    from arquitecto.registro import Manifiesto


# -- Constantes ----------------------------------------------------------------

# Mismo conjunto que el validador. Se repite aqui a proposito (defensa en
# profundidad): esta capa no debe depender de que el validador ya lo filtrase.
_METACARACTERES_SHELL: frozenset[str] = frozenset({
    ";", "|", "&", "`", "$", ">", "<", "\n", "\r", "\\",
})

# Niveles de peligrosidad que SIEMPRE exigen confirmacion, aunque el manifiesto
# declare requiere_confirmacion=false (suelo duro, defensa en profundidad). Para
# el resto de niveles se HONRA el campo requiere_confirmacion del manifiesto: el
# autor de cada manifiesto decide por operacion (mostrar/listar/descargar... no
# preguntan; borrar/apagar/activar... si). Asi el campo deja de ser letra muerta.
_PELIGROSIDAD_CONFIRMACION_FORZOSA: frozenset[str] = frozenset({"destructiva"})

# -- Delegacion a OpenCode (decision delegar_opencode) -------------------------
# Agentes OpenCode restringidos (definidos en ~/.config/opencode/agent/).
AGENTE_DELEGACION_LECTURA = "arquitecto-lectura"
AGENTE_DELEGACION_ESCRITURA = "arquitecto-escritura"

# Sandbox por defecto para las delegaciones de ESCRITURA: una carpeta
# dedicada bajo el HOME. Ninguna tarea de escritura toca nada fuera de aqui
# sin que el usuario mueva luego los ficheros a mano.
SANDBOX_ESCRITURA = str(Path.home() / "arqui-sandbox")


# -- Resultado -----------------------------------------------------------------


@dataclass(frozen=True)
class Veredicto:
    """Dictamen de seguridad sobre una invocacion concreta.

    Attributes:
        permitido: True si la invocacion puede llegar al ejecutor. Si es
            False, `motivo_bloqueo` explica por que y el ejecutor NO debe
            lanzar nada.
        requiere_confirmacion: True si, aun permitida, el ejecutor debe
            obtener un OK explicito del usuario antes de lanzar.
        requiere_red: True si la operacion necesita conectividad; el
            ejecutor debe verificarla justo antes de lanzar.
        motivo_bloqueo: Texto del motivo si `permitido` es False; None si
            esta permitida.
        avisos: Advertencias no bloqueantes (p. ej. un servicio systemd
            recomendado que no esta activo). El ejecutor las propaga al
            usuario pero no impiden la ejecucion.
        texto_confirmacion: Resumen legible de lo que se va a ejecutar,
            pensado para mostrarse en el prompt de confirmacion.
    """

    permitido: bool
    requiere_confirmacion: bool
    requiere_red: bool
    motivo_bloqueo: str | None = None
    avisos: tuple[str, ...] = ()
    texto_confirmacion: str = ""


# -- Helpers privados ----------------------------------------------------------


def _bloqueo(motivo: str) -> Veredicto:
    """Construye un Veredicto de rechazo limpio."""
    return Veredicto(
        permitido=False,
        requiere_confirmacion=False,
        requiere_red=False,
        motivo_bloqueo=motivo,
    )


def _primer_metacaracter(valor: str) -> str | None:
    for ch in valor:
        if ch in _METACARACTERES_SHELL:
            return ch
    return None


def _ruta_dentro_de(candidata: str, protegida: str) -> bool:
    """True si `candidata` es la ruta protegida o cuelga de ella.

    Comparacion puramente lexica sobre rutas absolutas normalizadas; NO
    toca el filesystem (no resuelve symlinks) para no depender de que las
    rutas existan en tiempo de evaluacion.
    """
    try:
        cand = Path(candidata)
        prot = Path(protegida)
    except (TypeError, ValueError):
        return False
    if not cand.is_absolute() or not prot.is_absolute():
        # Solo razonamos sobre rutas absolutas; las relativas ya las
        # rechaza el validador (no admite '..' ni metacaracteres).
        return False
    cand_partes = cand.parts
    prot_partes = prot.parts
    if len(cand_partes) < len(prot_partes):
        return False
    return cand_partes[: len(prot_partes)] == prot_partes


def _verificar_dependencias(
    manifiesto: "Manifiesto",
) -> tuple[str | None, list[str]]:
    """Comprueba las dependencias declaradas.

    Returns:
        (motivo_bloqueo, avisos). `motivo_bloqueo` no es None si falta algo
        sin lo cual la operacion fallara con seguridad (un binario o un
        fichero de config). Los servicios systemd inactivos solo generan
        avisos: la operacion podria funcionar igualmente.
    """
    avisos: list[str] = []
    dep = manifiesto.dependencias

    faltan_binarios = [b for b in dep.binarios if shutil.which(b) is None]
    if faltan_binarios:
        return (
            f"faltan binarios requeridos en PATH: {faltan_binarios}",
            avisos,
        )

    # Usamos exists() y no is_file(): el esquema define ficheros_config como
    # "rutas que deben existir", sin distinguir tipo, por lo que un directorio
    # de configuracion (p.ej. invocador_entorno/modos) es una dependencia
    # legitima. exists() sigue al symlink, asi que un enlace roto cuenta como
    # ausente y se bloquea igual: la garantia de seguridad se mantiene.
    faltan_config = [
        f for f in dep.ficheros_config if not Path(f).expanduser().exists()
    ]
    if faltan_config:
        return (
            f"faltan ficheros de configuracion requeridos: {faltan_config}",
            avisos,
        )

    for unidad in dep.servicios_systemd:
        if not _servicio_activo(unidad):
            avisos.append(
                f"servicio systemd '{unidad}' no esta activo; la operacion "
                f"podria no comportarse como se espera"
            )

    return None, avisos


def _servicio_activo(unidad: str) -> bool:
    """True si la unidad systemd-user esta activa.

    Tolerante: ante cualquier error (systemctl ausente, timeout) devuelve
    True para NO bloquear por una comprobacion auxiliar que falla. La
    ausencia de systemctl no debe impedir ejecutar un wrapper CLI.
    """
    if shutil.which("systemctl") is None:
        return True
    try:
        res = subprocess.run(
            ["systemctl", "--user", "is-active", unidad],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    return res.stdout.strip() == "active"


# -- API publica ---------------------------------------------------------------


def evaluar_invocacion(
    invocacion_norm: dict,
    manifiesto: "Manifiesto",
) -> Veredicto:
    """Evalua la seguridad de UNA invocacion ya validada.

    Args:
        invocacion_norm: Sub-dict normalizado por `validador.py` para una
            invocacion (el cuerpo de `invocar`, de
            `pedir_confirmacion.invocacion` o de un paso de `componer`).
            Debe contener al menos `clave_automatizacion`,
            `nombre_operacion`, `argumentos`, `peligrosidad_efectiva`,
            `requiere_confirmacion` y `bloquea_terminal`.
        manifiesto: El Manifiesto correspondiente a
            `invocacion_norm['clave_automatizacion']`.

    Returns:
        Veredicto. Nunca lanza.
    """
    clave = invocacion_norm.get("clave_automatizacion", "?")
    nombre_op = invocacion_norm.get("nombre_operacion", "?")

    operacion = manifiesto.operacion(nombre_op)
    if operacion is None:
        return _bloqueo(
            f"operacion '{nombre_op}' no existe en manifiesto '{clave}' "
            f"(seguridad)"
        )

    # 1) Bloqueos duros (defensa en profundidad sobre el validador).
    if operacion.bloquea_terminal:
        return _bloqueo(
            f"operacion '{nombre_op}' bloquea la terminal; debe lanzarla el "
            f"usuario manualmente"
        )
    if manifiesto.seguridad.requiere_sudo:
        return _bloqueo(
            f"automatizacion '{clave}' requiere sudo; fuera del flujo "
            f"automatico del Arquitecto"
        )

    # 2) Re-escaneo de metacaracteres de shell en cada valor de argumento.
    argumentos = invocacion_norm.get("argumentos") or {}
    if not isinstance(argumentos, dict):
        return _bloqueo(f"'{clave}.{nombre_op}': argumentos no es objeto")
    for clave_arg, valor in argumentos.items():
        if not isinstance(valor, str):
            return _bloqueo(
                f"'{clave}.{nombre_op}': argumento '{clave_arg}' no es string"
            )
        meta = _primer_metacaracter(valor)
        if meta is not None:
            return _bloqueo(
                f"'{clave}.{nombre_op}': argumento '{clave_arg}' contiene "
                f"metacaracter shell {meta!r}"
            )

    # 3) Rutas protegidas: ningun valor de argumento puede apuntar a una
    #    ruta declarada como protegida en el manifiesto.
    for protegida in manifiesto.seguridad.paths_protegidos:
        for clave_arg, valor in argumentos.items():
            if _ruta_dentro_de(valor, protegida):
                return _bloqueo(
                    f"'{clave}.{nombre_op}': argumento '{clave_arg}'="
                    f"'{valor}' toca ruta protegida '{protegida}'"
                )

    # 4) Dependencias: binarios/config faltantes bloquean; servicios
    #    inactivos solo avisan.
    motivo_dep, avisos = _verificar_dependencias(manifiesto)
    if motivo_dep is not None:
        return _bloqueo(f"'{clave}.{nombre_op}': {motivo_dep}")

    # 5) Politica de confirmacion: se honra el requiere_confirmacion declarado
    #    en el manifiesto; ademas, las peligrosidades del suelo duro
    #    (destructiva) exigen OK aunque el manifiesto diga lo contrario.
    peligrosidad = invocacion_norm.get(
        "peligrosidad_efectiva", operacion.peligrosidad
    )
    requiere_confirmacion = bool(
        invocacion_norm.get("requiere_confirmacion", False)
    ) or peligrosidad in _PELIGROSIDAD_CONFIRMACION_FORZOSA

    texto = (
        f"{manifiesto.nombre_visible}: {operacion.descripcion} "
        f"[peligrosidad: {peligrosidad}]"
    )

    return Veredicto(
        permitido=True,
        requiere_confirmacion=requiere_confirmacion,
        requiere_red=manifiesto.seguridad.requiere_red,
        motivo_bloqueo=None,
        avisos=tuple(avisos),
        texto_confirmacion=texto,
    )


def resolver_delegacion(ambito: str) -> tuple[str, str]:
    """Mapea el ambito de una delegacion al (agente, directorio) a usar.

    - 'lectura'  -> agente read-only, directorio = cwd actual del REPL.
    - 'escritura' -> agente con edicion, directorio = sandbox dedicado.
    """
    if ambito == "escritura":
        return AGENTE_DELEGACION_ESCRITURA, SANDBOX_ESCRITURA
    return AGENTE_DELEGACION_LECTURA, str(Path.cwd())


# -- Confinamiento del sandbox de escritura (P1.2) -----------------------------

# Aviso fijo que marca toda delegacion como capacidad EXCEPCIONAL: actua
# fuera del catalogo de manifiestos. Se propaga al texto de confirmacion y a
# los avisos del resultado para que quede trazado de forma inequivoca.
AVISO_EXCEPCIONAL = (
    "CAPACIDAD EXCEPCIONAL fuera de manifiestos: el Arquitecto deja actuar a "
    "OpenCode directamente (con confirmacion y sandbox)."
)


def sandbox_escritura() -> Path:
    """Ruta REAL (resuelta) del sandbox de escritura de las delegaciones."""
    return Path(SANDBOX_ESCRITURA).expanduser().resolve()


def _dentro_de(candidata: Path, base: Path) -> bool:
    """True si `candidata` (ya resuelta) es `base` o cuelga de ella."""
    try:
        candidata.relative_to(base)
        return True
    except ValueError:
        return False


def _rutas_que_escapan(tarea: str, sandbox: Path) -> list[str]:
    """Tokens path-like de `tarea` que apuntan FUERA del sandbox.

    Heuristica conservadora: vigila tokens que son ruta absoluta, ruta de
    HOME ('~') o que contienen un segmento '..'. Las rutas relativas sin
    '..' resuelven dentro del sandbox (que es el cwd de la delegacion) y se
    consideran seguras. No pretende cubrir todo (la tarea es lenguaje
    natural), pero bloquea las menciones explicitas peligrosas antes de
    lanzar nada.
    """
    ofensivas: list[str] = []
    for crudo in re.split(r"\s+", tarea or ""):
        # Quitar comillas y puntuacion de frase, pero NO '.' ni '/'.
        token = crudo.strip("\"'`,;:!?()[]{}<>")
        if not token:
            continue
        segmentos = re.split(r"[\\/]+", token)
        es_abs = token.startswith("/") or token.startswith("~")
        tiene_dotdot = ".." in segmentos
        if not (es_abs or tiene_dotdot):
            continue
        ruta = Path(token).expanduser()
        if not ruta.is_absolute():
            ruta = sandbox / ruta
        try:
            resuelta = ruta.resolve()
        except (OSError, RuntimeError, ValueError):
            ofensivas.append(token)
            continue
        if not _dentro_de(resuelta, sandbox):
            ofensivas.append(token)
    return ofensivas


def symlinks_que_escapan(sandbox: Path) -> list[str]:
    """Symlinks DENTRO del sandbox cuyo destino cae FUERA de el.

    Se usa como verificacion previa (estado del sandbox antes de delegar) y
    posterior (efectos tras la delegacion). Tolerante: ante error de acceso
    ignora ese nodo. Si el sandbox no existe, devuelve lista vacia.
    """
    malos: list[str] = []
    if not sandbox.exists():
        return malos
    try:
        nodos = list(sandbox.rglob("*"))
    except OSError:
        return malos
    for nodo in nodos:
        try:
            if not nodo.is_symlink():
                continue
            destino = nodo.resolve()
        except (OSError, RuntimeError, ValueError):
            malos.append(str(nodo))
            continue
        if not _dentro_de(destino, sandbox):
            malos.append(str(nodo))
    return malos


def evaluar_delegacion(decision_norm: dict) -> Veredicto:
    """Evalua una decision `delegar_opencode` ya validada.

    La delegacion SIEMPRE requiere confirmacion humana y es una capacidad
    EXCEPCIONAL fuera del catalogo de manifiestos. Para el ambito
    'escritura', ademas, el Arquitecto confina los efectos al sandbox
    `~/arqui-sandbox`:
      - bloquea si la tarea menciona rutas (absolutas, '~' o '..') que
        escapan del sandbox;
      - bloquea si el sandbox actual ya contiene symlinks que apuntan fuera;
      - el ejecutor lanza OpenCode con cwd = sandbox y agente sin bash, y
        revalida los efectos al terminar.
    """
    ambito = decision_norm.get("ambito")
    if ambito not in ("lectura", "escritura"):
        return _bloqueo(f"delegacion: ambito invalido '{ambito}'")

    tarea = str(decision_norm.get("tarea", "")).strip()
    agente, directorio = resolver_delegacion(ambito)

    avisos: list[str] = [AVISO_EXCEPCIONAL]

    if ambito == "escritura":
        sandbox = sandbox_escritura()

        # Rutas explicitas de la tarea que escapan del sandbox.
        escapan = _rutas_que_escapan(tarea, sandbox)
        if escapan:
            return _bloqueo(
                f"delegacion de escritura: la tarea referencia rutas fuera "
                f"del sandbox {sandbox}: {escapan}"
            )

        # Symlinks ya presentes en el sandbox que apuntan fuera.
        malos = symlinks_que_escapan(sandbox)
        if malos:
            return _bloqueo(
                f"delegacion de escritura: el sandbox contiene symlinks que "
                f"escapan del confinamiento: {malos[:5]}"
            )

        avisos.append(
            f"OpenCode SOLO podra CREAR/EDITAR ficheros dentro de {sandbox}"
        )

    texto = (
        f"[{AVISO_EXCEPCIONAL}]\n"
        f"Delegar a OpenCode ({ambito}) en {directorio}:\n  «{tarea}»"
    )
    return Veredicto(
        permitido=True,
        requiere_confirmacion=True,
        requiere_red=True,            # OpenCode usa el modelo remoto.
        motivo_bloqueo=None,
        avisos=tuple(avisos),
        texto_confirmacion=texto,
    )


# -- Modo de ingenieria gobernada (decision delegar_ingenieria) ----------------


def evaluar_ingenieria(
    decision_norm: dict, *, cwd: Path | None = None,
) -> Veredicto:
    """Evalua una decision `delegar_ingenieria` ya validada (PI-0 'explorar',
    PI-1 'editar').

    El modo de ingenieria deja que OpenCode actue sobre ficheros dentro de un
    directorio de una RAIZ autorizada, sin bash, sin red de sistema y sin
    skills (lo garantiza la configuracion del agente):
      - perfil 'explorar': SOLO lectura/busqueda/listado (`ingeniero-lectura`);
      - perfil 'editar': lectura + edicion/creacion CONFINADA al mismo
        directorio autorizado (`ingeniero-codigo`).
    Es una capacidad EXCEPCIONAL fuera del catalogo de manifiestos y SIEMPRE
    requiere confirmacion humana.

    Bloquea si:
      - el perfil no esta soportado (defensa en profundidad sobre el
        validador: 'comandos' aun no existe);
      - el directorio objetivo no cae en una raiz autorizada o es sensible
        (esto confina TAMBIEN la escritura del perfil 'editar': cualquier
        intento de editar fuera de la raiz autorizada queda bloqueado aqui);
      - la tarea menciona explicitamente rutas sensibles (credenciales).

    Nunca lanza: los problemas van en el Veredicto.
    """
    cwd = cwd if cwd is not None else Path.cwd()

    _agente, directorio, motivo = ingenieria.resolver_ejecucion_ingenieria(
        decision_norm, cwd,
    )
    if motivo is not None:
        return _bloqueo(f"ingenieria: {motivo}")

    tarea = str(decision_norm.get("tarea", ""))
    sensibles = ingenieria.rutas_sensibles_en_texto(tarea)
    if sensibles:
        return _bloqueo(
            f"ingenieria: la tarea referencia rutas sensibles: {sensibles}"
        )

    perfil = str(decision_norm.get("perfil", ""))
    escribe = ingenieria.perfil_escribe(perfil)
    if escribe:
        capacidades = (
            f"perfil={perfil}: lectura + EDICION confinada al directorio "
            "autorizado (sin bash, sin red, sin skills)"
        )
        concede = (
            "leer, buscar, listar y EDITAR/CREAR ficheros DENTRO del "
            "directorio."
        )
        deniega = "ejecutar comandos de shell, red e internet, skills."
        etiqueta = f"{perfil}, LECTURA + EDICION confinada"
    else:
        capacidades = (
            f"perfil={perfil}: SOLO lectura/busqueda/listado "
            "(sin bash, sin edicion, sin red, sin skills)"
        )
        concede = "leer, buscar y listar DENTRO del directorio."
        deniega = "editar, ejecutar comandos, red e internet, skills."
        etiqueta = f"{perfil}, SOLO LECTURA"

    avisos = (
        AVISO_EXCEPCIONAL,
        capacidades,
        f"directorio autorizado: {directorio}",
    )
    texto = (
        f"[{AVISO_EXCEPCIONAL}]\n"
        f"Ingenieria ({etiqueta}) en {directorio}:\n  «{tarea}»\n"
        f"  Concede: {concede}\n"
        f"  Deniega: {deniega}"
    )
    return Veredicto(
        permitido=True,
        requiere_confirmacion=True,
        requiere_red=True,            # OpenCode usa el modelo remoto.
        motivo_bloqueo=None,
        avisos=avisos,
        texto_confirmacion=texto,
    )


# -- Comandos estructurados gobernados (decision ejecutar_comandos, PI-2) -------

# Aviso fijo: `ejecutar_comandos` actua FUERA del catalogo de manifiestos. A
# diferencia de las delegaciones, aqui OpenCode NO ejecuta nada: solo propuso
# los comandos; los ejecuta el Arquitecto con shell=False y allowlist propia.
AVISO_COMANDOS = (
    "CAPACIDAD fuera de manifiestos: comandos de SOLO LECTURA propuestos por el "
    "cerebro y ejecutados por el Arquitecto (shell=False, allowlist propia)."
)


def evaluar_comandos(
    decision_norm: dict, *, cwd: Path | None = None,
) -> Veredicto:
    """Evalua una decision `ejecutar_comandos` ya validada (PI-2).

    Segunda barrera (defensa en profundidad sobre el validador): vuelve a
    preparar TODO el lote con `comandos.preparar_lote`, que revalida la forma
    contra la allowlist, resuelve el directorio de cada comando contra las
    RAICES_AUTORIZADAS (siguiendo symlinks) y confina las rutas de los
    argumentos. Si CUALQUIER comando no pasa, se BLOQUEA el lote entero (no
    se ejecuta nada).

    El texto de confirmacion describe el argv REAL de cada comando y
    comprueba de forma independiente (`comandos.comando_puede_escribir`) si
    alguno puede escribir: solo afirma "de solo lectura" cuando esa
    comprobacion lo confirma para TODO el lote. Si algun comando esta
    marcado como posible escritura, el texto lo señala explicitamente en vez
    de mentir (el usuario es la ultima barrera del diseño; no debe aprobar
    una escritura creyendo que es una lectura).

    SIEMPRE requiere confirmacion humana (una sola, para todo el lote). En v1
    ningun binario requiere red. Nunca lanza: los problemas van en el Veredicto.
    """
    lista = decision_norm.get("comandos") or []

    preparados, motivo = comandos.preparar_lote(lista, cwd_base=cwd)
    if motivo is not None:
        return _bloqueo(f"comandos: {motivo}")

    requiere_red = False
    lineas: list[str] = []
    alguna_escritura = False
    for argv, directorio, cmd in preparados:
        politica = comandos.binario_permitido(cmd.get("binario"))
        if politica is not None and politica.requiere_red:
            requiere_red = True
        razon_cmd = cmd.get("razon")
        sufijo = f"  ({razon_cmd})" if razon_cmd else ""
        if comandos.comando_puede_escribir(argv):
            alguna_escritura = True
            marca = "  [POSIBLE ESCRITURA]"
        else:
            marca = ""
        lineas.append(
            f"  $ {' '.join(argv)}{marca}\n      [dir: {directorio}]{sufijo}"
        )

    cuerpo = "\n".join(lineas)
    if alguna_escritura:
        encabezado = (
            f"ATENCION: el lote de {len(preparados)} comando(s) incluye al "
            f"menos uno marcado como POSIBLE ESCRITURA (revisa las lineas "
            f"señaladas antes de confirmar):"
        )
        avisos = (
            AVISO_COMANDOS,
            "ATENCION: este lote NO es de solo lectura; incluye comando(s) "
            "marcados como posible escritura.",
        )
    else:
        encabezado = f"Ejecutar {len(preparados)} comando(s) de solo lectura:"
        avisos = (AVISO_COMANDOS,)

    texto = f"[{AVISO_COMANDOS}]\n{encabezado}\n{cuerpo}"
    return Veredicto(
        permitido=True,
        requiere_confirmacion=True,
        requiere_red=requiere_red,
        motivo_bloqueo=None,
        avisos=avisos,
        texto_confirmacion=texto,
    )
