"""
Paquete interno del Arquitecto del Castillo.

Refactor en curso (Fase 0): el monolito `el_arquitecto_del_castillo.py`
se ira descomponiendo modulo a modulo dentro de este paquete. Hasta que
la migracion este completa, el monolito sigue siendo el punto de entrada
y este paquete contiene solo stubs y contratos.

Modulos planificados:
    registro    Carga y representa los manifiestos.toml del ecosistema.
    validador   Valida las decisiones del cerebro contra el registro.
    cerebro     Interfaz con OpenCode (subprocess opencode run -s <id>).
    seguridad   Veredicto de gating: bloqueo y confirmacion. (Fase 3, hecho)
    ejecutor    Lanza subprocesos de forma segura. (Fase 3, hecho)
    trazas      Persistencia JSONL de turnos. (Fase 3, hecho)
    repl        Bucle interactivo dirigido por el cerebro. (Fase 4, hecho)
    estado      Snapshot on-demand del sistema. (Fase 5)
"""
