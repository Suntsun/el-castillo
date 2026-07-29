# Arquitectura actual — El Arquitecto del Castillo

> Estado tras el saneamiento P0 + P1.1 + P1.2 + P2.
> Principio rector: **OpenCode piensa, el Arquitecto gobierna.**

## Resumen en una frase

Un orquestador Python **soberano** que usa OpenCode como **cerebro de
razonamiento restringido** (sin poder de ejecución directa) y que valida,
autoriza y ejecuta él mismo cada acción contra un catálogo de manifiestos,
con confirmación humana y `shell=False`.

## Entrypoint único

```
arqui  →  el_arquitecto_del_castillo.py : main()  →  arquitecto.repl.repl_cerebro
```

`~/.local/bin/arqui` lanza el monolito; `main()` arranca **únicamente** el
camino gobernado. No existe ningún otro camino de ejecución.

## Flujo gobernado (por turno)

```
Usuario
  → repl_cerebro
  → SesionCerebro.turno  →  opencode run --agent arquitecto-cerebro   [RAZONA → JSON]
  → validar_decision (validador)        [contrato + whitelist de manifiestos]
  → procesar_respuesta (repl)           [enrutado de la decisión]
  → ejecutor.ejecutar_invocacion        [resuelve manifiesto]
      → seguridad.evaluar_invocacion    [política / veredicto]
      → confirmador (si peligrosidad ≠ lectura)   [HUMANO, default = NO]
      → subprocess.run(shell=False)     [ÚNICA ejecución]
  → trazas.registrar_turno              [auditoría JSONL]
  → respuesta renderizada por el Arquitecto
```

## Roles

| Pieza | Responsabilidad |
|---|---|
| **OpenCode (cerebro)** | Interpretar lenguaje natural y emitir **un JSON** con la decisión. Corre bajo el agente `arquitecto-cerebro`, que **deniega** bash, edit/write, lectura de filesystem, red y herramientas laterales. No ejecuta nada. |
| **Arquitecto** | Autoridad de **validación** (contrato + manifiestos), **seguridad** (política, metacaracteres, sudo, rutas protegidas), **confirmación humana**, **ejecución** `shell=False` y **trazabilidad**. |
| **Humano** | Autoridad final sobre todo lo que no sea `lectura` y sobre toda delegación (confirmación explícita; el default es NO). |

## Fallo seguro

Si OpenCode no está disponible (o no se puede importar el paquete
`arquitecto`), `main()` **no ejecuta nada**: informa con un mensaje claro y
deja traza. **No** hay fallback a shell ni a ningún LLM local. Ver
`_fallo_seguro` / `_registrar_fallo_seguro`.

## `delegar_opencode` — excepción gobernada

Única vía que actúa fuera del catálogo de manifiestos. Para tareas de
código/ficheros abiertas. Salvaguardas:

- **Siempre** requiere confirmación humana.
- Agente OpenCode restringido (`arquitecto-lectura` / `arquitecto-escritura`)
  con `bash: deny`.
- Escritura **confinada** a `~/arqui-sandbox`: se bloquea (antes de
  confirmar) si la tarea menciona rutas absolutas / `~` / `..` que escapan, o
  si el sandbox ya contiene symlinks que apuntan fuera; verificación
  posterior de symlinks tras el run.
- La traza la marca con `fuera_de_manifiestos: true` y recoge sus `avisos`
  (incluido el aviso de capacidad EXCEPCIONAL).

**Límite conocido:** no es una cárcel del sistema operativo. Una escritura a
ruta absoluta externa **no mencionada** en la tarea no la detecta la
pre-validación de texto (mitigada por `bash: deny`, `cwd = sandbox`,
confirmación y chequeo posterior de symlinks).

## Código legacy inerte

El antiguo REPL por keywords (`COMANDOS`, `match_por_keyword`,
`match_por_url`, `construir_comando`, `generar_splash`, `generar_ayuda`) y
los stubs `repl` / `ejecutar_comando` / `_generar_comando_fs` **no son
alcanzables desde `arqui`** y no ejecutan nada. Se conservan solo porque las
pruebas unitarias existentes los cubren y verifican que siguen inertes. El
cuerpo ejecutable legacy (bucle, generación de shell por LLM, `shell=True`)
fue eliminado.
