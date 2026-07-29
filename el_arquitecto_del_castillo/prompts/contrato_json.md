# Contrato JSON Arquitecto <-> Cerebro

Documento normativo. Define el formato exacto de la respuesta del cerebro
(OpenCode) y las reglas que aplicara `validador.py`.

Toda respuesta del cerebro DEBE ser un unico objeto JSON con un campo
`decision` cuyo valor pertenece al enum cerrado de 10 valores. Cada
decision tiene su propio conjunto de campos obligatorios y opcionales.
Campos extra desconocidos -> rechazado.

---

## Tipos primitivos usados

| Notacion | Significado |
|---|---|
| `str` | Cadena UTF-8 no vacia (salvo que se indique lo contrario). |
| `str?` | Cadena opcional (puede faltar; si esta, no vacia). |
| `bool` | `true` o `false`. |
| `int` | Entero. |
| `dict<str, str>` | Objeto JSON con claves string y valores string. |
| `array<X>` | Array JSON homogeneo. |
| `enum(a, b, c)` | Cadena que SOLO puede tomar uno de esos valores. |

---

## 1. `responder`

Para conversacion pura, saludos, respuestas a preguntas generales sin
necesidad de accion.

```json
{
  "decision": "responder",
  "texto": "str (max 280 caracteres)"
}
```

**Reglas:**
- `texto` obligatorio, no vacio, max 280 caracteres.
- Sin campos extra.

---

## 2. `aclarar`

Para pedir UNA pregunta concisa al usuario cuando la peticion es ambigua.

```json
{
  "decision": "aclarar",
  "pregunta": "str (max 200 caracteres)",
  "opciones": ["str", "str", ...]
}
```

**Reglas:**
- `pregunta` obligatoria, no vacia, max 200 caracteres.
- `opciones` opcional. Si esta, array de 2 a 5 strings cortas que el
  usuario puede elegir.

---

## 3. `invocar`

Ejecutar UNA operacion concreta de UNA automatizacion del catalogo.

```json
{
  "decision": "invocar",
  "clave_automatizacion": "str",
  "nombre_operacion": "str",
  "argumentos": {"clave_arg": "valor_str", ...},
  "razon": "str (max 200 caracteres)"
}
```

**Reglas:**
- `clave_automatizacion` debe existir en el catalogo del turno.
- `nombre_operacion` debe existir en las operaciones del manifiesto
  referenciado.
- `argumentos` obligatorio: dict (puede estar vacio `{}`). Cada clave
  debe estar en `argumentos_aceptados` de la operacion. Cada valor debe
  cumplir las reglas del argumento (tipo, enum, regex, min/max).
- TODO argumento obligatorio del manifiesto debe estar presente en
  `argumentos`.
- `razon` obligatoria, no vacia, max 200 caracteres. Una sola linea.
- Si la operacion tiene `bloquea_terminal = true` o el manifiesto tiene
  `seguridad.requiere_sudo = true`, la decision sera rechazada y se
  pedira usar `responder` o `aclarar` indicando que el usuario debe
  lanzarla manualmente.

---

## 4. `proponer_nueva`

Cuando el usuario pide algo razonable pero no hay automatizacion para
ello en el catalogo.

```json
{
  "decision": "proponer_nueva",
  "nombre_sugerido": "str (snake_case)",
  "descripcion": "str (max 280 caracteres)",
  "justificacion": "str (max 280 caracteres)",
  "encaje_ecosistema": "str?"
}
```

**Reglas:**
- `nombre_sugerido` cumple `^[a-z][a-z0-9_]*$`.
- `descripcion` y `justificacion` obligatorias, max 280 cada una.
- `encaje_ecosistema` opcional: con que automatizaciones existentes se
  relaciona o complementa.
- El Arquitecto solo presenta esto al usuario; no crea nada.

---

## 5. `rechazar_peligro`

Cuando la peticion es peligrosa, esta fuera del alcance del Castillo o
podria dar lugar a destruccion no intencionada.

```json
{
  "decision": "rechazar_peligro",
  "motivo": "str (max 280 caracteres)",
  "sugerencia_segura": "str?"
}
```

**Reglas:**
- `motivo` obligatorio. Una o dos frases.
- `sugerencia_segura` opcional: que alternativa segura podria intentar.

---

## 6. `pedir_confirmacion`

Cuando quieres invocar algo pero, por la peligrosidad o por falta de
explicitud, prefieres pedir un OK al usuario antes.

```json
{
  "decision": "pedir_confirmacion",
  "mensaje": "str (max 280 caracteres)",
  "invocacion": {
    "clave_automatizacion": "str",
    "nombre_operacion": "str",
    "argumentos": {"clave_arg": "valor_str", ...}
  }
}
```

**Reglas:**
- `mensaje` obligatorio: lo que se le mostrara al usuario.
- `invocacion` obligatoria y debe cumplir TODAS las reglas de la
  decision `invocar` excepto el campo `razon` (que NO va aqui).
- El Arquitecto mostrara `mensaje` por terminal; si el usuario responde
  afirmativamente, se ejecutara la `invocacion` tal cual.

---

## 7. `componer`

Cuando la peticion requiere encadenar varias invocaciones en orden.

```json
{
  "decision": "componer",
  "razon": "str (max 200 caracteres)",
  "pasos": [
    {
      "clave_automatizacion": "str",
      "nombre_operacion": "str",
      "argumentos": {"clave_arg": "valor_str", ...},
      "parar_si_falla": true
    }
    /* ... mas pasos ... */
  ]
}
```

**Reglas:**
- `razon` obligatoria, una linea.
- `pasos`: array de 2 a 5 entradas. Una sola sub-invocacion no justifica
  `componer`; en ese caso usa `invocar`.
- Cada paso valida como una decision `invocar` (sin `razon` ni
  `decision`, pero con todos los demas campos) mas un `parar_si_falla`
  booleano que indica si el Arquitecto debe abortar la cadena cuando ese
  paso devuelve codigo de salida != 0.
- Si CUALQUIER paso falla la validacion individual, toda la `componer`
  se rechaza.

---

## 8. `delegar_opencode`

Cuando la peticion es una tarea de codigo o ficheros compleja/abierta
(analizar a fondo, refactorizar, generar codigo nuevo, reorganizar) que
NINGUNA automatizacion del catalogo cubre. En vez de proponer una
automatizacion nueva, delegas la tarea a OpenCode, que actuara con un
agente restringido dentro de un directorio acotado (sandbox). El
Arquitecto SIEMPRE pedira confirmacion al usuario antes de delegar.

```json
{
  "decision": "delegar_opencode",
  "tarea": "str (instruccion clara y autocontenida, max 600 caracteres)",
  "ambito": "lectura | escritura",
  "razon": "str (max 200 caracteres)"
}
```

**Reglas:**
- `tarea` obligatoria, no vacia, max 600. Describe QUE hacer de forma
  autocontenida (OpenCode no ve esta conversacion).
- `ambito` obligatorio: `lectura` (inspeccionar/analizar/buscar, no modifica;
  actua sobre el directorio actual) o `escritura` (crear/editar ficheros;
  actua SOLO dentro de un sandbox dedicado).
- `razon` obligatoria, una linea. Sin campos extra.
- Para tareas PUNTUALES de codigo/ficheros. Si falta una herramienta
  reutilizable, usa `proponer_nueva`. Si solo es explicar/analizar/resumir/
  buscar codigo, prefiere `invocar` sobre `asistente_opencode`.

---

## 9. `delegar_ingenieria`

Cuando la peticion exige MIRAR o EDITAR ficheros reales y ninguna
automatizacion del catalogo lo cubre. El Arquitecto delega en OpenCode con un
agente restringido acotado a un directorio de una raiz autorizada y SIEMPRE
pide confirmacion al usuario. Hay dos perfiles:
- `explorar` (SOLO lectura): leer, buscar, listar, entender como esta
  organizado un repositorio o un arbol de configuracion. Agente
  `ingeniero-lectura`.
- `editar` (lectura + EDICION CONFINADA): ademas de leer, editar o crear
  ficheros dentro del directorio autorizado para aplicar un cambio concreto de
  codigo o configuracion. Agente `ingeniero-codigo`. La escritura queda
  confinada al directorio autorizado.

```json
{
  "decision": "delegar_ingenieria",
  "tarea": "str (instruccion clara y autocontenida, max 800 caracteres)",
  "perfil": "explorar | editar",
  "directorio": "str?  (carpeta objetivo; debe caer en una raiz autorizada)",
  "razon": "str (max 200 caracteres)"
}
```

**Reglas:**
- `tarea` obligatoria, no vacia, max 800. Describe QUE inspeccionar o QUE
  cambio aplicar, de forma autocontenida (OpenCode no ve esta conversacion).
- `perfil` obligatorio. Valores permitidos: `explorar` (solo lectura) y
  `editar` (lectura + edicion confinada). El perfil `comandos` (ejecutar shell)
  NO existe todavia: si lo propones, el Arquitecto rechaza la decision.
- `directorio` opcional. Si lo indicas, debe caer dentro de una de las raices
  autorizadas; si no, el Arquitecto lo bloquea. Si lo omites, se usa el
  directorio actual SOLO si esta dentro de una raiz autorizada. En `editar`,
  ese directorio confina TODA la escritura: editar fuera de el se rechaza.
- `razon` obligatoria, una linea. Sin campos extra.
- Ningun perfil ejecuta comandos de shell, accede a internet ni usa skills. Si
  la peticion necesita algo de eso, todavia no esta disponible: usa `responder`
  para explicar la limitacion o `proponer_nueva`.
- Usa `editar` SOLO cuando el usuario pide aplicar un cambio real a ficheros
  dentro de una raiz autorizada. Para entender o localizar sin tocar nada, usa
  `explorar`.

Raices autorizadas (unicas carpetas donde puede mirar y, en `editar`, escribir):
`~/Escritorio/automatizaciones`, `~/Escritorio/proyectos`, `~/repos`,
`~/.config/omarchy`, `~/.config/waybar`, `~/.config/hypr`, `~/.config/walker`.
(NO se autoriza `~/.config` entero.)

Diferencia con `delegar_opencode`: `delegar_opencode` es la via de laboratorio
para tareas puntuales de CODIGO/ficheros con su propio sandbox
(`~/arqui-sandbox`); `delegar_ingenieria` actua sobre las raices autorizadas
reales (explorar = solo mirar; editar = mirar + cambiar confinado). Si hay que
leer/buscar/listar o aplicar un cambio concreto dentro de una raiz autorizada,
usa `delegar_ingenieria`.

---

## 10. `ejecutar_comandos`

Cuando el usuario necesita INSPECCIONAR el sistema con uno o varios comandos
concretos de SOLO LECTURA que ninguna automatizacion del catalogo cubre (ver
el estado de un repo git, cuanto espacio libre queda, el estado de un servicio
systemd del usuario, listar/leer ficheros de una raiz autorizada, etc.).

IMPORTANTE: tu NO ejecutas nada. Solo PROPONES los comandos de forma
estructurada. El Arquitecto los valida contra su propia allowlist y, si pasan,
los ejecuta el mismo con `subprocess.run(shell=False)`, pidiendo SIEMPRE una
confirmacion humana. Nunca propongas una linea de shell: propones binario +
lista de argumentos, por separado.

```json
{
  "decision": "ejecutar_comandos",
  "razon": "str (max 200 caracteres)",
  "comandos": [
    {
      "binario": "str (debe estar en la allowlist de abajo)",
      "argumentos": ["str", "str", ...],
      "directorio": "str?  (carpeta de una raiz autorizada; opcional)",
      "razon": "str?  (max 200; por que este comando)"
    }
    /* ... hasta 5 comandos ... */
  ]
}
```

**Reglas:**
- `razon` obligatoria (una linea). `comandos`: array de 1 a 5 entradas.
- `binario` debe estar en la allowlist. `argumentos` es un ARRAY de strings
  (un token por elemento: `["status", "--short"]`, NUNCA `"status --short"`).
  Sin metacaracteres de shell (`; | & $ \` > < \\`) ni `..` en ningun token.
- `directorio` opcional: si lo indicas, debe caer en una raiz autorizada (las
  mismas que `delegar_ingenieria`). Si lo omites, se usa una carpeta autorizada
  por defecto. Cualquier ruta ABSOLUTA o de HOME que aparezca en `argumentos`
  tambien debe caer en una raiz autorizada y no ser sensible (credenciales).
- SOLO LECTURA. Esta PROHIBIDO (el Arquitecto lo rechaza): cualquier binario
  fuera de la allowlist (`bash`, `sh`, `python`, `rm`, `mv`, `cp`, `kill`,
  `chmod`, `npm`, `pip`, `sudo`, ...), `git push`/`clean`/`reset`/`commit`/...,
  `systemctl start`/`stop`/`restart`/`enable`/`disable`, `tail -f`, y todo lo
  que mute ficheros, servicios o el sistema. Si necesitas mutar ficheros dentro
  de una raiz autorizada, usa `delegar_ingenieria` con `perfil="editar"`.
- El lote se ejecuta en orden y se DETIENE en el primer comando que falle
  (stop-on-fail). Una sola confirmacion cubre todo el lote.

Allowlist de binarios (SOLO LECTURA):
- Ficheros: `ls`, `cat`, `head`, `tail` (sin `-f`), `wc`, `nl`, `file`, `stat`,
  `realpath`, `basename`, `dirname`, `tree`, `du`.
- Busqueda: `grep`, `rg`, `fd`.
- Sistema: `df`, `free`, `uptime`, `uname`, `whoami`, `id`, `date`, `hostname`,
  `nproc`, `arch`, `lsblk`, `lscpu`, `ps`.
- Git (solo lectura): `git` con `status`, `diff`, `log`, `show`, `branch`,
  `remote`, `describe`, `rev-parse`, `ls-files`, `shortlog`, `blame`, `tag`,
  `reflog`, ... (NUNCA push/pull/fetch/commit/add/reset/checkout/clean/...).
- Servicios (solo lectura, modo usuario): `systemctl` con `status`,
  `is-active`, `is-enabled`, `list-units`, `list-timers`, `show`, `cat`;
  `journalctl` (sin `-f`).

Cuando elegir esto vs otras decisiones:
- Si una automatizacion del catalogo cubre la intencion, usa `invocar` (gana el
  catalogo). Ej.: el dashboard del Castillo es `invocar castillo`, no `df`/`ps`
  sueltos.
- Si solo hay que LEER/EXPLORAR ficheros y quieres que OpenCode lea y explique,
  usa `delegar_ingenieria`/`explorar`. Usa `ejecutar_comandos` cuando lo que
  necesitas es la SALIDA EXACTA de comandos concretos de inspeccion.

---

## Validacion global

Toda respuesta es rechazada si:

1. No es JSON parseable.
2. `decision` falta o no esta en el enum.
3. Hay campos extra desconocidos en el nivel raiz o en sub-objetos
   conocidos (objetos abiertos como `argumentos` SI admiten claves
   variables, pero validan contra la whitelist del manifiesto).
4. Algun campo obligatorio para esa decision falta.
5. Algun valor incumple su contrato (tipo, longitud, enum, regex).
6. Las referencias al catalogo (clave_automatizacion, nombre_operacion,
   argumentos) no resuelven.

Cuando una respuesta es rechazada, el Arquitecto reintenta UNA vez
reenviando al cerebro el motivo exacto del rechazo. Si el segundo
intento tambien falla, cae a fallback (Qwen) o responde al usuario una
disculpa generica.
