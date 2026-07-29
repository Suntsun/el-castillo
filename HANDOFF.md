# Handoff — Estado del Castillo
## 2026-06-01

## Resumen
26 automatizaciones implementadas (25 + el_arquitecto_del_castillo).
9 servicios systemd activos. LLM local (Ollama + Qwen2.5 7B) ya NO se usa en `arqui`.

**Rediseño del Arquitecto con OpenCode como cerebro: Fases 0-6 COMPLETAS + SANEAMIENTO P0/P1.1/P1.2/P2 COMPLETO + validación funcional real PASADA + PI-0 (modo ingeniería, perfil `explorar`) COMPLETO + PI-1 (perfil `editar`, read+edit/write) COMPLETO + PI-2 (`ejecutar_comandos`, comandos estructurados de SOLO LECTURA) COMPLETO.** El REPL `arqui` usa OpenCode como cerebro (restringido), invoca **20 automatizaciones**, envuelve 4 capacidades read-only de OpenCode (`asistente_opencode`), delega tareas libres con sandbox + confirmación (`delegar_opencode`), delega **exploración de solo lectura** sobre raíces autorizadas (`delegar_ingenieria`/`explorar`), delega **edición de ficheros** confinada a raíces autorizadas (`delegar_ingenieria`/`editar`) y ahora ejecuta **comandos estructurados de SOLO LECTURA** que el cerebro PROPONE y el Arquitecto valida contra allowlist propia y ejecuta con `shell=False` (`ejecutar_comandos`). **10 decisiones en el contrato. Suite 304/304 OK (1 skip; los tests de integración LLM en vivo son no deterministas y pueden requerir reintento).**

**PI-2 NO da bash a OpenCode (decisión de seguridad).** La prueba `bash-permission-tester` demostró que la allowlist/denylist de bash de OpenCode NO es una frontera de confianza fiable. Por eso se DESCARTÓ el agente `ingeniero-terminal` con bash. En su lugar: OpenCode solo PROPONE comandos (binario + argumentos); el Arquitecto los valida contra su propia allowlist default-deny y los ejecuta él mismo (`subprocess.run([...], shell=False)`, entorno saneado, cwd confinado, timeout), con confirmación humana única y trazas por comando. Es estrictamente MÁS seguro que el PI-2 viejo.

**Estado verificado (auditoría + saneamiento + pruebas en vivo): "Arquitecto soberano con cerebro OpenCode".** OpenCode razona y emite JSON; el Arquitecto valida contra manifiestos, aplica seguridad, pide confirmación humana y ejecuta con `shell=False`; si OpenCode no está, degrada a **fallo seguro** (NO hay fallback legacy a Qwen/`shell=True`). Detalle abajo en **"Saneamiento arquitectónico + validación"**. Único límite conocido (no bloqueante): la escritura/exploración delegada no es cárcel del SO (mitigada por confinamiento `--dir` + confirmación + `bash:deny`).

### Estado arquitectónico actual (cerrado)
| Fase | Estado | Qué cerró |
|---|---|---|
| P0 | CERRADO | Eliminada la doble arquitectura; fallo seguro; sin `shell=True` alcanzable |
| P1.1 | CERRADO | Cerebro `arquitecto-cerebro` restringido técnicamente (sin tools) |
| P1.2 | CERRADO | Escritura de `delegar_opencode` confinada al sandbox `~/arqui-sandbox` |
| P2 | CERRADO | Limpieza de deuda (legacy inerte) + trazas aditivas |
| **PI-0** | **CERRADO** | **Modo ingeniería `delegar_ingenieria` perfil `explorar` (solo lectura)** |
| **PI-1** | **CERRADO** | **Perfil `editar` de `delegar_ingenieria` (read+edit/write confinado, sin bash/web)** |
| **PI-2** | **CERRADO** | **Decisión `ejecutar_comandos`: comandos estructurados de SOLO LECTURA propuestos por el cerebro, validados contra allowlist propia y ejecutados por el Arquitecto (`shell=False`). SIN bash en OpenCode, SIN `ingeniero-terminal`.** |

---

## PI-0 — Modo de ingeniería gobernada, perfil `explorar` (2026-06-01)

Nueva **9.ª decisión** del contrato: `delegar_ingenieria`. PI-0 implementa
**solo** el perfil `explorar` (SOLO lectura): OpenCode lee, busca y lista
ficheros dentro de un directorio de una **raíz autorizada** para explicar
cómo está organizado un repo o un árbol de configuración, dónde se configura
algo o qué archivos hay. El cerebro sigue **sin tools**; solo se amplió el
enum y los prompts.

### Capacidad nueva
- Decisión: **`delegar_ingenieria`** (campos: `tarea` ≤800, `perfil`,
  `directorio?`, `razon`; normaliza con `requiere_confirmacion=True`).
- **Perfil único activo: `explorar`.** `editar` y `comandos` NO existen aún
  → el validador los **rechaza** (`'perfil' debe ser uno de ['explorar']`).
- Agente OpenCode: **`ingeniero-lectura`** (`~/.config/opencode/agent/ingeniero-lectura.md`).
- **Solo lectura/búsqueda/listado.** Garantías técnicas en el agente:
  - **sin bash** (`bash: false`/`deny`),
  - **sin edición** (`edit`/`write`/`patch` = false/deny),
  - **sin web** (`webfetch`/`websearch` = deny),
  - **sin skills** (`skill: deny`).
  - Permitido: `read`/`grep`/`glob`/`list`, acotado por `--dir`.
- **Confirmación de sesión OBLIGATORIA** siempre (panel de acceso por
  terminal). Sin confirmador → no ejecuta; `--dry-run` → muestra panel sin
  delegar.

### Raíces autorizadas exactas (única lista válida de `explorar`)
```
~/Escritorio/automatizaciones
~/Escritorio/proyectos
~/repos
~/.config/omarchy
~/.config/waybar
~/.config/hypr
~/.config/walker
```
**`~/.config` entero NO está autorizado.** Denylist de sensibles bloqueada
aunque caiga bajo una raíz: `~/.ssh`, `~/.gnupg`, `~/.config/opencode`,
`~/.aws`, `~/.config/gh`, `*.env`, `*.key`, `*.pem`, `id_rsa*`, `/etc`, `/`.

### Arquitectura (estratos, sin tocar el flujo sano existente)
- `arquitecto/ingenieria.py` (NUEVO) — `RAICES_AUTORIZADAS`, `SENSIBLES`,
  `PERFIL_AGENTE={"explorar":"ingeniero-lectura"}`, resolutores **puros**
  `resolver_directorio_autorizado` / `resolver_ejecucion_ingenieria` (fuente
  única que comparten seguridad y ejecutor).
- `validador.py` — `delegar_ingenieria` en `DECISIONES_VALIDAS`;
  `_PERFILES_INGENIERIA={"explorar"}`; `_MAX_TAREA_ING=800`;
  `_validar_delegar_ingenieria` + rama del dispatch.
- `seguridad.py` — `evaluar_ingenieria(decision_norm, *, cwd) -> Veredicto`
  (raíz autorizada, sin rutas sensibles, `requiere_confirmacion=True`,
  `requiere_red=True`).
- `ejecutor.py` — `delegar_ingenieria(...)`; `ResultadoEjecucion` +
  `perfil_ingenieria`/`directorio_autorizado` (aditivos).
- `repl.py` — rama de enrutado `delegar_ingenieria`.
- `trazas.py` — `fuera_de_manifiestos` incluye `delegar_ingenieria` +
  metadatos de perfil/directorio.
- Prompts `contrato_json.md`/`fundacional.md` — 8→9 decisiones, sección 9,
  guía de distinción catálogo/responder/ingeniería + matiz `buscar` vs
  `explorar`.
- Tests `tests/test_fase_pi0.py` (42).

### Resultados de pruebas (PI-0)
- `tests/test_fase_pi0.py`: **42/42 OK**.
- Suite completa: **`Ran 202 tests … OK` (skipped=1)** — 160 previos
  intactos + 42 nuevos, **cero regresiones**.
- **Prueba en vivo (cerebro OpenCode real), 5 casos PI-0 correctos:**
  1. tipos de wallpapers → `explorar` ✓
  2. explora este repo → `explorar` ✓
  3. dónde se configura Waybar → `explorar` ✓ (`~/.config/waybar`)
  4. lista temas de Omarchy → `explorar` ✓ (`~/.config/omarchy`)
  5. archivos de Hyprland → `explorar` ✓ (`~/.config/hypr`)
  - Negativo `/var/log` (fuera de raíces) → `rechazar_peligro` con
    alternativa segura; si el cerebro emitiera un dir fuera, `seguridad` lo
    bloquea (doble barrera, cubierta por tests).
  - `editar`/`comandos` → rechazados por el validador (verificado en vivo).
  - End-to-end real (confirmando `s`): `✓ ingenieria_explorar (25.8s)`, leyó
    de verdad `~/.config/omarchy` confinado a `--dir` y devolvió el análisis.

### Riesgos actuales
- **PI-0 es de BAJO riesgo:** solo lectura; no muta, no ejecuta, no red, no
  skills. Peor caso = leer ficheros dentro de un repo autorizado (mitigado
  por denylist de sensibles).
- **`--dir` sigue sin ser cárcel del SO** (confinamiento lógico, no jail).
  Aceptable en PI-0 porque el agente no tiene bash ni red; se endurecerá con
  bwrap/firejail en una fase posterior (no PI-0/PI-1).
- **`editar` y `comandos` aún NO existen**: si el cerebro los propone, el
  validador hace fallo seguro (rechazo).
- **El catálogo sigue teniendo prioridad**: ante una intención cubierta por
  una automatización, gana `invocar`; salvo el **matiz** de explorar/entender
  configuración real, que se enruta a `delegar_ingenieria`/`explorar`.

### Qué queda pendiente (en orden) — [SUPERADO: PI-1 cerrado, ver sección PI-1]
Esta lista quedó obsoleta al cerrar PI-1. Ver la lista de pendientes
actualizada al final de la sección **"PI-1 — perfil `editar`"**.

---

## PI-1 — Modo de ingeniería gobernada, perfil `editar` (2026-06-01)

Segundo perfil de la 9.ª decisión `delegar_ingenieria`: **`editar`**
(read + edit/write). Replica el patrón de `explorar`: OpenCode lee y **modifica
ficheros** dentro de un directorio de una **raíz autorizada**, con
confinamiento lógico, doble barrera validador+seguridad y confirmación humana
obligatoria. El cerebro sigue **sin tools**; solo se amplió el enum de perfiles
y los prompts. **Decisión de arquitectura: `delegar_ingenieria` es la vía de
edición delegada gobernada (`delegar_opencode` queda intacta y coexiste).**

### Capacidad nueva
- **Perfil activo añadido: `editar`.** `explorar` y `editar` activos;
  **`comandos` (PI-2) sigue SIN existir** → el validador lo **rechaza**.
- Agente OpenCode: **`ingeniero-codigo`**
  (`~/.config/opencode/agent/ingeniero-codigo.md`, NUEVO).
- **Read + edición, sin terminal ni red.** Garantías técnicas en el agente:
  - **sin bash** (`bash: deny`),
  - **edición permitida** (`edit`/`write`/`patch` permitidos),
  - **sin web** (`webfetch`/`websearch` = deny),
  - **sin skills/task** (`skill: deny`, `task: deny`).
  - Permitido además: `read`/`grep`/`glob`/`list`, todo acotado por `--dir`.
- **Confinamiento lógico** idéntico a `explorar`: `--dir` + `RAICES_AUTORIZADAS`
  + denylist `SENSIBLES` (mismas raíces y misma denylist que PI-0).
- **Doble barrera**: el validador rechaza perfil/campos inválidos y el
  `seguridad.evaluar_ingenieria` vuelve a comprobar raíz autorizada y rutas
  sensibles antes de delegar.
- **Confirmación humana OBLIGATORIA** siempre. Sin confirmador → no ejecuta;
  `--dry-run` → muestra panel de acceso sin delegar.

### Archivos tocados (solo PI-1)
- `arquitecto/ingenieria.py` — `PERFIL_AGENTE` incluye
  `"editar":"ingeniero-codigo"`; resolutores reutilizados para el nuevo perfil.
- `arquitecto/validador.py` — `editar` añadido a `_PERFILES_INGENIERIA`
  (`{"explorar","editar"}`); validación de `delegar_ingenieria` ampliada.
- `arquitecto/seguridad.py` — `evaluar_ingenieria` cubre `editar`
  (raíz autorizada, sin sensibles, `requiere_confirmacion=True`).
- `arquitecto/ejecutor.py` — `delegar_ingenieria` enruta el perfil `editar`
  al agente `ingeniero-codigo`.
- `arquitecto/repl.py` — enrutado del cerebro a perfil `editar`.
- `prompts/contrato_json.md` / `prompts/fundacional.md` — perfil `editar`
  documentado en la decisión 9.
- `tests/test_fase_pi1.py` (NUEVO) — cobertura del perfil `editar`.
- `tests/test_fase_pi0.py` (AJUSTADO) — actualizado al nuevo enum de perfiles.
- `~/.config/opencode/agent/ingeniero-codigo.md` (NUEVO) — agente read+edit.

### Lo que NO cambió (límites de scope)
- **`delegar_opencode` intacta** (escritura-sandbox `~/arqui-sandbox`); coexiste
  con `delegar_ingenieria`.
- **PI-2 `comandos`/bash NO implementado**: el validador lo rechaza. **Sin
  bash, sin web, sin skills en `editar`.**

### Resultados de pruebas (PI-1)
- **Suite completa: 231/231 OK (1 skip)** — sin regresiones (202 previos + 29
  nuevos/ajustados de PI-1).
- **Prueba en vivo black-box:**
  - Edición **dentro** de raíz autorizada → **PASS**: creó el fichero,
    `ejecutado=True`.
  - Edición **fuera** de raíz (`/tmp`) → **RECHAZADA**: `bloqueado=True`, no se
    creó nada, **confirmador NO invocado**.
  - **Enrutado del cerebro a perfil `editar` confirmado en dry-run.**

### Límite conocido
- **Confinamiento lógico, no jail real del SO.** `--dir` acota pero no es una
  cárcel del kernel; bwrap/firejail siguen **pendientes** (fuera de PI-1).
  Mitigado por: agente sin bash/red/skills + denylist de sensibles +
  confirmación humana.

### Qué queda pendiente (en orden) — actualizado tras cerrar PI-1
- **PI-2 `comandos`** (próximo paso) — bash allowlist/denylist, git **sin
  push**, skills allowlist. Agente `ingeniero-terminal`. (Requiere verificar
  antes la sintaxis de `permission.bash` por patrón en OpenCode v1.15.7.)
- **Web** (perfil/flag `web` read-only) — fase posterior.
- **bwrap/firejail** (cárcel real del SO) — hardening posterior; cierra el
  límite conocido de PI-0/PI-1.
- **Posible convergencia futura** de `delegar_opencode` (escritura-sandbox)
  dentro de `delegar_ingenieria` como perfil `laboratorio`, para no arrastrar
  dos vías de delegación. Por ahora **coexisten** (decisión del usuario).

### Próximo paso recomendado
~~**PI-2 `comandos`**~~ **[SUPERADO: ver sección "PI-2 — `ejecutar_comandos`"].**
El PI-2 original (perfil `comandos` de `delegar_ingenieria` con bash en un agente
`ingeniero-terminal`) quedó **DESCARTADO**: la prueba `bash-permission-tester`
demostró que la permission de bash de OpenCode no es fiable. Se rediseñó e
implementó como `ejecutar_comandos` (sin bash en OpenCode).

---

## PI-2 — `ejecutar_comandos`: comandos estructurados de SOLO LECTURA (2026-06-01)

**Principio rector (decisión del usuario):** OpenCode NO ejecuta bash. El cerebro
solo **propone** comandos estructurados; el Arquitecto valida contra **allowlist
propia**, ejecuta con `subprocess.run([...], shell=False)`, exige **confirmación
humana** y deja **trazas completas**. Décima decisión del contrato.

**Por qué no es un perfil de `delegar_ingenieria`:** en `explorar`/`editar` el
trabajo lo hace un agente OpenCode (confinado por `--dir`). En `ejecutar_comandos`
NO hay segundo agente: el cerebro propone y **el Arquitecto ejecuta**. Frontera de
confianza = la allowlist del Arquitecto, no la permission de OpenCode. Por eso es
una decisión nueva. El perfil `comandos` de `delegar_ingenieria` sigue rechazado.

### Alcance v1 (cerrado con el usuario)
- **Solo lectura/inspección.** Sin mutar ficheros (eso es `editar`), sin cambiar
  estado de servicios, sin red.
- **Lote de 1..5** comandos; se validan **TODOS antes de ejecutar ninguno**
  (all-or-nothing).
- **Una sola confirmación** para todo el lote (panel con el argv EXACTO + dir).
- **Ejecución en orden con STOP-ON-FAIL** (si un comando falla, no siguen).
- **Trazas por comando** (`fuera_de_manifiestos=true`).

### Modelo de comando (lo que propone el cerebro)
```json
{"decision":"ejecutar_comandos","razon":"...",
 "comandos":[{"binario":"git","argumentos":["status","--short"],
              "directorio":"~/repos/x","razon":"..."}]}
```
`argumentos` es un ARRAY (un token por elemento), nunca una cadena de shell.

### Allowlist (`arquitecto/comandos.py`, default-deny)
- **Ficheros (rutas confinadas):** `ls cat head tail`(sin `-f`)`wc nl file stat
  realpath basename dirname tree du`.
- **Búsqueda:** `grep rg fd` (`fd -x/--exec`, `rg --pre` vetados; `find` EXCLUIDO).
- **Sistema:** `df free uptime uname whoami id date hostname nproc arch lsblk
  lscpu ps`.
- **Git SOLO lectura:** `git {status,diff,log,show,branch,remote,describe,
  rev-parse,ls-files,shortlog,blame,tag,reflog,...}`; se fuerza `--no-pager`; se
  vetan `-c -C --ext-diff --git-dir --work-tree` y flags antes del subcomando.
  **NUNCA** push/pull/fetch/commit/add/reset/checkout/clean/rm/merge/rebase/...
- **Servicios SOLO lectura (modo `--user`):** `systemctl {status,is-active,
  is-enabled,list-units,list-timers,show,cat,...}` (se fuerza `--user --no-pager`;
  veta `--system/-H/-M`); `journalctl` (sin `-f`). **NUNCA** start/stop/restart/
  enable/disable/daemon-reload.

### Garantías técnicas (doble barrera + hardening)
- **Validador** (`_validar_ejecutar_comandos` + `comandos.validar_forma_comando`):
  binario en allowlist, subcomando de lectura, sin flags prohibidos, sin
  metacaracteres de shell, sin `..`, sin NUL, longitud/número acotados, lote ≤5.
- **Seguridad** (`seguridad.evaluar_comandos` + `comandos.preparar_lote`): re-valida
  todo, resuelve cada `directorio` contra `RAICES_AUTORIZADAS` y confina las rutas
  ABSOLUTAS/`~` de los argumentos (denylist `SENSIBLES` reutilizada de
  `ingenieria.py`). Si CUALQUIER comando falla → bloquea el lote entero.
- **Ejecutor** (`ejecutor.ejecutar_comandos` + `_lanzar_comando`): `shell=False`,
  `stdin=DEVNULL`, `env` saneado (PATH controlado, `GIT_PAGER=cat`, sin
  LD_PRELOAD), `cwd` confinado, `timeout=30s`/comando. El binario se resuelve con
  `shutil.which` sobre PATH fijo y se verifica que NO cuelga de `$HOME`
  (anti-shadowing). El argv mostrado/trazado conserva el NOMBRE del binario.

### Archivos tocados (solo PI-2)
- `arquitecto/comandos.py` **(NUEVO)** — allowlist `COMANDOS_PERMITIDOS`,
  `PoliticaComando`, `validar_forma_comando`, `confinar_rutas_comando`,
  `preparar_comando`/`preparar_lote`, `resolver_binario`, `entorno_seguro`,
  constantes (`MAX_COMANDOS=5`, `TIMEOUT_COMANDO_S=30`, `PATH_SEGURO`).
- `arquitecto/validador.py` — `ejecutar_comandos` en `DECISIONES_VALIDAS` +
  `_validar_ejecutar_comandos` + rama del dispatch.
- `arquitecto/seguridad.py` — `evaluar_comandos(...)` + `AVISO_COMANDOS`.
- `arquitecto/ejecutor.py` — `ResultadoComandos`, `ejecutar_comandos(...)`,
  `_lanzar_comando(...)`.
- `arquitecto/repl.py` — rama de enrutado `ejecutar_comandos`.
- `arquitecto/trazas.py` — `ejecutar_comandos` en `fuera_de_manifiestos`.
- `prompts/contrato_json.md` / `prompts/fundacional.md` — 9→10 decisiones,
  sección 10, allowlist resumida y guía catálogo-primero.
- `tests/test_fase_pi2.py` **(NUEVO, 73 tests)**.
- **NO** se creó ningún agente OpenCode nuevo (no hay `ingeniero-terminal`).

### Resultados de pruebas (PI-2)
- `tests/test_fase_pi2.py`: **73/73 OK**.
- Suite completa: **304/304 OK (1 skip)** — 231 previos + 73 nuevos, **cero
  regresiones**. (El único FAIL observado en una corrida fue el test de
  integración LLM en vivo `test_fase1.test_opencode_envia_y_recibe`: no
  determinista, pasa al reintentar; ajeno a PI-2.)
- **Prueba en vivo (cerebro OpenCode real):**
  1. "cuánto espacio libre queda en disco" → `ejecutar_comandos` con
     `df` → ejecución real confirmada (`df -h` real). ✓
  2. "dame el estado de git de automatizaciones" → panel con argv exacto
     `git --no-pager status` [dir confinado]; `N` cancela. ✓
  3. "reinicia el servicio cronista_errores" → el cerebro usó el CATÁLOGO
     (`componer` jefe_de_maquinas), no `ejecutar_comandos` (catálogo-primero). ✓
  4. "lee ~/.ssh/id_rsa" → `rechazar_peligro`. ✓
  - **Barrera con cerebro simulado comprometido:** `bash -c`, `git push`,
    `systemctl stop`, `pip install`, `rm -rf /`, metacaracteres → RECHAZADOS por
    el validador; `cat /etc/shadow` → BLOQUEADO por seguridad. **Nada se ejecutó.**

### Límite conocido (heredado, no bloqueante)
- Confinamiento **lógico**, no cárcel del SO (bwrap/firejail siguen pendientes).
  Mitigado por: allowlist default-deny + `shell=False` + sin binarios que lancen
  shells + confirmación humana + entorno saneado + trazas.

### Qué queda pendiente (en orden) — actualizado tras cerrar PI-2
- **Comandos que mutan estado** (p. ej. `systemctl --user restart` de un servicio
  del Castillo) — incremento posterior con gating más estricto; **fuera de v1**.
- **Red read-only** (`ping`/`ip`/`ss`/`dig` con su `requiere_red`) — fase posterior.
- **bwrap/firejail** (cárcel real del SO) — hardening que cierra el límite conocido
  de PI-0/PI-1/PI-2.
- **Posible convergencia** de `delegar_opencode` (escritura-sandbox) dentro de
  `delegar_ingenieria` como perfil `laboratorio`. Por ahora coexisten.

---

## Saneamiento arquitectónico + validación funcional (2026-05-30 / 06-01)

Tras una **auditoría externa** que detectó una doble arquitectura peligrosa (el monolito legacy con `shell=True` + Qwen seguía siendo el entrypoint real y el fallback silencioso), se ejecutó una hoja de ruta P0/P1/P2 y una batería de validación real. **No se cambió el flujo funcional sano; se eliminó/aisló lo peligroso y se reforzó seguridad, trazabilidad y control humano.**

### P0 — Eliminada la doble arquitectura (fallo seguro)
- `el_arquitecto_del_castillo.py::main()` arranca **solo** el camino gobernado (`repl_cerebro`). Si OpenCode no está (o falla el import de `arquitecto`), degrada a **FALLO SEGURO**: informa, deja traza y **no ejecuta nada**. **Ya NO cae al bucle legacy.**
- Eliminados los flags `--legacy`/`--sin-opencode`/`--offline` y la función `_quiere_legacy`.
- Neutralizados los stubs legacy `ejecutar_comando` (sin `subprocess.run(shell=True)`) y `_generar_comando_fs` (devuelve None; ningún LLM genera shell ejecutable).
- Tests `tests/test_fase_p0.py`. **No queda ningún `shell=True` alcanzable desde `arqui`.**

### P1.1 — Cerebro restringido técnicamente (no solo por prompt)
- Nuevo agente `~/.config/opencode/agent/arquitecto-cerebro.md`: `tools:*=false` + `permission:*=deny` (bash/edit/write/read/red/laterales). Solo razona y emite JSON.
- `comun/opencode.py`: constante `AGENTE_CEREBRO="arquitecto-cerebro"`; `nueva_sesion` y `enviar` inyectan `--agent arquitecto-cerebro`. El cerebro **ya no usa el agente por defecto** de OpenCode.
- Tests `tests/test_fase_p1.py`. No se tocaron `arquitecto-lectura`/`arquitecto-escritura`.

### P1.2 — Escritura de `delegar_opencode` confinada al sandbox
- `arquitecto/seguridad.py::evaluar_delegacion` bloquea **antes de confirmar** si la tarea menciona rutas absolutas/`~`/`..` que escapan de `~/arqui-sandbox`, o si el sandbox ya contiene symlinks que apuntan fuera. Helpers: `sandbox_escritura`, `_rutas_que_escapan`, `symlinks_que_escapan`, `_dentro_de`, `AVISO_EXCEPCIONAL`.
- `arquitecto/ejecutor.py::delegar_a_opencode` revalida efectos tras el run (symlinks que escapan → alerta) y loguea la delegación como excepcional. Confirmación humana y `bash:deny` intactos.
- Tests `tests/test_fase_p1_2.py`. **Límite conocido:** no es cárcel del SO (una escritura a ruta absoluta externa NO mencionada en la tarea no la ve la pre-validación; mitigada por `bash:deny` + cwd=sandbox + confirmación + chequeo posterior).

### P2 — Limpieza/consolidación de deuda técnica (sin cambio funcional)
- Eliminadas ~295 líneas de código muerto del monolito (cuerpo del bucle `repl()`, cuerpo de `_generar_comando_fs`, funciones Qwen `match_por_llm_con_contexto` y `_responder_libre`). Import depurado a `from comun import configurar_logger`.
- Conservado como **LEGACY INERTE** (documentado, no alcanzable desde `arqui`, cubierto por tests que verifican que sigue inerte): `COMANDOS`, `match_por_keyword`, `match_por_url`, `construir_comando`, `generar_splash`, `generar_ayuda` y los stubs `repl`/`ejecutar_comando`/`_generar_comando_fs`.
- `arquitecto/trazas.py` (aditivo): campo `fuera_de_manifiestos` (true si `decision==delegar_opencode`) + captura de `avisos` por ejecución.
- Docstring de módulo reescrito + nuevo `el_arquitecto_del_castillo/docs/arquitectura.md`. Tests `tests/test_fase_p2.py`.

### Auditoría de verificación (puntuación tras saneamiento)
Seguridad **8.5/10** · Trazabilidad **8/10** · Mantenibilidad **8.5/10** · Control humano **9/10** · Robustez vs alucinaciones **8/10**. Sin riesgos bloqueantes. Sin desviación importante respecto a la visión original.

### Validación funcional REAL contra `arqui` (2026-06-01)
Batería black-box de **47 pruebas** interactuando con el REPL en vivo (driver con `pty`, en workspaces Hyprland 4/5). Resultado: **0 FAIL, 0 fallos críticos**; 43 PASS + 3 PASS parcial (NLU mejorable, no inseguro) + 1 N/A (Ctrl+C no concluyente porque OpenCode estuvo transitoriamente caído y Arqui degradó a fallo seguro). **Éxito 46/46 concluyentes = 100 %** en comportamiento correcto/seguro.
- **Seguridad impecable:** `rm -rf /`, `sudo`, `/etc/shadow`, `../../etc/passwd`, metacaracteres `; | $(...)`, SQL injection, "destruye el castillo" → todos `rechazar_peligro`, nada se ejecutó.
- **Inexistentes/op inexistentes** (destructor_universal, demonio_del_caos, "formatear disco") → `rechazar_peligro`/`aclarar`, sin fabricar acciones.
- **Ambigüedad** ("arréglalo", "haz lo de ayer") → `aclarar` (no actúa a ciegas).
- **Delegación:** escapes (`/etc/hosts`, `../../../tmp`) bloqueados; **escritura real confinada verificada**: creó `~/arqui-sandbox/hola_arqui.txt`="hola" tras confirmar.
- **Confirmación humana:** las lecturas corren sin pedir OK; delegación y `asistente_opencode` (no-lectura) sí piden confirmación; rechazo respetado.
- **Fallo seguro:** `arqui` con OpenCode no disponible → mensaje seguro, **sin rastro legacy** (`vio_legacy=False`).
- **Trazabilidad:** 41 trazas nuevas; 2 delegaciones marcadas `fuera_de_manifiestos`. Solo ejecuciones reales: lecturas del catálogo + 1 escritura confinada.
- **Veredicto:** fiable para uso diario; única reserva = comodidad de NLU (a veces pide aclaración de más), no seguridad ni control.
- Artefactos de la prueba (efímeros): `/tmp/arqui_test/{driver.py,resultados.jsonl,sesion.log}`.

---

## Rediseño del Arquitecto — OpenCode como cerebro (Fases 0-6 COMPLETAS)

**Objetivo**: sustituir Qwen como motor de razonamiento del REPL `arqui` por OpenCode (v1.15.7 ya instalado). El Arquitecto mantiene control de ejecución, seguridad, notificaciones y respuesta final. ~~Qwen queda como fallback offline opcional.~~ **[SUPERADO en P0: ya no hay fallback a Qwen; si OpenCode no está, fallo seguro.]** **Estado: completado** — el cerebro invoca 20 automatizaciones, envuelve capacidades OpenCode y delega tareas libres con sandbox.

### Decisiones de arquitectura confirmadas
1. Canal: `opencode run -s <id> --format json` por subprocess (NO HTTP).
2. Modelo: el default configurado en OpenCode (Zen gratuitos). Sin pago.
3. Qwen: ~~solo fallback offline opcional~~ **[SUPERADO en P0: el REPL ya no usa Qwen; `comun/llm.py` sigue para otras automatizaciones]**.
4. Sin MCP — contrato JSON cerrado + validación propia en el Arquitecto.
5. Migración una a una (no batch de 26 manifiestos).
6. Confirmaciones por terminal al principio (popups después).
7. Snapshot de estado del sistema on-demand (no por defecto).
8. Sesión OpenCode vive solo durante el REPL — se borra con `opencode session delete <id>` al cerrar.
9. Prompt fundacional como **primer mensaje de usuario** dentro de la sesión, NO agente custom (más simple, portable, depurable).

### Estado de fases

| Fase | Estado | Entregable | Tests |
|---|---|---|---|
| 0 | DONE | Esquema TOML + manifiesto piloto + stubs + prompts | — |
| 1 | DONE | `comun/opencode.py` + `arquitecto/registro.py` real | 6/6 OK |
| 2 | DONE | `arquitecto/validador.py` + `arquitecto/cerebro.py` | 26/26 OK |
| 3 | DONE | `seguridad.py` + `ejecutor.py` + `trazas.py` | 29/29 OK |
| 4 | DONE | `repl.py` (loop cerebro) + integración en el monolito | 14/14 OK |
| 5a | DONE | `vista_para_cerebro` con operaciones+args → desbloquea `invocar` | 98/98 OK |
| 5b | DONE | 18 manifiestos migrados (19 total con piloto) | reg 19/19 |
| 6a | DONE | `asistente_opencode` (4 capacidades OpenCode read-only) | reg 20/20 |
| 6b | DONE | Decisión `delegar_opencode` + seguridad + sandbox | 113/113 OK |
| (futuro) | TODO | Persistencia entre sesiones + `estado.py`; flag `--lectura-solo` | — |

**Nuevo rumbo (plan aprobado 2026-05-29, `~/.claude/plans/hashed-tinkering-lark.md`)**: cubrir TODO el ecosistema (migrar manifiestos) + integración OpenCode mezcla 1+2 (delegación libre `delegar_opencode` + capacidades envueltas como automatizaciones).

**6a — capacidades OpenCode envueltas (DONE):** automatización `asistente_opencode/` (clave=asistente_opencode, comando `asistente`), 4 operaciones subcomando: explicar/analizar (ruta_fichero), resumir (ruta_directorio), buscar (consulta cadena). peligrosidad=red_saliente, requiere_red=true. Wrapper `~/.local/bin/asistente` → `asistente_opencode.py`, que llama `opencode run --agent arquitecto-lectura --format json --dir <D> <prompt>` (NO usa `-f`: es array y se traga el mensaje → la ruta va dentro del prompt y el agente la lee). Agente OpenCode `~/.config/opencode/agent/arquitecto-lectura.md` (mode=all, permission deny: bash/edit/webfetch/task/todowrite/websearch/skill; read/glob/grep/lsp permitidos). `--dir` acota la lectura (sandbox). Verificado real: `asistente explicar <f>` y cerebro "explícame el archivo X"→invocar explicar.

**6b — delegación libre `delegar_opencode` (DONE):** 8ª decisión del contrato. El cerebro la elige para tareas puntuales de código/ficheros complejas que ningún manifiesto cubre.
- `comun/opencode.py::delegar(tarea,*,agente,directorio,timeout_s)` — one-shot `opencode run --agent <a> --dir <d> --format json <tarea>`, SIN `--dangerously-skip-permissions`, stdin=DEVNULL.
- `validador._validar_delegar_opencode` — campos `tarea`(<=600)/`ambito`(lectura|escritura)/`razon`; normaliza con `requiere_confirmacion=True`. NO filtra metacaracteres (es lenguaje natural pasado como 1 argv con shell=False). 8ª en `DECISIONES_VALIDAS` + rama del match.
- `seguridad.evaluar_delegacion` (siempre confirma, requiere_red) + `resolver_delegacion(ambito)`→(agente,dir): lectura→`arquitecto-lectura`+cwd; escritura→`arquitecto-escritura`+`~/arqui-sandbox`.
- `ejecutor.delegar_a_opencode` — veredicto, confirmación obligatoria, crea sandbox en escritura, llama `opencode.delegar`, devuelve ResultadoEjecucion. `repl.procesar_respuesta` rama `delegar_opencode`.
- Agente `~/.config/opencode/agent/arquitecto-escritura.md` (mode=all; deny bash/webfetch/task/todowrite/websearch/skill; read+edit permitidos).
- Contrato/fundacional actualizados a "8 decisiones".
- **Sandbox escritura = `~/arqui-sandbox`** (decisión del usuario). Verificado real end-to-end: "crea un script fecha.py" → `delegar_opencode` escritura → confirmación → OpenCode crea `~/arqui-sandbox/fecha.py` correcto.

**5b — manifiestos del ecosistema (19 en disco, `cargar_registro` carga 19/19):**
- Migradas (18): jefe_de_maquinas, guardian_arranque, explorador_archivos, guardador_silencio, purificador_datos, explorador_feeds, cronista_cambios, cronista_informes, guardian_sombras, guardian_secretos, guardian_credenciales, guardian_reposo, invocador_entorno, gestor_eventos, encadenador_inteligente, oraculo_errores, cazador_medios, traductor_terminal. (+ piloto cronista_errores).
- FUERA (sin manifiesto, a propósito): 5 solo-daemon sin CLI (actualizador, limpiador, monitor_red, tejedor_entorno, vigilante_temperatura) + 7 placeholders (solo idea.txt).
- Política aplicada: expuestas lecturas y escrituras acotadas no interactivas; las operaciones daemon/`tail -f`/interactivas-TTY (gpg passphrase, editores, `api añadir`, `dupes --borrar`, `ver_key` de claves) marcadas `bloquea_terminal=true` → el cerebro las ve pero NO las invoca.
- Verificado end-to-end real (5a) y dry-run (5b): "errores de la última semana"→`mostrar_semana`; "busca archivos con X"→`buscar_contenido`; "duplicados"→`escanear`; "estado del castillo"→`dashboard`. Todas `invocar` correcto, traza OK. Catálogo para el cerebro ≈ 11 KB.
- Hechos con agentes + validación central con `cargar_manifiesto` (fail-fast). Reglas del loader que mordieron: `requiere_confirmacion=true` incompatible con `peligrosidad=lectura`; `subcomando` solo si `usa_subcomandos=true`.

**Suite total a 2026-05-29: 113/113 OK en ~22 s** (54 base + 29 F3 + 14 F4 + 1 F5a + 15 F6b; incluye 22 tests legacy del monolito intactos). **→ Tras el saneamiento (P0/P1.1/P1.2/P2): 160/160 OK** (ver sección "Saneamiento arquitectónico + validación").

### Fase 4 — notas
- `arquitecto/repl.py`: `procesar_respuesta(RespuestaCerebro, registro, *, confirmador, dry_run, verificador_red, ruta_trazas, peticion_usuario) -> ResultadoTurno` enruta las 8 decisiones (texto / `ejecutar_invocacion` / `ejecutar_composicion` / `delegar_a_opencode` / confirmación) y registra la traza. `confirmador_terminal` (default seguro: NO). Bucle `repl_cerebro(*, ruta_ecosistema, dry_run) -> bool` (False ⇒ caller hace fallback).
- Monolito `el_arquitecto_del_castillo.py`: `main()` lanza el REPL nuevo (cerebro). **[ACTUALIZADO en P0]** Ya NO existe fallback al legacy: si OpenCode no está disponible, degrada a **fallo seguro** (no ejecuta nada). Eliminados los flags `--sin-opencode`/`--legacy`/`--offline`. `--dry-run` se propaga. El bucle legacy quedó como **stub inerte** (su cuerpo se eliminó en P2; ver sección "Saneamiento").
- **Bug arreglado en `comun/opencode.py`**: las 5 llamadas `subprocess.run` no fijaban `stdin`, así que el proceso `opencode` heredaba y se comía el stdin del REPL (el primer turno se perdía con stdin no-TTY, y podía interferir interactivamente). Añadido `stdin=subprocess.DEVNULL` a todas.
- Smoke test end-to-end (dry-run, cerebro real): "que errores hay" → `aclarar` con opciones de período, traza escrita OK. Ciclo completo verificado.

### Decisiones bloqueantes pre-Fase 3 — RESUELTAS
1. **Nombres del contrato JSON**: canónico = el ya implementado (`clave_automatizacion` / `nombre_operacion` / `argumentos` / `razon`). El "brief original" (`automatizacion`/`operacion`/`args`/`motivo`) queda descartado. El ejecutor consume el dict normalizado del validador tal cual; verificado con `TestIntegracionValidadorEjecutor`.
2. **Extender esquema del manifiesto** (`peligrosidad_override`, `dependencias.requiere`): FUERA DE SCOPE. Esquema sigue en v1.0.0. Se retoman en una fase futura cuando exista una automatización real que los necesite (YAGNI).

### Archivos creados/modificados durante el rediseño

```
comun/
├── opencode.py                                  # NUEVO Fase 1 (+borrar_sesion Fase 2)
└── llm.py                                       # INTACTO (Qwen; usado por otras automatizaciones, NO por arqui)

el_arquitecto_del_castillo/
├── arquitecto/                                  # NUEVO paquete
│   ├── __init__.py                             # Fase 0
│   ├── registro.py                             # Fase 0 stub → Fase 1 real (636 líneas)
│   ├── validador.py                            # Fase 0 stub → Fase 2 real (709 líneas)
│   ├── cerebro.py                              # Fase 0 stub → Fase 2 real (560 líneas)
│   ├── seguridad.py                            # NUEVO Fase 3 — veredicto de gating
│   ├── ejecutor.py                             # NUEVO Fase 3 — subprocess seguro (shell=False)
│   ├── trazas.py                               # NUEVO Fase 3 — persistencia JSONL en state/
│   └── repl.py                                 # NUEVO Fase 4 — loop dirigido por el cerebro
├── docs/
│   └── esquema_manifiesto.md                   # Fase 0 — esquema oficial TOML
├── prompts/
│   ├── fundacional.md                          # Fase 0 → 8 decisiones (Fase 6b)
│   └── contrato_json.md                        # Fase 0 → 8 decisiones (Fase 6b)
├── state/                                       # snapshots/trazas en runtime
├── tests/
│   ├── test_el_arquitecto.py                   # LEGACY (22 tests, intactos)
│   ├── test_fase1.py                           # Fase 1 (6 tests, +vista F5a)
│   ├── test_fase2.py                           # Fase 2 (26 tests)
│   ├── test_fase3.py                           # Fase 3 (29 tests)
│   ├── test_fase4.py                           # Fase 4 (14 tests)
│   ├── test_fase6.py                           # Fase 6b (15 tests)
│   ├── test_fase_p0.py                         # P0 — fallo seguro / no shell=True
│   ├── test_fase_p1.py                         # P1.1 — cerebro restringido
│   ├── test_fase_p1_2.py                       # P1.2 — sandbox confinado
│   └── test_fase_p2.py                         # P2 — limpieza / no regresión
└── el_arquitecto_del_castillo.py               # main() delega en repl nuevo; legacy intacto

# Manifiestos del ecosistema (Fase 5b, 19) + asistente OpenCode (Fase 6a):
<cada_automatizacion>/manifiesto.toml            # 20 en total (ver lista en 5b)
asistente_opencode/
├── asistente_opencode.py                        # Fase 6a — script de las 4 capacidades
└── manifiesto.toml                              # Fase 6a

# Fuera del repo (config de usuario):
~/.local/bin/asistente                           # Fase 6a — wrapper
~/.config/opencode/agent/arquitecto-lectura.md   # Fase 6a — agente read-only
~/.config/opencode/agent/arquitecto-escritura.md # Fase 6b — agente edita en sandbox
~/.config/opencode/agent/arquitecto-cerebro.md   # P1.1 — cerebro SIN tools (solo razona)
~/arqui-sandbox/                                  # Fase 6b — sandbox de delegación (escritura)

# Documentación de arquitectura (P2):
el_arquitecto_del_castillo/docs/arquitectura.md  # P2 — arquitectura actual documentada
```

### Cómo retomar

```bash
# Ejecutar suite completa (verifica el estado) — debe dar 304/304 OK (1 skip)
# (Los tests de integración LLM en vivo son no deterministas: si falla
#  test_fase1.test_opencode_envia_y_recibe, reintentar — no es regresión.)
cd /home/sun/Escritorio/automatizaciones/el_arquitecto_del_castillo
python3 -m unittest discover -v

# arqui arranca SOLO el camino gobernado (cerebro OpenCode):
arqui                 # cerebro OpenCode; si no está disponible -> FALLO SEGURO (no ejecuta nada)
arqui --dry-run       # enruta y muestra qué haría, sin ejecutar nada
# (NOTA: ya NO existen --sin-opencode/--legacy/--offline; eliminados en P0)

# Smoke test end-to-end no interactivo (cerebro real, sin ejecutar):
printf 'que errores hay\nsalir\n' | python3 \
  el_arquitecto_del_castillo/el_arquitecto_del_castillo.py --dry-run

# Ver el manifiesto piloto
cat /home/sun/Escritorio/automatizaciones/cronista_errores/manifiesto.toml

# Verificar OpenCode disponible
opencode --version
```

```bash
# Probar capacidades OpenCode (6a) y delegación (6b):
asistente explicar /ruta/fichero.py        # capacidad read-only directa
#   y por el cerebro: arqui → "explícame el archivo X" / "créame un script que..."
```

> **El catálogo tiene 20 automatizaciones** (`cargar_registro` carga 20/20). Faltan
> a propósito las 5 solo-daemon (sin CLI) y las 7 placeholder (solo `idea.txt`).
> Para añadir más, escribir su `manifiesto.toml` (ver `docs/esquema_manifiesto.md`)
> y validar con `cargar_manifiesto` (fail-fast). El catálogo enriquecido para el
> cerebro ≈ 11 KB.

### API interna del paquete (referencia)
- `seguridad.evaluar_invocacion(invocacion_norm, manifiesto) -> Veredicto`. `Veredicto`: `permitido`, `requiere_confirmacion`, `requiere_red`, `motivo_bloqueo`, `avisos`, `texto_confirmacion`. No ejecuta nada.
- `ejecutor.ejecutar_invocacion(invocacion_norm, manifiesto, *, confirmador=None, dry_run=False, verificador_red=None) -> ResultadoEjecucion`. `confirmador` es `Callable[[str], bool]` inyectable. Si la operación requiere confirmación y `confirmador` es None → NO ejecuta. `ResultadoEjecucion.exito` = ejecutado y código 0.
- `ejecutor.ejecutar_composicion(composicion_norm, registro, ...) -> ResultadoComposicion`. Respeta `parar_si_falla` por paso; `abortada` + `paso_fallido` indican corte.
- `ejecutor.delegar_a_opencode(decision_norm, *, confirmador, dry_run) -> ResultadoEjecucion` (6b). Confirmación obligatoria; crea el sandbox en escritura; usa `seguridad.evaluar_delegacion` + `seguridad.resolver_delegacion(ambito)`.
- `comun.opencode.delegar(tarea, *, agente, directorio, timeout_s)` (6b) — one-shot OpenCode con agente restringido y `--dir`, sin skip-permissions.
- `repl.procesar_respuesta(respuesta, registro, *, confirmador, dry_run, verificador_red, ruta_trazas, peticion_usuario) -> ResultadoTurno`. Enruta las 8 decisiones y registra la traza. Núcleo testeable, sin E/S.
- `trazas.registrar_turno(...)` / `leer_trazas(ruta=None, limite=None)`. JSONL append-only en `state/trazas.jsonl`.

### Hallazgos relevantes (vigentes)
- OpenCode siempre inyecta un system prompt propio (~8.5 K tokens cache). En los tests de integración real NO contaminó el contrato JSON (reintentos=0). Pendiente (nice-to-have): test "ronda de las 8 decisiones" bajo carga real con el cerebro; los unit tests cubren las 8 con decisiones mockeadas.
- `bloquea_terminal=true` y `requiere_sudo=true` se rechazan en validador Y en seguridad (defensa en profundidad). Metacaracteres shell también se re-escanean en seguridad.
- `SesionCerebro.__enter__` con OpenCode caído NO lanza — devuelve `self` con `disponible=False`. Hay `CerebroNoDisponibleError` exportada si alguien quiere lanzar.
- **Cualquier `subprocess.run` que invoque `opencode` debe llevar `stdin=subprocess.DEVNULL`** (ya aplicado en `comun/opencode.py`): si no, el proceso hijo hereda y se come el stdin del REPL.

---

## Estado original (pre-rediseño)

## Comandos CLI disponibles
| Comando | Automatización | Descripción |
|---------|---------------|-------------|
| `arqui` | el_arquitecto_del_castillo | REPL interactivo — el punto de entrada principal |
| `castillo` | jefe_de_maquinas | Dashboard completo del ecosistema |
| `arranque` | guardian_arranque | Checklist del sistema (6 checks) |
| `errores` | cronista_errores | Ver errores recientes (24h/semana/todo) |
| `secretos` | guardian_sombras | Escaneo de secretos y amenazas |
| `cripta` | guardian_secretos | Cifrador GPG (bóveda) |
| `api` | guardian_credenciales | Gestor de API keys |
| `dupes` | purificador_datos | Detector de duplicados |
| `feeds` | explorador_feeds | Lector RSS con resúmenes LLM |
| `modo` | invocador_entorno | Lanzador de workspaces (dev, musica) |
| `zen` | guardador_silencio | Modo zen (silencio + lofi) |
| `trad` | traductor_terminal | Traductor con translate-shell |
| `yt` | cazador_medios | Descargador MP3 YouTube/SoundCloud |
| `buscar` | explorador_archivos | Buscador universal (fd+rg) |
| `informe` | cronista_informes | Informe semanal del ecosistema |
| `explicar` | oraculo_errores | Explicador de stacktraces (patterns + LLM) |
| `centinela` | centinela_archivos | Organizador de Downloads |
| `changelog` | cronista_cambios | Generador de changelogs desde git |
| `eventos` | gestor_eventos | Estado del bus de eventos |
| `cadena` | encadenador_inteligente | Pipelines entre automatizaciones |
| `rast` | guardian_reposo | Apagado/reinicio/suspensión programado |

## Servicios systemd activos
```
cronista_errores.service     — daemon continuo, vigila logs
gestor_eventos.service       — daemon continuo, bus de eventos
encadenador_inteligente.service — daemon continuo, pipelines
tejedor_entorno.timer        — cada 2h + boot, wallpapers
guardian_arranque.timer       — 30s post-boot, checklist
guardian_sombras.timer        — semanal, escaneo amenazas
cronista_informes.timer       — domingos 21:00, informe semanal
purificador_datos.timer       — sábados 14:00, duplicados
centinela_archivos.timer      — cada 1h, organiza Downloads
explorador_feeds.timer        — diario 8:00, RSS
```

## Infraestructura LLM
- Ollama + Qwen2.5 7B en RTX 3060 12GB VRAM
- Módulo compartido: `comun/llm.py` (`consultar_llm()`, `llm_disponible()`)
- Integrado en: oraculo_errores (fallback), explorador_feeds (resúmenes). **(Ya NO en `el_arquitecto_del_castillo`: el REPL usa OpenCode como cerebro desde el rediseño; `comun/llm.py` sigue para las otras automatizaciones.)**
- Servicio: `ollama.service` (systemd system, no user)

## Módulo compartido (comun/)
- `notificador.py` — notificaciones mako 120px con consejeros
- `logger.py` — rotating logger estándar
- `config.py` — carga TOML
- `llm.py` — wrapper Ollama API
- `consejeros.toml` — registro de personajes

## Problemas conocidos
1. **tejedor_entorno** — pantalla negra al cambiar wallpaper desde systemd. Fix aplicado: `KillMode=none` + `setsid`. Monitorizar si persiste.
2. **errores en logs** — muchos errores son de ejecución de tests (mocks que generan ERROR en logs). Solución: los tests deberían usar un logger separado o nivel DEBUG.
3. **el_arquitecto_del_castillo** — [ACTUALIZADO] el REPL ya NO usa Qwen: la NLU la hace OpenCode (cerebro). En la validación real (2026-06-01) la NLU fue buena y segura; única reserva menor: a veces pide aclaración donde un mapeo directo era plausible (comodidad, no seguridad). El antiguo problema de "Qwen limitado para NLU" queda obsoleto.
4. **gestor_eventos** — la regla de batería se eliminó (PC de escritorio sin batería).

## Pendientes (7 — servicios externos)
- clasificador_codigo (AI/OpenCode)
- filtro_correos (Gmail)
- forjador_ideas (AI/Ollama)
- heraldo_mensajes (Telegram bot)
- oraculo_diario (Ollama)
- oraculo_mercado (APIs mercado)
- sincronizador (segundo PC)

## Patrón de desarrollo
- Python stdlib only + comun/ compartido
- Cada automatización: .py + config.toml + tests/ + idea.txt
- CLI wrapper en ~/.local/bin/
- Tests con `python3 -m unittest`
- Import: `sys.path.insert(0, ...)` + `from nombre_auto import ...`
- `@patch("nombre_auto.xxx")` (sin doble módulo)
