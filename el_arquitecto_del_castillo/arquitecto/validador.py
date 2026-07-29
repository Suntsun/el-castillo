"""
Validador del contrato JSON Arquitecto <-> Cerebro.

Cada turno del REPL, el cerebro (OpenCode) devuelve un JSON con UNA decision
del conjunto cerrado de 7:

    responder, aclarar, invocar, proponer_nueva,
    rechazar_peligro, pedir_confirmacion, componer

Esta unidad NO ejecuta NADA. Solo:

    1. Verifica que el dict cumple el esquema de su decision.
    2. Verifica que NO hay campos extra desconocidos en el nivel raiz ni
       en los sub-objetos cerrados.
    3. Si la decision implica invocar una operacion, comprueba contra el
       registro que existe la automatizacion, la operacion concreta y que
       los argumentos propuestos son compatibles con la whitelist y los
       tipos declarados en el manifiesto.
    4. Rechazo TOTAL ante cualquier metacaracter de shell en valores
       string (no admitimos shell crudo en ningun campo).

El contrato formal vive en prompts/contrato_json.md. ESTE modulo es la
implementacion ejecutable de ese contrato; si hay discrepancia, manda el
contrato y este modulo debe alinearse.

Errores SIEMPRE devueltos como tupla; nunca se lanza desde la API publica.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from arquitecto import comandos as _comandos

if TYPE_CHECKING:
    from arquitecto.registro import Manifiesto, Operacion, Argumento


# -- Constantes ----------------------------------------------------------------

# Conjunto cerrado de decisiones admitidas. Cualquier otra es rechazada.
DECISIONES_VALIDAS: frozenset[str] = frozenset({
    "responder",
    "aclarar",
    "invocar",
    "proponer_nueva",
    "rechazar_peligro",
    "pedir_confirmacion",
    "componer",
    "delegar_opencode",
    "delegar_ingenieria",
    "ejecutar_comandos",
})

# Ambitos validos para delegar_opencode (determina el agente y el sandbox).
_AMBITOS_DELEGACION: frozenset[str] = frozenset({"lectura", "escritura"})

# Perfiles validos para delegar_ingenieria. PI-0: 'explorar' (solo lectura).
# PI-1: 'editar' (lectura + edicion confinada al directorio autorizado).
# 'comandos' (PI-2, bash) NO existe todavia; si el cerebro lo propone, esta
# whitelist lo rechaza (fallo seguro).
_PERFILES_INGENIERIA: frozenset[str] = frozenset({"explorar", "editar"})

# Metacaracteres de shell que provocan RECHAZO TOTAL si aparecen en
# cualquier valor string de un campo de argumentos o equivalente. Es una
# regla de oro: el cerebro NUNCA propone shell crudo. Si lo hace, descartamos.
_METACARACTERES_SHELL: frozenset[str] = frozenset({
    ";", "|", "&", "`", "$", ">", "<", "\n", "\r", "\\",
})

# Para parsear $(...) y && y || y casos compuestos basta con detectar el
# caracter individual: '$', '&', '|', '`' ya estan listados.
# Tambien rechazamos saltos de linea por defensa-en-profundidad.

# Patrones aceptados por defecto segun tipo declarado en el manifiesto.
_REGEX_NOMBRE_SUGERIDO = re.compile(r"^[a-z][a-z0-9_]*$")
_REGEX_ENTERO = re.compile(r"^-?\d+$")
_REGEX_URL = re.compile(r"^https?://[^\s<>'\"]+$")

# Limites duros del contrato.
_MAX_RESPONDER = 280
_MAX_ACLARAR = 200
_MAX_RAZON = 200
_MAX_PROPONER = 280
_MAX_RECHAZAR = 280
_MAX_MENSAJE = 280
_MAX_TAREA = 600
_MAX_TAREA_ING = 800
_MIN_PASOS = 2
_MAX_PASOS = 5
_MIN_OPCIONES = 2
_MAX_OPCIONES = 5


# -- Helpers privados ----------------------------------------------------------


def _es_str_no_vacio(valor: Any, *, max_len: int | None = None) -> bool:
    if not isinstance(valor, str) or not valor:
        return False
    if max_len is not None and len(valor) > max_len:
        return False
    return True


def _contiene_metacaracter_shell(valor: str) -> str | None:
    """Devuelve el primer metacaracter encontrado, o None si esta limpio."""
    for ch in valor:
        if ch in _METACARACTERES_SHELL:
            return ch
    return None


def _claves_extra(presentes: set[str], permitidas: set[str]) -> set[str]:
    return presentes - permitidas


def _peor_peligrosidad(actual: str, nueva: str) -> str:
    """Devuelve la peligrosidad mas alta entre dos."""
    orden = {
        "lectura": 0,
        "escritura_local": 1,
        "red_saliente": 2,
        "escritura_sistema": 3,
        "destructiva": 4,
    }
    return actual if orden.get(actual, -1) >= orden.get(nueva, -1) else nueva


def _validar_valor_argumento(
    arg: "Argumento", valor: Any,
) -> tuple[bool, str]:
    """Valida UN valor concreto contra la declaracion del Argumento.

    Devuelve (True, "") si pasa, (False, motivo) si no.
    Acepta solo strings tal y como define el contrato (`dict<str,str>`).
    """
    if not isinstance(valor, str):
        return False, (
            f"argumento '{arg.clave}': valor debe ser string "
            f"(es {type(valor).__name__})"
        )

    # Defensa-en-profundidad: ningun valor de argumento puede tener shell.
    meta = _contiene_metacaracter_shell(valor)
    if meta is not None:
        return False, (
            f"argumento '{arg.clave}': contiene metacaracter shell {meta!r}"
        )

    if arg.tipo == "enum":
        if not arg.valores_validos or valor not in arg.valores_validos:
            return False, (
                f"argumento '{arg.clave}': valor '{valor}' no en "
                f"{list(arg.valores_validos or ())}"
            )

    elif arg.tipo == "entero":
        if not _REGEX_ENTERO.match(valor):
            return False, (
                f"argumento '{arg.clave}': '{valor}' no es entero valido"
            )
        try:
            n = int(valor)
        except ValueError:
            return False, f"argumento '{arg.clave}': '{valor}' no parsea como int"
        if arg.minimo is not None and n < arg.minimo:
            return False, (
                f"argumento '{arg.clave}': {n} < min ({arg.minimo})"
            )
        if arg.maximo is not None and n > arg.maximo:
            return False, (
                f"argumento '{arg.clave}': {n} > max ({arg.maximo})"
            )

    elif arg.tipo == "url":
        if not _REGEX_URL.match(valor):
            return False, f"argumento '{arg.clave}': '{valor}' no es URL http(s)"

    elif arg.tipo in {"ruta_fichero", "ruta_directorio"}:
        # NO comprobamos existencia (decision: validacion sintactica aqui;
        # existencia la valida el ejecutor en Fase 3). Solo bloqueamos
        # rutas con metacaracteres (ya cubierto) y rutas relativas peligrosas.
        if ".." in valor.split("/"):
            return False, (
                f"argumento '{arg.clave}': ruta contiene '..' (no permitido)"
            )

    elif arg.tipo == "cadena":
        # Sin restricciones adicionales mas alla del regex (si declarado).
        pass

    else:
        return False, f"argumento '{arg.clave}': tipo '{arg.tipo}' desconocido"

    # Regex declarado en el manifiesto (aplica a cualquier tipo).
    if arg.regex is not None:
        try:
            patron = re.compile(arg.regex)
        except re.error as exc:
            return False, (
                f"argumento '{arg.clave}': regex del manifiesto invalido ({exc})"
            )
        if not patron.match(valor):
            return False, (
                f"argumento '{arg.clave}': '{valor}' no casa con regex "
                f"'{arg.regex}'"
            )

    return True, ""


def _validar_invocacion_minima(
    sub: dict,
    registro: dict[str, "Manifiesto"],
    *,
    contexto: str,
    permite_razon: bool,
    es_nivel_raiz: bool = False,
) -> tuple[bool, str, dict | None]:
    """Valida el cuerpo comun de una invocacion (campos clave + argumentos).

    Se usa tanto para la decision `invocar` (con razon, nivel raiz) como
    para los bloques anidados de `pedir_confirmacion.invocacion` (sin
    razon, no raiz) y de cada paso de `componer` (con `parar_si_falla` y
    sin razon, no raiz, parar_si_falla ya retirado por el caller).

    Devuelve (ok, motivo, normalizada_minima). La normalizada incluye
    la operacion y la peligrosidad efectiva.
    """
    if not isinstance(sub, dict):
        return False, f"{contexto}: bloque no es objeto", None

    claves_permitidas = {
        "clave_automatizacion",
        "nombre_operacion",
        "argumentos",
    }
    if permite_razon:
        claves_permitidas.add("razon")
    if es_nivel_raiz:
        claves_permitidas.add("decision")

    clave_auto = sub.get("clave_automatizacion")
    if not _es_str_no_vacio(clave_auto):
        return False, f"{contexto}: 'clave_automatizacion' obligatorio y no vacio", None
    if clave_auto not in registro:
        return False, (
            f"{contexto}: automatizacion '{clave_auto}' no existe en el registro"
        ), None

    manifiesto = registro[clave_auto]

    nombre_op = sub.get("nombre_operacion")
    if not _es_str_no_vacio(nombre_op):
        return False, f"{contexto}: 'nombre_operacion' obligatorio y no vacio", None
    operacion = manifiesto.operacion(nombre_op)
    if operacion is None:
        return False, (
            f"{contexto}: operacion '{nombre_op}' no existe en manifiesto "
            f"'{clave_auto}'"
        ), None

    # Bloqueante: el cerebro no puede invocar operaciones que bloquean
    # la terminal. Esas las lanza el usuario manualmente.
    if operacion.bloquea_terminal:
        return False, (
            f"{contexto}: operacion '{nombre_op}' bloquea_terminal=true "
            f"(no invocable por el cerebro)"
        ), None

    # Sudo: regla del contrato (validacion global #5).
    if manifiesto.seguridad.requiere_sudo:
        return False, (
            f"{contexto}: automatizacion '{clave_auto}' requiere sudo "
            f"(no invocable por el cerebro)"
        ), None

    # argumentos: dict obligatorio (puede ser vacio).
    args = sub.get("argumentos")
    if args is None or not isinstance(args, dict):
        return False, f"{contexto}: 'argumentos' obligatorio (objeto, puede ser vacio)", None

    # Construir indice de argumentos del manifiesto.
    args_manifiesto: dict[str, "Argumento"] = {a.clave: a for a in manifiesto.argumentos}

    # 1) Toda clave propuesta debe estar en argumentos_aceptados de la operacion
    #    Y existir como Argumento del manifiesto.
    for clave_arg, valor in args.items():
        if clave_arg not in operacion.argumentos_aceptados:
            return False, (
                f"{contexto}: argumento '{clave_arg}' no esta en "
                f"argumentos_aceptados de '{nombre_op}'"
            ), None
        if clave_arg not in args_manifiesto:
            return False, (
                f"{contexto}: argumento '{clave_arg}' no declarado en "
                f"manifiesto '{clave_auto}'"
            ), None
        arg_def = args_manifiesto[clave_arg]
        ok, motivo = _validar_valor_argumento(arg_def, valor)
        if not ok:
            return False, f"{contexto}: {motivo}", None

    # 2) Todo argumento obligatorio de la operacion debe estar presente.
    for clave_arg in operacion.argumentos_aceptados:
        if clave_arg in args_manifiesto and args_manifiesto[clave_arg].obligatorio:
            if clave_arg not in args:
                return False, (
                    f"{contexto}: falta argumento obligatorio '{clave_arg}' "
                    f"de '{nombre_op}'"
                ), None

    # 3) Defensa: campos extra desconocidos en el sub-objeto. Para
    #    `invocar` y `pedir_confirmacion.invocacion` no admitimos extras.
    #    Para pasos de `componer` el caller se encarga de meter
    #    parar_si_falla en claves_permitidas.
    extra = _claves_extra(set(sub.keys()), claves_permitidas)
    if extra:
        return False, f"{contexto}: campos extra no permitidos: {sorted(extra)}", None

    # Peligrosidad efectiva: por ahora == la de la operacion. Los
    # `peligrosidad_override` de flags del brief NO existen en el esquema
    # actual del manifiesto; no se aplica nada extra. Si en el futuro se
    # anaden flags con override, este es el punto a tocar.
    peligrosidad_efectiva = operacion.peligrosidad

    requiere_confirmacion = (
        operacion.requiere_confirmacion or peligrosidad_efectiva != "lectura"
    )

    normalizada: dict[str, Any] = {
        "clave_automatizacion": clave_auto,
        "nombre_operacion": nombre_op,
        "argumentos": dict(args),
        "peligrosidad_efectiva": peligrosidad_efectiva,
        "requiere_confirmacion": requiere_confirmacion,
        "bloquea_terminal": operacion.bloquea_terminal,
        "manifiesto_clave": clave_auto,
    }
    return True, "", normalizada


# -- API publica: validadores especificos --------------------------------------


def _validar_responder(decision: dict) -> tuple[bool, str, dict | None]:
    extra = _claves_extra(set(decision.keys()), {"decision", "texto"})
    if extra:
        return False, f"responder: campos extra: {sorted(extra)}", None
    texto = decision.get("texto")
    if not _es_str_no_vacio(texto, max_len=_MAX_RESPONDER):
        return False, (
            f"responder: 'texto' obligatorio, no vacio, max {_MAX_RESPONDER}"
        ), None
    return True, "", {
        "decision": "responder",
        "texto": texto,
        "requiere_confirmacion": False,
    }


def _validar_aclarar(decision: dict) -> tuple[bool, str, dict | None]:
    extra = _claves_extra(set(decision.keys()), {"decision", "pregunta", "opciones"})
    if extra:
        return False, f"aclarar: campos extra: {sorted(extra)}", None
    pregunta = decision.get("pregunta")
    if not _es_str_no_vacio(pregunta, max_len=_MAX_ACLARAR):
        return False, (
            f"aclarar: 'pregunta' obligatoria, no vacia, max {_MAX_ACLARAR}"
        ), None
    opciones = decision.get("opciones")
    if opciones is not None:
        if not isinstance(opciones, list):
            return False, "aclarar: 'opciones' debe ser array", None
        if not (_MIN_OPCIONES <= len(opciones) <= _MAX_OPCIONES):
            return False, (
                f"aclarar: 'opciones' debe tener entre {_MIN_OPCIONES} "
                f"y {_MAX_OPCIONES} entradas"
            ), None
        if any(not _es_str_no_vacio(x) for x in opciones):
            return False, "aclarar: 'opciones' deben ser strings no vacios", None
    norm: dict[str, Any] = {
        "decision": "aclarar",
        "pregunta": pregunta,
        "requiere_confirmacion": False,
    }
    if opciones is not None:
        norm["opciones"] = list(opciones)
    return True, "", norm


def _validar_invocar(
    decision: dict,
    registro: dict[str, "Manifiesto"],
) -> tuple[bool, str, dict | None]:
    # Razon obligatoria a parte del bloque comun.
    razon = decision.get("razon")
    if not _es_str_no_vacio(razon, max_len=_MAX_RAZON):
        return False, (
            f"invocar: 'razon' obligatoria, no vacia, max {_MAX_RAZON}"
        ), None
    ok, motivo, base = _validar_invocacion_minima(
        decision, registro,
        contexto="invocar", permite_razon=True, es_nivel_raiz=True,
    )
    if not ok or base is None:
        return False, motivo, None

    norm = {
        "decision": "invocar",
        "razon": razon,
        **base,
    }
    return True, "", norm


def _validar_proponer_nueva(
    decision: dict,
    registro: dict[str, "Manifiesto"],
) -> tuple[bool, str, dict | None]:
    permitidas = {
        "decision", "nombre_sugerido", "descripcion",
        "justificacion", "encaje_ecosistema",
    }
    extra = _claves_extra(set(decision.keys()), permitidas)
    if extra:
        return False, f"proponer_nueva: campos extra: {sorted(extra)}", None

    nombre = decision.get("nombre_sugerido")
    if not _es_str_no_vacio(nombre):
        return False, "proponer_nueva: 'nombre_sugerido' obligatorio", None
    if not _REGEX_NOMBRE_SUGERIDO.match(nombre):
        return False, (
            f"proponer_nueva: 'nombre_sugerido'='{nombre}' no cumple snake_case "
            f"(^[a-z][a-z0-9_]*$)"
        ), None
    if nombre in registro:
        return False, (
            f"proponer_nueva: '{nombre}' ya existe en el registro; "
            f"deberia ser 'invocar', no 'proponer_nueva'"
        ), None

    descripcion = decision.get("descripcion")
    if not _es_str_no_vacio(descripcion, max_len=_MAX_PROPONER):
        return False, (
            f"proponer_nueva: 'descripcion' obligatoria, max {_MAX_PROPONER}"
        ), None

    justificacion = decision.get("justificacion")
    if not _es_str_no_vacio(justificacion, max_len=_MAX_PROPONER):
        return False, (
            f"proponer_nueva: 'justificacion' obligatoria, max {_MAX_PROPONER}"
        ), None

    encaje = decision.get("encaje_ecosistema")
    if encaje is not None and not _es_str_no_vacio(encaje, max_len=_MAX_PROPONER):
        return False, (
            f"proponer_nueva: 'encaje_ecosistema' si existe, no vacio, "
            f"max {_MAX_PROPONER}"
        ), None

    norm: dict[str, Any] = {
        "decision": "proponer_nueva",
        "nombre_sugerido": nombre,
        "descripcion": descripcion,
        "justificacion": justificacion,
        "requiere_confirmacion": False,
    }
    if encaje is not None:
        norm["encaje_ecosistema"] = encaje
    return True, "", norm


def _validar_rechazar_peligro(decision: dict) -> tuple[bool, str, dict | None]:
    extra = _claves_extra(
        set(decision.keys()), {"decision", "motivo", "sugerencia_segura"},
    )
    if extra:
        return False, f"rechazar_peligro: campos extra: {sorted(extra)}", None
    motivo = decision.get("motivo")
    if not _es_str_no_vacio(motivo, max_len=_MAX_RECHAZAR):
        return False, (
            f"rechazar_peligro: 'motivo' obligatorio, no vacio, max {_MAX_RECHAZAR}"
        ), None
    sugerencia = decision.get("sugerencia_segura")
    if sugerencia is not None and not _es_str_no_vacio(sugerencia, max_len=_MAX_RECHAZAR):
        return False, (
            f"rechazar_peligro: 'sugerencia_segura' si existe, no vacio"
        ), None
    norm: dict[str, Any] = {
        "decision": "rechazar_peligro",
        "motivo": motivo,
        "requiere_confirmacion": False,
    }
    if sugerencia is not None:
        norm["sugerencia_segura"] = sugerencia
    return True, "", norm


def _validar_pedir_confirmacion(
    decision: dict,
    registro: dict[str, "Manifiesto"],
) -> tuple[bool, str, dict | None]:
    extra = _claves_extra(set(decision.keys()), {"decision", "mensaje", "invocacion"})
    if extra:
        return False, f"pedir_confirmacion: campos extra: {sorted(extra)}", None
    mensaje = decision.get("mensaje")
    if not _es_str_no_vacio(mensaje, max_len=_MAX_MENSAJE):
        return False, (
            f"pedir_confirmacion: 'mensaje' obligatorio, no vacio, max {_MAX_MENSAJE}"
        ), None
    invocacion = decision.get("invocacion")
    if not isinstance(invocacion, dict):
        return False, "pedir_confirmacion: 'invocacion' obligatorio (objeto)", None
    ok, motivo, base = _validar_invocacion_minima(
        invocacion, registro,
        contexto="pedir_confirmacion.invocacion", permite_razon=False,
    )
    if not ok or base is None:
        return False, motivo, None
    norm = {
        "decision": "pedir_confirmacion",
        "mensaje": mensaje,
        "invocacion": base,
        # Por definicion esta decision YA pide confirmacion.
        "requiere_confirmacion": True,
    }
    return True, "", norm


def _validar_paso_componer(
    paso: dict,
    registro: dict[str, "Manifiesto"],
    idx: int,
) -> tuple[bool, str, dict | None]:
    if not isinstance(paso, dict):
        return False, f"componer.pasos[{idx}]: no es objeto", None

    # Sacar parar_si_falla aparte para que _validar_invocacion_minima no
    # se queje de campo extra.
    if "parar_si_falla" not in paso:
        return False, (
            f"componer.pasos[{idx}]: falta 'parar_si_falla' (bool)"
        ), None
    parar = paso.get("parar_si_falla")
    if not isinstance(parar, bool):
        return False, (
            f"componer.pasos[{idx}]: 'parar_si_falla' debe ser bool"
        ), None

    sub = {k: v for k, v in paso.items() if k != "parar_si_falla"}
    ok, motivo, base = _validar_invocacion_minima(
        sub, registro,
        contexto=f"componer.pasos[{idx}]", permite_razon=False,
    )
    if not ok or base is None:
        return False, motivo, None

    norm = {
        **base,
        "parar_si_falla": parar,
    }
    return True, "", norm


def _validar_componer(
    decision: dict,
    registro: dict[str, "Manifiesto"],
) -> tuple[bool, str, dict | None]:
    extra = _claves_extra(set(decision.keys()), {"decision", "razon", "pasos"})
    if extra:
        return False, f"componer: campos extra: {sorted(extra)}", None

    razon = decision.get("razon")
    if not _es_str_no_vacio(razon, max_len=_MAX_RAZON):
        return False, (
            f"componer: 'razon' obligatoria, no vacia, max {_MAX_RAZON}"
        ), None

    pasos = decision.get("pasos")
    if not isinstance(pasos, list):
        return False, "componer: 'pasos' debe ser array", None
    if not (_MIN_PASOS <= len(pasos) <= _MAX_PASOS):
        return False, (
            f"componer: 'pasos' debe tener entre {_MIN_PASOS} y {_MAX_PASOS} "
            f"(tiene {len(pasos)})"
        ), None

    norm_pasos: list[dict] = []
    peor = "lectura"
    requiere_conf = False
    for i, paso in enumerate(pasos):
        ok, motivo, base = _validar_paso_componer(paso, registro, i)
        if not ok or base is None:
            return False, motivo, None
        norm_pasos.append(base)
        peor = _peor_peligrosidad(peor, base["peligrosidad_efectiva"])
        requiere_conf = requiere_conf or base["requiere_confirmacion"]

    return True, "", {
        "decision": "componer",
        "razon": razon,
        "pasos": norm_pasos,
        "peligrosidad_efectiva": peor,
        "requiere_confirmacion": requiere_conf,
    }


def _validar_delegar_opencode(decision: dict) -> tuple[bool, str, dict | None]:
    """Valida la decision `delegar_opencode`.

    El cerebro la elige cuando la peticion es compleja/abierta (refactor,
    analisis profundo, generar codigo) y ninguna automatizacion del catalogo
    encaja. El Arquitecto la ejecutara delegando en OpenCode con un agente
    restringido y un sandbox; por eso SIEMPRE requiere confirmacion.

    No se aplica el filtro de metacaracteres de shell: `tarea` es una
    instruccion en lenguaje natural que se pasa como UN unico argumento a
    `subprocess.run(..., shell=False)`, asi que no hay riesgo de inyeccion.
    """
    permitidas = {"decision", "tarea", "ambito", "razon"}
    extra = _claves_extra(set(decision.keys()), permitidas)
    if extra:
        return False, f"delegar_opencode: campos extra: {sorted(extra)}", None

    tarea = decision.get("tarea")
    if not _es_str_no_vacio(tarea, max_len=_MAX_TAREA):
        return False, (
            f"delegar_opencode: 'tarea' obligatoria, no vacia, max {_MAX_TAREA}"
        ), None

    ambito = decision.get("ambito")
    if ambito not in _AMBITOS_DELEGACION:
        return False, (
            f"delegar_opencode: 'ambito' debe ser uno de {sorted(_AMBITOS_DELEGACION)}"
        ), None

    razon = decision.get("razon")
    if not _es_str_no_vacio(razon, max_len=_MAX_RAZON):
        return False, (
            f"delegar_opencode: 'razon' obligatoria, no vacia, max {_MAX_RAZON}"
        ), None

    return True, "", {
        "decision": "delegar_opencode",
        "tarea": tarea,
        "ambito": ambito,
        "razon": razon,
        # Delegar a OpenCode siempre pasa por confirmacion del usuario.
        "requiere_confirmacion": True,
    }


def _validar_delegar_ingenieria(decision: dict) -> tuple[bool, str, dict | None]:
    """Valida la decision `delegar_ingenieria` (PI-0 'explorar', PI-1 'editar').

    El cerebro la elige cuando la peticion exige MIRAR o EDITAR ficheros
    reales (leer/buscar/listar con 'explorar'; ademas editar/crear con
    'editar') y ninguna automatizacion del catalogo lo cubre. El Arquitecto
    delega en OpenCode con un agente acotado a un directorio de una raiz
    autorizada; por eso SIEMPRE requiere confirmacion.

    Esta capa solo valida FORMA. La whitelist de raices autorizadas y el
    bloqueo de rutas sensibles los aplica `seguridad.evaluar_ingenieria`.

    No se aplica el filtro de metacaracteres de shell: `tarea`/`directorio`
    se pasan como argumentos a `subprocess.run(..., shell=False)`.
    """
    permitidas = {"decision", "tarea", "perfil", "directorio", "razon"}
    extra = _claves_extra(set(decision.keys()), permitidas)
    if extra:
        return False, f"delegar_ingenieria: campos extra: {sorted(extra)}", None

    tarea = decision.get("tarea")
    if not _es_str_no_vacio(tarea, max_len=_MAX_TAREA_ING):
        return False, (
            f"delegar_ingenieria: 'tarea' obligatoria, no vacia, "
            f"max {_MAX_TAREA_ING}"
        ), None

    perfil = decision.get("perfil")
    if perfil not in _PERFILES_INGENIERIA:
        return False, (
            f"delegar_ingenieria: 'perfil' debe ser uno de "
            f"{sorted(_PERFILES_INGENIERIA)} (comandos aun no existe)"
        ), None

    # 'directorio' es opcional; si esta, debe ser string no vacio (la
    # resolucion contra las raices autorizadas la hace seguridad).
    directorio = decision.get("directorio")
    if directorio is not None and not _es_str_no_vacio(directorio):
        return False, (
            "delegar_ingenieria: 'directorio', si se indica, debe ser string "
            "no vacio"
        ), None

    razon = decision.get("razon")
    if not _es_str_no_vacio(razon, max_len=_MAX_RAZON):
        return False, (
            f"delegar_ingenieria: 'razon' obligatoria, no vacia, "
            f"max {_MAX_RAZON}"
        ), None

    norm = {
        "decision": "delegar_ingenieria",
        "tarea": tarea,
        "perfil": perfil,
        "razon": razon,
        # El modo de ingenieria siempre pasa por confirmacion del usuario.
        "requiere_confirmacion": True,
    }
    if directorio is not None:
        norm["directorio"] = directorio
    return True, "", norm


def _validar_ejecutar_comandos(decision: dict) -> tuple[bool, str, dict | None]:
    """Valida la decision `ejecutar_comandos` (PI-2).

    El cerebro PROPONE un lote de comandos de SOLO LECTURA estructurados
    (binario + lista de argumentos). El Arquitecto NO da bash a OpenCode: aqui
    se valida la FORMA contra la allowlist de `comandos.py` (binario permitido,
    subcomando de lectura, sin flags prohibidos, sin metacaracteres ni '..') y
    el lote completo. La confinacion de rutas a las raices autorizadas y la
    resolucion del directorio las hace `seguridad.evaluar_comandos` (necesitan
    cwd). SIEMPRE requiere confirmacion humana.

    No se filtra `directorio`/`razon` por metacaracteres de shell aqui: van a
    `subprocess.run(..., shell=False)` como argumentos sueltos; los argumentos
    de cada comando SI se filtran (regla de oro) en `validar_forma_comando`.
    """
    permitidas = {"decision", "comandos", "razon"}
    extra = _claves_extra(set(decision.keys()), permitidas)
    if extra:
        return False, f"ejecutar_comandos: campos extra: {sorted(extra)}", None

    razon = decision.get("razon")
    if not _es_str_no_vacio(razon, max_len=_MAX_RAZON):
        return False, (
            f"ejecutar_comandos: 'razon' obligatoria, no vacia, max {_MAX_RAZON}"
        ), None

    lista = decision.get("comandos")
    if not isinstance(lista, list):
        return False, "ejecutar_comandos: 'comandos' debe ser un array", None
    if not (1 <= len(lista) <= _comandos.MAX_COMANDOS):
        return False, (
            f"ejecutar_comandos: 'comandos' debe tener entre 1 y "
            f"{_comandos.MAX_COMANDOS} entradas (tiene {len(lista)})"
        ), None

    norm_cmds: list[dict] = []
    for i, cmd in enumerate(lista):
        ctx = f"ejecutar_comandos.comandos[{i}]"
        if not isinstance(cmd, dict):
            return False, f"{ctx}: no es un objeto", None
        extra_cmd = _claves_extra(
            set(cmd.keys()), {"binario", "argumentos", "directorio", "razon"},
        )
        if extra_cmd:
            return False, f"{ctx}: campos extra: {sorted(extra_cmd)}", None

        binario = cmd.get("binario")
        if not _es_str_no_vacio(binario):
            return False, f"{ctx}: 'binario' obligatorio y no vacio", None

        argumentos = cmd.get("argumentos")
        if argumentos is None:
            argumentos = []
        if not isinstance(argumentos, list):
            return False, f"{ctx}: 'argumentos' debe ser un array", None

        # Validacion de FORMA contra la allowlist (binario, subcomando, flags,
        # metacaracteres, '..'). El argv final lo recompone seguridad/ejecutor.
        ok, motivo, _argv = _comandos.validar_forma_comando(binario, argumentos)
        if not ok:
            return False, f"{ctx}: {motivo}", None

        directorio = cmd.get("directorio")
        if directorio is not None and not _es_str_no_vacio(directorio):
            return False, (
                f"{ctx}: 'directorio', si se indica, debe ser string no vacio"
            ), None

        razon_cmd = cmd.get("razon")
        if razon_cmd is not None and not _es_str_no_vacio(razon_cmd, max_len=_MAX_RAZON):
            return False, (
                f"{ctx}: 'razon', si se indica, no vacia, max {_MAX_RAZON}"
            ), None

        norm_cmd: dict[str, Any] = {
            "binario": binario,
            "argumentos": [str(a) for a in argumentos],
        }
        if directorio is not None:
            norm_cmd["directorio"] = directorio
        if razon_cmd is not None:
            norm_cmd["razon"] = razon_cmd
        norm_cmds.append(norm_cmd)

    return True, "", {
        "decision": "ejecutar_comandos",
        "razon": razon,
        "comandos": norm_cmds,
        # El Arquitecto SIEMPRE confirma antes de ejecutar comandos del lote.
        "requiere_confirmacion": True,
    }


# -- API publica: entrada unica ------------------------------------------------


def validar_decision(
    decision: Any,
    registro: dict[str, "Manifiesto"],
) -> tuple[bool, str, dict | None]:
    """
    Valida un dict JSON devuelto por OpenCode contra el contrato y el
    registro.

    Args:
        decision: dict ya parseado desde el JSON del cerebro. Si no es un
                  dict, se rechaza limpiamente.
        registro: registro inmutable cargado por `registro.cargar_registro`.

    Returns:
        (es_valida, motivo_si_no, decision_normalizada).
          - decision_normalizada incluye campos derivados
            (peligrosidad_efectiva, requiere_confirmacion, etc.).
          - Nunca lanza. Errores siempre como tupla.
    """
    if not isinstance(decision, dict):
        return False, "raiz: la decision debe ser un objeto JSON", None

    nombre_decision = decision.get("decision")
    if not isinstance(nombre_decision, str):
        return False, "raiz: campo 'decision' obligatorio y debe ser string", None
    if nombre_decision not in DECISIONES_VALIDAS:
        return False, (
            f"raiz: 'decision'='{nombre_decision}' no en {sorted(DECISIONES_VALIDAS)}"
        ), None

    match nombre_decision:
        case "responder":
            return _validar_responder(decision)
        case "aclarar":
            return _validar_aclarar(decision)
        case "invocar":
            return _validar_invocar(decision, registro)
        case "proponer_nueva":
            return _validar_proponer_nueva(decision, registro)
        case "rechazar_peligro":
            return _validar_rechazar_peligro(decision)
        case "pedir_confirmacion":
            return _validar_pedir_confirmacion(decision, registro)
        case "componer":
            return _validar_componer(decision, registro)
        case "delegar_opencode":
            return _validar_delegar_opencode(decision)
        case "delegar_ingenieria":
            return _validar_delegar_ingenieria(decision)
        case "ejecutar_comandos":
            return _validar_ejecutar_comandos(decision)
        case _:
            # Inalcanzable por la comprobacion previa, pero defensa.
            return False, f"raiz: rama no implementada para '{nombre_decision}'", None


def normalizar_a_lista_argumentos(
    decision_invocar_norm: dict,
    manifiesto: "Manifiesto",
) -> list[str]:
    """
    Traduce una decision 'invocar' YA validada en la lista exacta de
    tokens que pasara al subprocess.run() (siempre con shell=False).

    NO se ejecuta nada aqui; solo se construye la lista.

    El brief de Fase 2 propuso renombrar `normalizar_a_linea_shell` a
    algo que dejase claro que NO devolvemos shell. Lo dejo asi: devuelve
    LISTA, no string. La construccion definitiva (con flags fijos de
    operacion + serializacion de argumentos por forma_paso) la consume el
    ejecutor en Fase 3; aqui se queda el esqueleto pero ya funcional para
    operaciones sin argumentos (que es lo que tiene el piloto).

    Returns:
        Lista de strings tipo ["errores", "--semana"]. Nunca un string
        unico, nunca con `shell=True` posible.
    """
    op = manifiesto.operacion(decision_invocar_norm["nombre_operacion"])
    if op is None:
        # No deberia pasar si la decision ya pasa por validar_decision.
        return [manifiesto.comando_base]

    tokens: list[str] = [manifiesto.comando_base]

    if manifiesto.usa_subcomandos and op.subcomando:
        tokens.append(op.subcomando)

    # Flags fijos de la operacion (en el orden declarado).
    tokens.extend(op.flags)

    # Argumentos propuestos por el cerebro (whitelist ya validada). La
    # serializacion concreta por forma_paso (flag_largo, flag_corto,
    # flag_bool, posicional) se completa en Fase 3 cuando exista alguna
    # operacion del Castillo que use args. El piloto no usa.
    args = decision_invocar_norm.get("argumentos") or {}
    args_manifiesto = {a.clave: a for a in manifiesto.argumentos}
    for clave, valor in args.items():
        arg = args_manifiesto.get(clave)
        if arg is None:
            continue  # Ya validado, no deberia ocurrir.
        match arg.forma_paso:
            case "flag_bool":
                # Solo se anhade el flag literal si valor truthy.
                if valor.lower() in {"true", "1", "si", "yes"}:
                    if arg.flag_literal:
                        tokens.append(arg.flag_literal)
            case "flag_largo" | "flag_corto":
                if arg.flag_literal:
                    tokens.append(arg.flag_literal)
                    tokens.append(valor)
            case "posicional":
                tokens.append(valor)

    return tokens


class DecisionInvalidaError(Exception):
    """Se lanza solo desde codigo cliente que prefiera excepciones a tuplas."""
