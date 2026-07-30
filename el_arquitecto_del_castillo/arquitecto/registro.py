"""
Registro de manifiestos del Castillo.

Recorre el directorio raiz de automatizaciones, carga cada manifiesto.toml
con tomllib (stdlib) y construye un diccionario inmutable
{clave: Manifiesto} que el resto del Arquitecto consume.

El registro NO valida en profundidad las decisiones del cerebro
(eso es trabajo de `validador.py`); solo garantiza que cada manifiesto
parsea, cumple el esquema basico y esta listo para ser consultado.

Esta unidad NO ejecuta subprocesos ni I/O de red.
"""

from __future__ import annotations

import hashlib
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Logger compartido del Castillo. El paquete `comun` vive en la raiz de
# automatizaciones, asi que aseguramos el import anhadiendo dicha raiz al
# sys.path si hace falta.
_RAIZ_AUTOMATIZACIONES = Path(__file__).resolve().parent.parent.parent
if str(_RAIZ_AUTOMATIZACIONES) not in sys.path:
    sys.path.insert(0, str(_RAIZ_AUTOMATIZACIONES))

from comun.logger import configurar_logger  # noqa: E402

_log = configurar_logger("arquitecto.registro")


# Version MAJOR del esquema de manifiesto que esta version del Arquitecto soporta.
ESQUEMA_MAJOR_SOPORTADO = 1

# Nombre exacto del fichero que debe haber en cada carpeta de automatizacion.
NOMBRE_FICHERO_MANIFIESTO = "manifiesto.toml"

# Carpetas dentro de RUTA_BASE que NUNCA contienen automatizaciones reales.
CARPETAS_IGNORADAS: frozenset[str] = frozenset({
    "comun",
    "images",
    "logs",
    "el_arquitecto_del_castillo",
    "__pycache__",
})

# Enums permitidos (definidos en docs/esquema_manifiesto.md).
_CATEGORIAS_VALIDAS: frozenset[str] = frozenset({
    "monitorizacion", "mantenimiento", "seguridad", "medios", "red",
    "productividad", "comunicacion", "meta", "otra",
})
_PELIGROSIDADES_VALIDAS: frozenset[str] = frozenset({
    "lectura", "escritura_local", "escritura_sistema",
    "red_saliente", "destructiva",
})
_SALIDAS_VALIDAS: frozenset[str] = frozenset({
    "texto_corto", "texto_largo", "interactivo", "silencioso",
})
_TIPOS_INVOCACION_VALIDOS: frozenset[str] = frozenset({
    "wrapper_cli", "script_directo", "comando_sistema",
})
_TIPOS_ARGUMENTO_VALIDOS: frozenset[str] = frozenset({
    "enum", "entero", "cadena", "ruta_fichero", "ruta_directorio", "url",
})
_FORMAS_PASO_VALIDAS: frozenset[str] = frozenset({
    "posicional", "flag_largo", "flag_corto", "flag_bool",
})

_REGEX_CLAVE = re.compile(r"^[a-z][a-z0-9_]*$")
_REGEX_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


@dataclass(frozen=True)
class Operacion:
    """Una operacion concreta que el LLM puede proponer ejecutar."""

    nombre: str
    descripcion: str
    flags: tuple[str, ...]
    argumentos_aceptados: tuple[str, ...]
    requiere_confirmacion: bool
    peligrosidad: str
    bloquea_terminal: bool
    salida_esperada: str
    subcomando: str | None = None


@dataclass(frozen=True)
class Argumento:
    """Un argumento aceptado por una o varias operaciones."""

    clave: str
    descripcion: str
    tipo: str
    obligatorio: bool
    forma_paso: str
    valor_por_defecto: str | None = None
    valores_validos: tuple[str, ...] | None = None
    minimo: int | None = None
    maximo: int | None = None
    regex: str | None = None
    flag_literal: str | None = None


@dataclass(frozen=True)
class Seguridad:
    """Reglas de seguridad globales de la automatizacion."""

    permite_argumentos_libres: bool
    requiere_red: bool
    requiere_sudo: bool
    tiempo_max_segundos: int
    paths_protegidos: tuple[str, ...] = ()


@dataclass(frozen=True)
class Dependencias:
    """Requisitos externos declarados por la automatizacion."""

    binarios: tuple[str, ...]
    paquetes_python: tuple[str, ...]
    ficheros_config: tuple[str, ...]
    servicios_systemd: tuple[str, ...]


@dataclass(frozen=True)
class ContextoLlm:
    """Pistas semanticas que se inyectan al cerebro."""

    cuando_usar: str
    cuando_no_usar: str
    ejemplos_peticion: tuple[str, ...]
    palabras_clave: tuple[str, ...]


@dataclass(frozen=True)
class Manifiesto:
    """Manifiesto completo de una automatizacion, ya validado y normalizado."""

    clave: str
    nombre_visible: str
    descripcion_corta: str
    categoria: str
    version_manifiesto: str
    comando_base: str
    tipo_invocacion: str
    usa_subcomandos: bool
    subcomando_por_defecto: str | None
    operaciones: tuple[Operacion, ...]
    argumentos: tuple[Argumento, ...]
    seguridad: Seguridad
    dependencias: Dependencias
    contexto_llm: ContextoLlm
    ruta_fichero: Path = field(compare=False)

    def operacion(self, nombre: str) -> Operacion | None:
        """Devuelve la operacion por nombre, o None si no existe."""
        for op in self.operaciones:
            if op.nombre == nombre:
                return op
        return None


class ManifiestoInvalidoError(Exception):
    """Se lanza cuando un manifiesto.toml no cumple el esquema."""


# -- Helpers internos de validacion --------------------------------------------


def _campo_str(seccion: dict[str, Any], clave: str, contexto: str) -> str:
    valor = seccion.get(clave)
    if not isinstance(valor, str) or not valor:
        raise ManifiestoInvalidoError(
            f"{contexto}: campo '{clave}' obligatorio y debe ser string no vacio"
        )
    return valor


def _campo_bool(seccion: dict[str, Any], clave: str, contexto: str) -> bool:
    valor = seccion.get(clave)
    if not isinstance(valor, bool):
        raise ManifiestoInvalidoError(
            f"{contexto}: campo '{clave}' obligatorio y debe ser bool"
        )
    return valor


def _campo_int(seccion: dict[str, Any], clave: str, contexto: str) -> int:
    valor = seccion.get(clave)
    if not isinstance(valor, int) or isinstance(valor, bool):
        raise ManifiestoInvalidoError(
            f"{contexto}: campo '{clave}' obligatorio y debe ser int"
        )
    return valor


def _campo_lista_str(
    seccion: dict[str, Any], clave: str, contexto: str,
) -> tuple[str, ...]:
    valor = seccion.get(clave)
    if valor is None:
        raise ManifiestoInvalidoError(
            f"{contexto}: campo '{clave}' obligatorio (lista de strings)"
        )
    if not isinstance(valor, list) or any(not isinstance(x, str) for x in valor):
        raise ManifiestoInvalidoError(
            f"{contexto}: campo '{clave}' debe ser lista de strings"
        )
    return tuple(valor)


def _validar_semver_major(version: str, contexto: str) -> None:
    m = _REGEX_SEMVER.match(version)
    if not m:
        raise ManifiestoInvalidoError(
            f"{contexto}: version_manifiesto '{version}' no es SemVer X.Y.Z"
        )
    major = int(m.group(1))
    if major != ESQUEMA_MAJOR_SOPORTADO:
        raise ManifiestoInvalidoError(
            f"{contexto}: version_manifiesto MAJOR={major} no soportado "
            f"(soportado: {ESQUEMA_MAJOR_SOPORTADO})"
        )


def _parsear_operacion(
    datos: dict[str, Any], idx: int, usa_subcomandos: bool,
) -> Operacion:
    contexto = f"operaciones[{idx}]"
    nombre = _campo_str(datos, "nombre", contexto)
    if not _REGEX_CLAVE.match(nombre):
        raise ManifiestoInvalidoError(
            f"{contexto}: nombre '{nombre}' no cumple snake_case"
        )
    descripcion = _campo_str(datos, "descripcion", contexto)
    flags = _campo_lista_str(datos, "flags", contexto)
    argumentos_aceptados = _campo_lista_str(datos, "argumentos_aceptados", contexto)
    requiere_confirmacion = _campo_bool(datos, "requiere_confirmacion", contexto)
    peligrosidad = _campo_str(datos, "peligrosidad", contexto)
    if peligrosidad not in _PELIGROSIDADES_VALIDAS:
        raise ManifiestoInvalidoError(
            f"{contexto}: peligrosidad '{peligrosidad}' no en {sorted(_PELIGROSIDADES_VALIDAS)}"
        )
    bloquea_terminal = _campo_bool(datos, "bloquea_terminal", contexto)
    salida_esperada = _campo_str(datos, "salida_esperada", contexto)
    if salida_esperada not in _SALIDAS_VALIDAS:
        raise ManifiestoInvalidoError(
            f"{contexto}: salida_esperada '{salida_esperada}' no en {sorted(_SALIDAS_VALIDAS)}"
        )

    # Coherencia: confirmacion solo tiene sentido si la operacion no es lectura.
    if requiere_confirmacion and peligrosidad == "lectura":
        raise ManifiestoInvalidoError(
            f"{contexto}: requiere_confirmacion=true incompatible con peligrosidad=lectura"
        )

    subcomando: str | None = None
    if usa_subcomandos:
        subcomando = _campo_str(datos, "subcomando", contexto)
    elif "subcomando" in datos:
        raise ManifiestoInvalidoError(
            f"{contexto}: declara 'subcomando' pero invocacion.usa_subcomandos=false"
        )

    return Operacion(
        nombre=nombre,
        descripcion=descripcion,
        flags=flags,
        argumentos_aceptados=argumentos_aceptados,
        requiere_confirmacion=requiere_confirmacion,
        peligrosidad=peligrosidad,
        bloquea_terminal=bloquea_terminal,
        salida_esperada=salida_esperada,
        subcomando=subcomando,
    )


def _parsear_argumento(datos: dict[str, Any], idx: int) -> Argumento:
    contexto = f"argumentos[{idx}]"
    clave = _campo_str(datos, "clave", contexto)
    if not _REGEX_CLAVE.match(clave):
        raise ManifiestoInvalidoError(
            f"{contexto}: clave '{clave}' no cumple snake_case"
        )
    descripcion = _campo_str(datos, "descripcion", contexto)
    tipo = _campo_str(datos, "tipo", contexto)
    if tipo not in _TIPOS_ARGUMENTO_VALIDOS:
        raise ManifiestoInvalidoError(
            f"{contexto}: tipo '{tipo}' no en {sorted(_TIPOS_ARGUMENTO_VALIDOS)}"
        )
    obligatorio = _campo_bool(datos, "obligatorio", contexto)
    forma_paso = _campo_str(datos, "forma_paso", contexto)
    if forma_paso not in _FORMAS_PASO_VALIDAS:
        raise ManifiestoInvalidoError(
            f"{contexto}: forma_paso '{forma_paso}' no en {sorted(_FORMAS_PASO_VALIDAS)}"
        )

    valor_por_defecto = datos.get("valor_por_defecto")
    if valor_por_defecto is not None and not isinstance(valor_por_defecto, str):
        raise ManifiestoInvalidoError(
            f"{contexto}: valor_por_defecto debe ser string"
        )
    if obligatorio and valor_por_defecto is not None:
        raise ManifiestoInvalidoError(
            f"{contexto}: obligatorio=true incompatible con valor_por_defecto"
        )

    valores_validos: tuple[str, ...] | None = None
    if tipo == "enum":
        bruto = datos.get("valores_validos")
        if not isinstance(bruto, list) or not bruto:
            raise ManifiestoInvalidoError(
                f"{contexto}: tipo=enum requiere valores_validos no vacio"
            )
        if any(not isinstance(x, str) for x in bruto):
            raise ManifiestoInvalidoError(
                f"{contexto}: valores_validos debe ser lista de strings"
            )
        valores_validos = tuple(bruto)

    minimo = datos.get("min")
    maximo = datos.get("max")
    if tipo == "entero":
        if minimo is not None and not isinstance(minimo, int):
            raise ManifiestoInvalidoError(f"{contexto}: min debe ser int")
        if maximo is not None and not isinstance(maximo, int):
            raise ManifiestoInvalidoError(f"{contexto}: max debe ser int")
        if minimo is not None and maximo is not None and minimo > maximo:
            raise ManifiestoInvalidoError(f"{contexto}: min > max")

    regex = datos.get("regex")
    if regex is not None and not isinstance(regex, str):
        raise ManifiestoInvalidoError(f"{contexto}: regex debe ser string")

    flag_literal = datos.get("flag_literal")
    if forma_paso.startswith("flag_"):
        if not isinstance(flag_literal, str) or not flag_literal:
            raise ManifiestoInvalidoError(
                f"{contexto}: forma_paso='{forma_paso}' requiere flag_literal"
            )

    return Argumento(
        clave=clave,
        descripcion=descripcion,
        tipo=tipo,
        obligatorio=obligatorio,
        forma_paso=forma_paso,
        valor_por_defecto=valor_por_defecto,
        valores_validos=valores_validos,
        minimo=minimo,
        maximo=maximo,
        regex=regex,
        flag_literal=flag_literal,
    )


# -- API publica del modulo ----------------------------------------------------


def cargar_manifiesto(ruta_fichero: Path) -> Manifiesto | None:
    """Carga UN manifiesto.toml. Devuelve None si es invalido (logueando WARNING).

    Args:
        ruta_fichero: Ruta absoluta al manifiesto.toml.

    Returns:
        Instancia inmutable de Manifiesto, o None si el TOML no cumple.
    """
    ruta = Path(ruta_fichero)
    if not ruta.is_file():
        _log.warning("cargar_manifiesto: %s no es fichero", ruta)
        return None

    try:
        with open(ruta, "rb") as fh:
            datos = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        _log.warning("cargar_manifiesto: error parseando %s: %s", ruta, exc)
        return None

    try:
        return _construir_manifiesto(datos, ruta)
    except ManifiestoInvalidoError as exc:
        _log.warning("cargar_manifiesto: %s invalido: %s", ruta, exc)
        return None


def _construir_manifiesto(datos: dict[str, Any], ruta: Path) -> Manifiesto:
    """Convierte el dict TOML en un Manifiesto validado. Lanza si falla."""
    # [meta]
    meta = datos.get("meta")
    if not isinstance(meta, dict):
        raise ManifiestoInvalidoError("falta seccion [meta]")
    clave = _campo_str(meta, "clave", "meta")
    if not _REGEX_CLAVE.match(clave):
        raise ManifiestoInvalidoError(f"meta.clave '{clave}' no cumple snake_case")
    nombre_carpeta = ruta.parent.name
    if clave != nombre_carpeta:
        raise ManifiestoInvalidoError(
            f"meta.clave='{clave}' no coincide con carpeta padre='{nombre_carpeta}'"
        )
    nombre_visible = _campo_str(meta, "nombre_visible", "meta")
    descripcion_corta = _campo_str(meta, "descripcion_corta", "meta")
    if len(descripcion_corta) > 120:
        raise ManifiestoInvalidoError(
            f"meta.descripcion_corta excede 120 chars ({len(descripcion_corta)})"
        )
    categoria = _campo_str(meta, "categoria", "meta")
    if categoria not in _CATEGORIAS_VALIDAS:
        raise ManifiestoInvalidoError(
            f"meta.categoria '{categoria}' no en {sorted(_CATEGORIAS_VALIDAS)}"
        )
    version_manifiesto = _campo_str(meta, "version_manifiesto", "meta")
    _validar_semver_major(version_manifiesto, "meta")

    # [invocacion]
    inv = datos.get("invocacion")
    if not isinstance(inv, dict):
        raise ManifiestoInvalidoError("falta seccion [invocacion]")
    comando_base = _campo_str(inv, "comando_base", "invocacion")
    tipo_invocacion = _campo_str(inv, "tipo", "invocacion")
    if tipo_invocacion not in _TIPOS_INVOCACION_VALIDOS:
        raise ManifiestoInvalidoError(
            f"invocacion.tipo '{tipo_invocacion}' no en {sorted(_TIPOS_INVOCACION_VALIDOS)}"
        )
    usa_subcomandos = _campo_bool(inv, "usa_subcomandos", "invocacion")
    subcomando_por_defecto: str | None = None
    if usa_subcomandos:
        subcomando_por_defecto = _campo_str(
            inv, "subcomando_por_defecto", "invocacion",
        )
    elif "subcomando_por_defecto" in inv:
        raise ManifiestoInvalidoError(
            "invocacion: subcomando_por_defecto presente con usa_subcomandos=false"
        )

    # [[operaciones]]
    ops_raw = datos.get("operaciones")
    if not isinstance(ops_raw, list) or not ops_raw:
        raise ManifiestoInvalidoError("falta seccion [[operaciones]] no vacia")
    operaciones: list[Operacion] = []
    nombres_op: set[str] = set()
    for i, op_dict in enumerate(ops_raw):
        if not isinstance(op_dict, dict):
            raise ManifiestoInvalidoError(f"operaciones[{i}] no es tabla")
        op = _parsear_operacion(op_dict, i, usa_subcomandos)
        if op.nombre in nombres_op:
            raise ManifiestoInvalidoError(
                f"operaciones: nombre duplicado '{op.nombre}'"
            )
        nombres_op.add(op.nombre)
        operaciones.append(op)

    # [[argumentos]] (opcional)
    args_raw = datos.get("argumentos", [])
    if not isinstance(args_raw, list):
        raise ManifiestoInvalidoError("argumentos debe ser lista de tablas")
    argumentos: list[Argumento] = []
    claves_arg: set[str] = set()
    for i, ar_dict in enumerate(args_raw):
        if not isinstance(ar_dict, dict):
            raise ManifiestoInvalidoError(f"argumentos[{i}] no es tabla")
        ar = _parsear_argumento(ar_dict, i)
        if ar.clave in claves_arg:
            raise ManifiestoInvalidoError(
                f"argumentos: clave duplicada '{ar.clave}'"
            )
        claves_arg.add(ar.clave)
        argumentos.append(ar)

    # Referencias cruzadas operacion.argumentos_aceptados -> argumentos.clave
    for op in operaciones:
        for clave_ref in op.argumentos_aceptados:
            if clave_ref not in claves_arg:
                raise ManifiestoInvalidoError(
                    f"operacion '{op.nombre}': argumento '{clave_ref}' no declarado"
                )

    # [seguridad]
    seg = datos.get("seguridad")
    if not isinstance(seg, dict):
        raise ManifiestoInvalidoError("falta seccion [seguridad]")
    permite_libres = _campo_bool(seg, "permite_argumentos_libres", "seguridad")
    requiere_red = _campo_bool(seg, "requiere_red", "seguridad")
    requiere_sudo = _campo_bool(seg, "requiere_sudo", "seguridad")
    tiempo_max = _campo_int(seg, "tiempo_max_segundos", "seguridad")
    if not (1 <= tiempo_max <= 3600):
        raise ManifiestoInvalidoError(
            f"seguridad.tiempo_max_segundos={tiempo_max} fuera de [1, 3600]"
        )
    paths_prot_raw = seg.get("paths_protegidos", [])
    if not isinstance(paths_prot_raw, list) or any(
        not isinstance(x, str) for x in paths_prot_raw
    ):
        raise ManifiestoInvalidoError(
            "seguridad.paths_protegidos debe ser lista de strings"
        )
    paths_prot_raw = [str(Path(p).expanduser()) for p in paths_prot_raw]
    for p in paths_prot_raw:
        if not p.startswith("/"):
            raise ManifiestoInvalidoError(
                f"seguridad.paths_protegidos: '{p}' no es ruta absoluta"
            )
    seguridad = Seguridad(
        permite_argumentos_libres=permite_libres,
        requiere_red=requiere_red,
        requiere_sudo=requiere_sudo,
        tiempo_max_segundos=tiempo_max,
        paths_protegidos=tuple(paths_prot_raw),
    )

    # [dependencias] (obligatoria segun esquema: todos los arrays presentes)
    dep = datos.get("dependencias")
    if not isinstance(dep, dict):
        raise ManifiestoInvalidoError("falta seccion [dependencias]")
    dependencias = Dependencias(
        binarios=_campo_lista_str(dep, "binarios", "dependencias"),
        paquetes_python=_campo_lista_str(dep, "paquetes_python", "dependencias"),
        ficheros_config=_campo_lista_str(dep, "ficheros_config", "dependencias"),
        servicios_systemd=_campo_lista_str(dep, "servicios_systemd", "dependencias"),
    )

    # [contexto_llm]
    ctx = datos.get("contexto_llm")
    if not isinstance(ctx, dict):
        raise ManifiestoInvalidoError("falta seccion [contexto_llm]")
    cuando_usar = _campo_str(ctx, "cuando_usar", "contexto_llm")
    cuando_no_usar = _campo_str(ctx, "cuando_no_usar", "contexto_llm")
    ejemplos = _campo_lista_str(ctx, "ejemplos_peticion", "contexto_llm")
    if not (2 <= len(ejemplos) <= 5):
        raise ManifiestoInvalidoError(
            f"contexto_llm.ejemplos_peticion debe tener 2..5 entradas (tiene {len(ejemplos)})"
        )
    palabras = _campo_lista_str(ctx, "palabras_clave", "contexto_llm")
    if not palabras:
        raise ManifiestoInvalidoError(
            "contexto_llm.palabras_clave no puede estar vacia"
        )
    contexto_llm = ContextoLlm(
        cuando_usar=cuando_usar,
        cuando_no_usar=cuando_no_usar,
        ejemplos_peticion=ejemplos,
        palabras_clave=palabras,
    )

    return Manifiesto(
        clave=clave,
        nombre_visible=nombre_visible,
        descripcion_corta=descripcion_corta,
        categoria=categoria,
        version_manifiesto=version_manifiesto,
        comando_base=comando_base,
        tipo_invocacion=tipo_invocacion,
        usa_subcomandos=usa_subcomandos,
        subcomando_por_defecto=subcomando_por_defecto,
        operaciones=tuple(operaciones),
        argumentos=tuple(argumentos),
        seguridad=seguridad,
        dependencias=dependencias,
        contexto_llm=contexto_llm,
        ruta_fichero=ruta,
    )


def cargar_registro(ruta_base: Path) -> dict[str, Manifiesto]:
    """Escanea `ruta_base/*/manifiesto.toml` y devuelve {clave: Manifiesto}.

    Carpetas sin manifiesto se omiten silenciosamente (la migracion es
    incremental). Manifiestos invalidos se descartan con WARNING.
    """
    base = Path(ruta_base)
    if not base.is_dir():
        _log.error("cargar_registro: %s no es directorio", base)
        return {}

    registro: dict[str, Manifiesto] = {}
    for entrada in sorted(base.iterdir()):
        if not entrada.is_dir() or entrada.name in CARPETAS_IGNORADAS:
            continue
        if entrada.name.startswith("."):
            continue
        ruta_manif = entrada / NOMBRE_FICHERO_MANIFIESTO
        if not ruta_manif.is_file():
            continue
        manif = cargar_manifiesto(ruta_manif)
        if manif is None:
            continue
        if manif.clave in registro:
            _log.warning(
                "cargar_registro: clave duplicada '%s' (omitiendo %s)",
                manif.clave, ruta_manif,
            )
            continue
        registro[manif.clave] = manif

    claves = sorted(registro.keys())
    hash_claves = hashlib.sha256("|".join(claves).encode("utf-8")).hexdigest()[:12]
    _log.info(
        "cargar_registro: %d manifiestos cargados, hash_claves=%s",
        len(registro), hash_claves,
    )
    return registro


def _formato_argumentos(
    op: Operacion, args_idx: dict[str, Argumento],
) -> str:
    """Formatea los argumentos aceptados de una operacion, compacto.

    Ej.: "(args: periodo:enum[dia,semana]*, ruta:ruta_fichero)". El sufijo
    `*` marca obligatorio. Para enum se listan los valores validos. Cadena
    vacia si la operacion no acepta argumentos.
    """
    partes: list[str] = []
    for clave in op.argumentos_aceptados:
        arg = args_idx.get(clave)
        if arg is None:
            continue
        desc = f"{arg.clave}:{arg.tipo}"
        if arg.tipo == "enum" and arg.valores_validos:
            desc += "[" + ",".join(arg.valores_validos) + "]"
        if arg.obligatorio:
            desc += "*"
        partes.append(desc)
    if not partes:
        return ""
    return "  (args: " + ", ".join(partes) + ")"


def vista_para_cerebro(registro: dict[str, Manifiesto]) -> str:
    """Catalogo en texto para inyectar en el prompt del cerebro.

    Por cada automatizacion, una linea de cabecera y debajo SUS operaciones
    con peligrosidad y argumentos, para que el cerebro pueda emitir un
    `invocar` valido (nombre_operacion exacto + argumentos correctos) sin
    inventarse nada. Formato:

        - <clave>: <descripcion_corta> [<categoria>]
            · <operacion>: <descripcion>  [<peligrosidad>· confirma]  (args: ...)
            · <operacion_bloqueante>: ...  [solo-manual: <motivo>]

    Las operaciones que el cerebro NO puede invocar (las que bloquean la
    terminal, o todas si el manifiesto requiere sudo) se marcan como
    `solo-manual`: el cerebro las ve para poder explicar que existen, pero
    sabe que debe pedir al usuario que las lance a mano.
    """
    lineas: list[str] = []
    for clave in sorted(registro.keys()):
        m = registro[clave]
        lineas.append(f"- {clave}: {m.descripcion_corta} [{m.categoria}]")
        args_idx = {a.clave: a for a in m.argumentos}
        sudo = m.seguridad.requiere_sudo
        for op in m.operaciones:
            desc = op.descripcion.strip()
            if len(desc) > 70:
                desc = desc[:67] + "..."

            if sudo:
                marca = "solo-manual: requiere sudo"
            elif op.bloquea_terminal:
                marca = "solo-manual: bloquea la terminal"
            else:
                marca = op.peligrosidad
                # Coherente con seguridad._PELIGROSIDAD_CONFIRMACION_FORZOSA:
                # se honra el manifiesto + suelo duro en 'destructiva'.
                if op.requiere_confirmacion or op.peligrosidad == "destructiva":
                    marca += "· confirma"

            args = "" if (sudo or op.bloquea_terminal) else _formato_argumentos(op, args_idx)
            lineas.append(f"    · {op.nombre}: {desc}  [{marca}]{args}")
    return "\n".join(lineas)
