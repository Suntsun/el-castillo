# Esquema oficial del `manifiesto.toml`

Documento de referencia técnica que define la estructura, los campos y las
reglas de validación que aplica el Arquitecto del Castillo sobre el archivo
`manifiesto.toml` de cada automatización.

Cada automatización del Castillo tiene (o tendrá) UN `manifiesto.toml` en la
raíz de su carpeta. El Arquitecto carga todos los manifiestos al arrancar y
construye un registro inmutable. El LLM-cerebro recibe una vista resumida del
registro y propone invocaciones; el `validador.py` comprueba toda propuesta
contra estos manifiestos antes de pasar nada al `ejecutor.py`.

Formato: TOML (parseado con `tomllib` de stdlib, Python 3.11+).

---

## Convenciones generales

- El nombre de la clave principal de la automatización (campo `meta.clave`)
  DEBE coincidir exactamente con el nombre de la carpeta que lo contiene
  (ejemplo: `cronista_errores/manifiesto.toml` lleva `clave = "cronista_errores"`).
- Todos los textos visibles al usuario van en español.
- Identificadores internos (claves TOML, nombres de operación, IDs de
  argumento) van en snake_case y SIN tildes ni eñes.
- Si una sección no aplica, debe declararse vacía (`[seccion]` sin campos, o
  array vacío `valor = []`). Nunca se omite una sección obligatoria.
- Los manifiestos NO contienen lógica ejecutable: son datos puros que
  describen contratos.

---

## Estructura completa

```
[meta]               obligatoria
[invocacion]         obligatoria
[[operaciones]]      obligatoria, al menos 1
[seguridad]          obligatoria
[[argumentos]]       opcional, 0..N entradas
[dependencias]       opcional pero recomendada
[contexto_llm]       obligatoria
```

---

## Sección `[meta]`

Identidad de la automatización.

| Campo | Tipo | Obligatorio | Descripción |
|---|---|---|---|
| `clave` | string | sí | Identificador único, igual al nombre de la carpeta. snake_case. |
| `nombre_visible` | string | sí | Nombre legible mostrado al usuario. |
| `descripcion_corta` | string | sí | Una frase, máx. 120 caracteres. Lo que ve el LLM al elegir. |
| `categoria` | string (enum) | sí | Una de: `monitorizacion`, `mantenimiento`, `seguridad`, `medios`, `red`, `productividad`, `comunicacion`, `meta`, `otra`. |
| `version_manifiesto` | string | sí | Versión SemVer del esquema usado: `"1.0.0"` para esta primera versión. |

**Reglas de validación:**
- `clave` debe cumplir la regex `^[a-z][a-z0-9_]*$`.
- `clave` debe coincidir con el nombre de la carpeta padre.
- `descripcion_corta` no puede exceder 120 caracteres.
- `categoria` debe ser uno de los valores del enum exacto.
- `version_manifiesto` debe parsear como `MAJOR.MINOR.PATCH`. Las versiones
  con el mismo `MAJOR` son compatibles.

---

## Sección `[invocacion]`

Cómo se ejecuta el comando real desde shell.

| Campo | Tipo | Obligatorio | Descripción |
|---|---|---|---|
| `comando_base` | string | sí | Comando absoluto o presente en `$PATH`. Ej.: `"errores"`, `"~/.local/bin/castillo"`. |
| `tipo` | string (enum) | sí | `wrapper_cli` (script en `~/.local/bin/`), `script_directo` (ruta a `.py`), o `comando_sistema` (binario instalado). |
| `usa_subcomandos` | bool | sí | `true` si el primer argumento es un subcomando (estilo git). |
| `subcomando_por_defecto` | string | si `usa_subcomandos = true` | Subcomando implícito cuando no se pasa ninguno. |

**Reglas de validación:**
- Si `tipo = "wrapper_cli"`, el `comando_base` debe existir como archivo
  ejecutable en `$PATH` (o ruta absoluta válida).
- Si `tipo = "script_directo"`, debe ser ruta absoluta y existir.
- Si `usa_subcomandos = false`, no debe declararse `subcomando_por_defecto`.
- El validador NO ejecuta el comando para comprobar; solo verifica
  existencia y permisos de ejecución.

---

## Sección `[[operaciones]]` (array de tablas)

Cada operación es una acción concreta que el LLM puede proponer. Una
automatización puede tener varias (ej.: `errores` tiene `mostrar`, `seguir`,
`limpiar`, etc.). El nombre de la operación es el identificador que el LLM
usa en su JSON de decisión `invocar`.

Cada entrada `[[operaciones]]` lleva:

| Campo | Tipo | Obligatorio | Descripción |
|---|---|---|---|
| `nombre` | string | sí | ID interno, snake_case. Único dentro del manifiesto. |
| `descripcion` | string | sí | Qué hace, en una o dos frases. |
| `flags` | array de strings | sí | Flags exactos que se pasan al comando base, en orden. Puede estar vacío `[]`. |
| `subcomando` | string | si `invocacion.usa_subcomandos = true` | Subcomando concreto de esta operación. |
| `argumentos_aceptados` | array de strings | sí | Lista de claves de `[[argumentos]]` que esta operación acepta. Puede ser `[]`. |
| `requiere_confirmacion` | bool | sí | Si `true`, el ejecutor pedirá confirmación explícita por terminal antes de lanzar. |
| `peligrosidad` | string (enum) | sí | `lectura` (solo lee), `escritura_local` (escribe en disco propio), `escritura_sistema` (toca config o servicios), `red_saliente`, `destructiva` (rm, kill, etc.). |
| `bloquea_terminal` | bool | sí | Si `true`, la operación corre en foreground y captura stdin/stdout (ej.: `seguir` con tail -f). El ejecutor decidirá si lanzarla de otro modo. |
| `salida_esperada` | string (enum) | sí | `texto_corto` (cabe en notificación), `texto_largo` (paginar), `interactivo` (necesita TTY), `silencioso` (no produce salida útil). |

**Reglas de validación:**
- `nombre` único dentro del manifiesto.
- `nombre` cumple `^[a-z][a-z0-9_]*$`.
- Toda clave en `argumentos_aceptados` debe corresponder a una entrada real
  de `[[argumentos]]`.
- `peligrosidad in {lectura, escritura_local, escritura_sistema, red_saliente, destructiva}`.
- Si `peligrosidad in {destructiva, escritura_sistema}` entonces
  `requiere_confirmacion` debe ser `true`.
- `salida_esperada in {texto_corto, texto_largo, interactivo, silencioso}`.
- Si `bloquea_terminal = true`, el Arquitecto NO podrá ejecutarla
  automáticamente desde el cerebro; solo el usuario podrá lanzarla
  explícitamente (queda fuera del flujo `invocar` del LLM en Fase 0).
- Al menos UNA operación con `peligrosidad = "lectura"` debe existir si la
  automatización tiene flujo de consulta.

---

## Sección `[seguridad]`

Reglas globales que aplican a TODA la automatización, independientemente de
la operación.

| Campo | Tipo | Obligatorio | Descripción |
|---|---|---|---|
| `permite_argumentos_libres` | bool | sí | Si `false`, ningún argumento puede pasarse fuera de los declarados en `[[argumentos]]`. Si `true`, el ejecutor aceptará un único campo libre validado por regex (avanzado, Fase posterior). |
| `requiere_red` | bool | sí | Si `true`, el Arquitecto verificará conectividad antes de invocar. |
| `requiere_sudo` | bool | sí | Si `true`, el manifiesto será marcado como bloqueado del flujo automático del LLM. |
| `tiempo_max_segundos` | int | sí | Timeout duro del subprocess. El ejecutor matará el proceso si lo excede. Mínimo 1, máximo 3600. |
| `paths_protegidos` | array de strings | no | Rutas absolutas que la operación NO debe modificar (avisa el validador si aparecen en los argumentos). Default: `[]`. |

**Reglas de validación:**
- `tiempo_max_segundos` entre 1 y 3600 inclusive.
- Si `requiere_sudo = true`, toda operación queda fuera del flujo `invocar`
  del LLM (Fase 0). El usuario solo puede invocarlas manualmente.
- `paths_protegidos` debe contener solo rutas absolutas si está presente.

---

## Sección `[[argumentos]]` (array de tablas, opcional)

Whitelist estricta de argumentos posicionales o con nombre que el LLM puede
incluir en su propuesta. Cada argumento tiene UN tipo y reglas de validación.

| Campo | Tipo | Obligatorio | Descripción |
|---|---|---|---|
| `clave` | string | sí | ID interno del argumento. snake_case. Único en el manifiesto. |
| `descripcion` | string | sí | Para qué sirve, contexto para el LLM. |
| `tipo` | string (enum) | sí | `enum`, `entero`, `cadena`, `ruta_fichero`, `ruta_directorio`, `url`. |
| `obligatorio` | bool | sí | Si `true`, debe aparecer en la invocación. |
| `valor_por_defecto` | string | no | Sólo válido si `obligatorio = false`. |
| `valores_validos` | array | si `tipo = "enum"` | Lista exhaustiva de strings permitidos. |
| `min` | int | si `tipo = "entero"` | Cota inferior inclusiva. |
| `max` | int | si `tipo = "entero"` | Cota superior inclusiva. |
| `regex` | string | si `tipo = "cadena"` | Patrón Python `re` que el valor debe cumplir entero (`fullmatch`). |
| `forma_paso` | string (enum) | sí | Cómo se traslada al CLI: `posicional`, `flag_largo` (`--clave valor`), `flag_corto` (`-x valor`), `flag_bool` (presencia sin valor). |
| `flag_literal` | string | si `forma_paso` empieza por `flag_` | Texto exacto del flag tal como espera el comando (ej.: `"--ruta"`, `"-n"`). |

**Reglas de validación:**
- `clave` única, snake_case.
- `tipo` debe ser uno del enum.
- Si `tipo = "enum"`, `valores_validos` no puede estar vacío.
- Si `tipo = "entero"` y se declaran `min`/`max`, debe cumplirse `min <= max`.
- Si `tipo` es `ruta_fichero` o `ruta_directorio`, el validador comprobará
  que la ruta es absoluta y no figura en `seguridad.paths_protegidos`.
- Si `tipo = "url"`, el valor debe parsear como URL HTTP/HTTPS válida.
- Si `forma_paso = "flag_bool"`, `valor_por_defecto` solo puede ser
  `"true"` o `"false"`.
- Si `obligatorio = true`, NO puede declararse `valor_por_defecto`.

---

## Sección `[dependencias]`

Requisitos externos que la automatización necesita para funcionar.

| Campo | Tipo | Obligatorio | Descripción |
|---|---|---|---|
| `binarios` | array de strings | sí | Comandos shell que deben existir en `$PATH`. Ej.: `["notify-send", "jq"]`. Puede ser `[]`. |
| `paquetes_python` | array de strings | sí | Imports no-stdlib. Puede ser `[]`. |
| `ficheros_config` | array de strings | sí | Rutas absolutas que deben existir para que la operación funcione. Puede ser `[]`. |
| `servicios_systemd` | array de strings | sí | Unidades systemd-user que deben estar activas. Puede ser `[]`. |

**Reglas de validación:**
- Todos los arrays son obligatorios (pueden estar vacíos, no omitidos).
- El validador solo comprueba existencia de binarios en `$PATH`; no instala.
- Si alguno falla, el Arquitecto puede usar la decisión `rechazar_peligro`
  o `aclarar` para avisar al usuario antes de invocar.

---

## Sección `[contexto_llm]`

Información que se inyecta al cerebro (OpenCode) para que decida bien sobre
esta automatización. Es lo único que ve el LLM de cada manifiesto cuando se
construye el catálogo del registro.

| Campo | Tipo | Obligatorio | Descripción |
|---|---|---|---|
| `cuando_usar` | string | sí | Frase imperativa: cuándo TIENE sentido invocar esta automatización. |
| `cuando_no_usar` | string | sí | Casos típicos en que NO se debería invocar (anti-ejemplos). |
| `ejemplos_peticion` | array de strings | sí | 2 a 5 frases reales en lenguaje natural que deberían disparar esta automatización. |
| `palabras_clave` | array de strings | sí | Términos sueltos que ayudan al matching rápido sin LLM. |

**Reglas de validación:**
- `ejemplos_peticion` debe tener entre 2 y 5 entradas.
- `palabras_clave` no puede estar vacío.
- Los textos deben ir en español, sin emojis.

---

## Ejemplo mínimo válido

Automatización imaginaria `eco` que solo imprime lo que recibe (no existe;
sólo para ilustrar el mínimo):

```toml
[meta]
clave = "eco"
nombre_visible = "Eco"
descripcion_corta = "Imprime lo recibido por stdin"
categoria = "otra"
version_manifiesto = "1.0.0"

[invocacion]
comando_base = "eco"
tipo = "wrapper_cli"
usa_subcomandos = false

[[operaciones]]
nombre = "imprimir"
descripcion = "Imprime el texto recibido"
flags = []
argumentos_aceptados = []
requiere_confirmacion = false
peligrosidad = "lectura"
bloquea_terminal = false
salida_esperada = "texto_corto"

[seguridad]
permite_argumentos_libres = false
requiere_red = false
requiere_sudo = false
tiempo_max_segundos = 5

[dependencias]
binarios = []
paquetes_python = []
ficheros_config = []
servicios_systemd = []

[contexto_llm]
cuando_usar = "Cuando el usuario quiere verificar que el sistema responde."
cuando_no_usar = "Para cualquier operación real; es un placeholder."
ejemplos_peticion = ["di hola", "responde algo"]
palabras_clave = ["eco", "hola"]
```

---

## Ejemplo completo de referencia

Ver el manifiesto real de `cronista_errores`:
`~/Escritorio/automatizaciones/cronista_errores/manifiesto.toml`.

Es el primer manifiesto piloto y cubre todos los campos relevantes con
valores reales del comportamiento actual de la automatización.

---

## Compatibilidad y extensibilidad

- El número `meta.version_manifiesto` sigue SemVer.
- Cambios MINOR/PATCH (añadir campos opcionales nuevos) deben ser
  retrocompatibles: los manifiestos viejos siguen cargando.
- Cambios MAJOR (renombrar campos, cambiar enums, hacer obligatorio algo que
  era opcional) requieren bump de `MAJOR` y migración explícita.
- Para añadir un campo nuevo opcional sin romper nada:
  1. Documentarlo aquí marcándolo como opcional.
  2. Hacer que el validador trate la ausencia como valor por defecto.
  3. Subir `MINOR`.
- El Arquitecto rechazará cargar manifiestos cuya `MAJOR` difiera de la que
  soporta la versión actual del validador.

---

## Reglas resumidas que aplicará `validador.py`

1. El TOML parsea sin errores.
2. Todas las secciones obligatorias están presentes.
3. Todos los campos obligatorios de cada sección están presentes y con el
   tipo correcto.
4. Los enums sólo aceptan valores listados.
5. `meta.clave` coincide con el nombre del directorio padre.
6. `meta.version_manifiesto.MAJOR` coincide con la MAJOR soportada por el
   validador (1 en esta versión).
7. Toda referencia cruzada (`operaciones.argumentos_aceptados` →
   `argumentos.clave`) resuelve.
8. Reglas adicionales descritas en cada sección arriba.

Si cualquier regla falla, el manifiesto se descarta y el error se loguea.
El Arquitecto NO arranca el REPL si menos del 100% de los manifiestos
declarados son válidos (decisión de Fase 0: fail-fast).
