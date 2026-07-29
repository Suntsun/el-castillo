# El Heraldo — feedback de espera del Castillo

Pieza reutilizable de UX (`comun/heraldo.py`) que ambienta la espera de las
operaciones bloqueantes con un spinner y frases medievales, y permite avisar
hitos por notificación. Cosmético y seguro: nunca añade latencia ni rompe al
llamante.

## Qué hace

- `heraldo.pensando(tema=...)` — context manager que envuelve una operación
  bloqueante:
  ```python
  from comun import heraldo
  with heraldo.pensando(tema=heraldo.tema_actual()):
      respuesta = operacion_lenta()   # el spinner gira mientras bloquea
  ```
  - Fuera de TTY (p. ej. `echo ... | arqui`, o tests por pipe): **no-op total**
    — no imprime nada, no lanza hilo, cero latencia. La salida funcional queda
    intacta.
  - En TTY: hilo aparte que pinta spinner + frase rotatoria; al salir del
    `with` para el hilo y borra la línea limpiamente. Maneja Ctrl-C sin
    ensuciar el terminal y jamás propaga errores del spinner.

- `heraldo.soldadito(consejero, mensaje, severidad, duracion)` — dispara una
  notificación de hito reutilizando `comun.notificador.notificar` (popup con la
  imagen del personaje). Útil para hitos de operaciones largas.

## Temas

- `medieval` (por defecto): frases ambientales ("El Arquitecto consulta los
  pergaminos…", "Convoca a los consejeros del castillo…", …) y glifos.
- `clasico`: réplica exacta del comportamiento previo ("Pensando...").

## Activar / desactivar el tema

Prioridad de resolución: **variable de entorno > fichero > default**.

1. Variable de entorno (puntual, por invocación):
   ```bash
   ARQUI_TEMA=clasico arqui      # desactiva el tema medieval
   ARQUI_TEMA=medieval arqui     # fuerza medieval
   ```

2. Fichero persistente:
   ```bash
   mkdir -p ~/.config/automatizaciones
   echo medieval > ~/.config/automatizaciones/tema   # o "clasico"
   ```

3. Sin env ni fichero → `medieval`. Cualquier valor inválido se sanea a
   `medieval`.

Para volver al comportamiento clásico de forma permanente:
```bash
echo clasico > ~/.config/automatizaciones/tema
```

## Personaje de los hitos (soldadito)

Por defecto usa el consejero `explorador_archivos` (existe en
`consejeros.toml`). Se puede cambiar con la variable de entorno
`ARQUI_CONSEJERO_HITO`.

## Notas de seguridad / diseño

- El spinner es puramente cosmético; el trabajo real bloquea de todos modos,
  por lo que no introduce latencia perceptible.
- Stdlib pura (`threading`, `itertools`, `time`). Sin dependencias nuevas.
- Cualquier fallo interno del Heraldo se degrada a silencioso: la operación
  envuelta y su valor de retorno nunca se ven afectados.
