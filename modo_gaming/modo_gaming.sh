#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# modo-gaming — Toggle de optimización del sistema para sesiones de juego
# Castillo de automatizaciones — ~/Escritorio/automatizaciones/modo_gaming/
#
# Caso de uso: Mount & Blade Bannerlord 2 vía Steam/Proton.
# Problema raíz: congelaciones en batallas grandes por presión de RAM.
# El SO + Chromium + Opera + agentes gastan ~5.5 GB en reposo en 16 GB totales.
#
# Uso:
#   modo-gaming on  [--yes]   Activa el modo gaming (--yes omite confirmaciones)
#   modo-gaming off           Restaura el estado previo
#   modo-gaming status        Muestra el estado actual
#
# Qué hace ON:
#   1. Idempotencia: comprueba si ya está activo y guarda estado previo.
#   2. GATE HUMANO: lista procesos pesados a suspender y pide confirmación.
#   3. Suspende (SIGSTOP) procesos pesados no esenciales: Chromium, Opera, etc.
#      (reversible en OFF con SIGCONT; preferido sobre kill para no perder datos)
#   4. sync + drop_caches (libera pagecache; pide sudo puntualmente).
#   5. Gamemode: avisa si no está instalado y cómo instalarlo.
#   6. Governor CPU: confirma/fuerza performance en todos los cores.
#   7. Hyprland: desactiva animaciones, blur y sombras vía hyprctl (runtime,
#      reversible; nota honesta: en pantalla completa el compositor ya se bypasea).
#   8. Resumen de RAM antes/después.
#
# Qué hace OFF:
#   Restaura desde el fichero de estado: SIGCONT a procesos suspendidos,
#   restaura Hyprland, limpia el estado.
# ═══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

# ── Rutas y constantes ────────────────────────────────────────────────────────

ESTADO_DIR="$HOME/.local/state/modo-gaming"
ESTADO_FILE="$ESTADO_DIR/state.json"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Procesos a suspender: nombres exactos (comm) de procesos pesados no esenciales.
# NUNCA tocar: steam, steamwebhelper, Xorg, hyprland, waybar, claude, python3,
#              systemd, pipewire, pulseaudio, dbus, NetworkManager, ni ningún
#              proceso cuyo uid sea distinto al del usuario.
PROCESOS_SUSPENDIBLES=("chromium" "opera" "opera_autoupdate" "chromium-browser" "chrome")

# ── Colores ANSI ──────────────────────────────────────────────────────────────

RESET="\033[0m"
BOLD="\033[1m"
DIM="\033[2m"
CYAN="\033[36m"
GREEN="\033[32m"
YELLOW="\033[33m"
RED="\033[31m"
BLUE="\033[34m"
MAGENTA="\033[35m"

c() { printf "%b%s%b\n" "$1" "$2" "$RESET"; }

# ── Funciones de utilidad ─────────────────────────────────────────────────────

ram_disponible() {
    # Devuelve la RAM disponible en formato humano (ej: "9.9Gi")
    free -h | awk '/^Mem:/ {print $7}'
}

ram_usada() {
    # Devuelve la RAM usada en formato humano
    free -h | awk '/^Mem:/ {print $3}'
}

ram_libre_mb() {
    # Devuelve RAM disponible en MB (para comparar antes/después)
    free -m | awk '/^Mem:/ {print $7}'
}

governor_actual() {
    cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null || echo "desconocido"
}

hypr_opcion() {
    # Lee una opción de Hyprland y devuelve el valor numérico (0 o 1)
    # Uso: hypr_opcion "animations:enabled"
    hyprctl getoption "$1" 2>/dev/null | awk '/^int:/ {print $2}'
}

gamemode_instalado() {
    command -v gamemoded &>/dev/null
}

gamemode_activo() {
    gamemode_instalado && pgrep -x gamemoded &>/dev/null
}

# ── Estado ────────────────────────────────────────────────────────────────────

leer_estado() {
    if [[ -f "$ESTADO_FILE" ]]; then
        cat "$ESTADO_FILE"
    else
        echo ""
    fi
}

esta_activo() {
    [[ -f "$ESTADO_FILE" ]] && \
        python3 -c "import json,sys; d=json.load(open('$ESTADO_FILE')); sys.exit(0 if d.get('activo') else 1)" 2>/dev/null
}

guardar_estado() {
    # Recibe JSON y lo escribe en el fichero de estado
    local json="$1"
    mkdir -p "$ESTADO_DIR"
    # Usar fichero temp + mv atómico para evitar estado inconsistente
    local tmp
    tmp=$(mktemp "$ESTADO_DIR/state.XXXXXX.json")
    printf '%s\n' "$json" > "$tmp"
    mv "$tmp" "$ESTADO_FILE"
}

borrar_estado() {
    rm -f "$ESTADO_FILE"
}

# ── Procesos suspendibles ─────────────────────────────────────────────────────

encontrar_procesos_pesados() {
    # Devuelve líneas: "PID COMM RSS_MB" para procesos del usuario actual
    # que coincidan con PROCESOS_SUSPENDIBLES y tengan >100 MB RSS.
    local uid
    uid=$(id -u)
    local resultado=()

    for nombre in "${PROCESOS_SUSPENDIBLES[@]}"; do
        # Buscamos por comm exacto (sin ruta) perteneciente al usuario actual
        while IFS= read -r linea; do
            [[ -z "$linea" ]] && continue
            resultado+=("$linea")
        done < <(
            ps -u "$uid" -o pid=,comm=,rss= 2>/dev/null \
            | awk -v n="$nombre" '$2 == n && ($3/1024) > 100 {
                printf "%s %s %.0f\n", $1, $2, $3/1024
              }'
        )
    done

    # Deduplicar por PID (un proceso puede aparecer dos veces si el comm coincide
    # con dos entradas de la lista)
    printf '%s\n' "${resultado[@]}" | sort -k1,1n -u
}

suspender_proceso() {
    local pid="$1" comm="$2"
    if kill -0 "$pid" 2>/dev/null; then
        kill -STOP "$pid" 2>/dev/null && return 0 || return 1
    fi
    return 1
}

reanudar_proceso() {
    local pid="$1"
    if kill -0 "$pid" 2>/dev/null; then
        kill -CONT "$pid" 2>/dev/null && return 0 || return 1
    fi
    # El proceso ya no existe: no es un error, simplemente no hacemos nada
    return 0
}

# ── Drop caches ───────────────────────────────────────────────────────────────

drop_caches() {
    # Sincroniza y libera pagecache + dentries + inodes.
    # Requiere sudo; degrada con elegancia si no disponible.
    c "$YELLOW" "  Liberando pagecache (sync + drop_caches)..."
    c "$DIM" "  Nota: se pedirá sudo puntualmente para escribir en /proc/sys/vm/drop_caches."

    if ! sudo -n true 2>/dev/null; then
        c "$YELLOW" "  Sudo no disponible sin contraseña. Intentando con prompt..."
    fi

    sync
    if sudo sh -c 'echo 3 > /proc/sys/vm/drop_caches' 2>/dev/null; then
        c "$GREEN" "  Pagecache liberado correctamente."
        return 0
    else
        c "$YELLOW" "  No se pudo liberar el pagecache (sin privilegios o error de sudo)."
        c "$DIM" "  La operación continúa: el resto del modo gaming se aplica igualmente."
        return 0  # Degradamos con elegancia; no abortamos toda la operación
    fi
}

# ── Governor CPU ──────────────────────────────────────────────────────────────

forzar_governor_performance() {
    local gov
    gov=$(governor_actual)
    if [[ "$gov" == "performance" ]]; then
        c "$GREEN" "  CPU governor ya en 'performance' — sin cambios."
        return 0
    fi
    c "$YELLOW" "  Governor actual: $gov. Intentando cambiar a 'performance'..."
    if command -v cpupower &>/dev/null; then
        if sudo cpupower frequency-set -g performance &>/dev/null; then
            c "$GREEN" "  Governor cambiado a 'performance'."
        else
            c "$YELLOW" "  No se pudo cambiar el governor (sin sudo). Continúa en '$gov'."
        fi
    else
        c "$YELLOW" "  'cpupower' no disponible. Governor no cambiado."
    fi
}

# ── Hyprland ──────────────────────────────────────────────────────────────────

leer_opciones_hyprland() {
    # Devuelve un JSON con los valores actuales para restaurarlos en OFF
    local anim blur shadow
    anim=$(hypr_opcion "animations:enabled")
    blur=$(hypr_opcion "decoration:blur:enabled")
    shadow=$(hypr_opcion "decoration:shadow:enabled")
    printf '{"animations_enabled":%s,"blur_enabled":%s,"shadow_enabled":%s}' \
        "${anim:-1}" "${blur:-1}" "${shadow:-1}"
}

aplicar_hyprland_gaming() {
    # Desactiva animaciones, blur y sombras para reducir carga del compositor.
    # NOTA HONESTA: en pantalla completa, Hyprland ya bypasea el compositor
    # automáticamente (fullscreen unredirection), por lo que esta optimización
    # tiene impacto menor. Se implementa igualmente para ventana o modo ventana
    # sin bordes, y porque el toggle es reversible y no tiene coste.
    c "$CYAN" "  Configurando Hyprland para gaming..."
    hyprctl keyword animations:enabled false &>/dev/null \
        && c "$GREEN" "  Animaciones desactivadas." \
        || c "$YELLOW" "  No se pudo desactivar animaciones."
    hyprctl keyword decoration:blur:enabled false &>/dev/null \
        && c "$GREEN" "  Blur desactivado." \
        || c "$YELLOW" "  No se pudo desactivar blur."
    hyprctl keyword decoration:shadow:enabled false &>/dev/null \
        && c "$GREEN" "  Sombras desactivadas." \
        || c "$YELLOW" "  No se pudo desactivar sombras."
    c "$DIM" "  (Nota: en fullscreen el compositor ya se bypasea; ganancia menor)"
}

restaurar_hyprland() {
    # Recibe JSON con valores anteriores y los restaura
    local json="$1"
    local anim blur shadow
    anim=$(printf '%s' "$json" | python3 -c "import json,sys; d=json.load(sys.stdin); print('true' if d['animations_enabled'] else 'false')")
    blur=$(printf '%s' "$json" | python3 -c "import json,sys; d=json.load(sys.stdin); print('true' if d['blur_enabled'] else 'false')")
    shadow=$(printf '%s' "$json" | python3 -c "import json,sys; d=json.load(sys.stdin); print('true' if d['shadow_enabled'] else 'false')")

    hyprctl keyword animations:enabled "$anim" &>/dev/null \
        && c "$GREEN" "  Animaciones restauradas ($anim)." \
        || c "$YELLOW" "  No se pudo restaurar animaciones."
    hyprctl keyword decoration:blur:enabled "$blur" &>/dev/null \
        && c "$GREEN" "  Blur restaurado ($blur)." \
        || c "$YELLOW" "  No se pudo restaurar blur."
    hyprctl keyword decoration:shadow:enabled "$shadow" &>/dev/null \
        && c "$GREEN" "  Sombras restauradas ($shadow)." \
        || c "$YELLOW" "  No se pudo restaurar sombras."
}

# ── Gamemode ──────────────────────────────────────────────────────────────────

aviso_gamemode() {
    # Informa sobre gamemode y cómo instalarlo + configurarlo en Steam.
    if gamemode_instalado; then
        c "$GREEN" "  gamemode instalado."
        if gamemode_activo; then
            c "$GREEN" "  gamemoded en ejecución."
        else
            c "$DIM" "  gamemoded no está corriendo (se activa automáticamente cuando un juego lo llama)."
        fi
    else
        c "$YELLOW" "  gamemode NO está instalado."
        printf '\n'
        c "$BOLD" "  Para instalarlo (requiere confirmación):"
        c "$DIM" "    sudo pacman -S --needed gamemode lib32-gamemode"
        printf '\n'
        printf '  ¿Instalar gamemode ahora? [s/N] '
        read -r respuesta
        if [[ "${respuesta,,}" == "s" ]]; then
            c "$CYAN" "  Instalando gamemode..."
            if sudo pacman -S --needed gamemode lib32-gamemode; then
                c "$GREEN" "  gamemode instalado correctamente."
            else
                c "$YELLOW" "  Instalación cancelada o fallida. Continuando sin gamemode."
            fi
        else
            c "$DIM" "  Omitiendo instalación de gamemode."
        fi
        printf '\n'
        c "$BOLD" "  IMPORTANTE — Para que Bannerlord use gamemode en Steam:"
        c "$CYAN" "    1. Abre Steam → Biblioteca → Botón derecho en Bannerlord → Propiedades"
        c "$CYAN" "    2. En 'Opciones de lanzamiento' escribe exactamente:"
        c "$BOLD$CYAN" "         gamemoderun %command%"
        c "$DIM" "    (esto instruye a Proton a ejecutar el juego bajo gamemode)"
        printf '\n'
    fi
}

# ── Subcomando ON ─────────────────────────────────────────────────────────────

cmd_on() {
    local flag_yes="${1:-}"

    # P1 — Idempotencia: si ya está activo, informar y salir limpio
    if esta_activo; then
        c "$YELLOW" "modo-gaming ya está ACTIVO. Nada que hacer."
        c "$DIM" "Usa 'modo-gaming status' para ver el estado actual."
        c "$DIM" "Usa 'modo-gaming off' para desactivarlo."
        exit 0
    fi

    printf '\n'
    c "$BOLD$CYAN" "╔══════════════════════════════════════════════╗"
    c "$BOLD$CYAN" "║         MODO GAMING — ACTIVANDO              ║"
    c "$BOLD$CYAN" "╚══════════════════════════════════════════════╝"
    printf '\n'

    # Captura de RAM inicial (antes de tocar nada)
    local ram_antes_mb
    ram_antes_mb=$(ram_libre_mb)
    local ram_antes_h
    ram_antes_h=$(ram_disponible)

    c "$BLUE" "RAM disponible ANTES: $ram_antes_h"
    printf '\n'

    # ── Paso 1: Detectar procesos pesados ────────────────────────────────────
    c "$BOLD" "1/5  Detectando procesos pesados..."
    local procesos_detectados
    procesos_detectados=$(encontrar_procesos_pesados)

    local -a pids_suspendidos=()
    local -a info_suspendidos=()

    if [[ -z "$procesos_detectados" ]]; then
        c "$DIM" "  No se encontraron procesos pesados suspendibles en ejecución."
    else
        # Mostrar lista y pedir confirmación (P2 — Gate humano)
        printf '\n'
        c "$YELLOW" "  Procesos suspendibles encontrados:"
        c "$DIM" "  (se usará SIGSTOP — suspensión reversible, NO se pierden datos)"
        printf '\n'
        printf '  %-8s  %-20s  %s\n' "PID" "PROCESO" "RAM (aprox)"
        printf '  %-8s  %-20s  %s\n' "───────" "────────────────────" "──────────"
        while IFS=' ' read -r pid comm rss_mb; do
            printf '  %-8s  %-20s  %s MB\n' "$pid" "$comm" "$rss_mb"
        done <<< "$procesos_detectados"
        printf '\n'

        local confirmar="s"
        if [[ "$flag_yes" != "--yes" ]]; then
            printf '  ¿Suspender estos procesos para liberar RAM? [S/n] '
            read -r confirmar
            confirmar="${confirmar:-s}"
        fi

        if [[ "${confirmar,,}" != "n" ]]; then
            c "$CYAN" "  Suspendiendo procesos..."
            while IFS=' ' read -r pid comm rss_mb; do
                if suspender_proceso "$pid" "$comm"; then
                    c "$GREEN" "  SUSPENDIDO: $comm (PID $pid, ~${rss_mb} MB)"
                    pids_suspendidos+=("$pid")
                    info_suspendidos+=("$pid:$comm")
                else
                    c "$YELLOW" "  No se pudo suspender: $comm (PID $pid) — ya terminó o sin permisos."
                fi
            done <<< "$procesos_detectados"
        else
            c "$DIM" "  Suspensión omitida por el usuario."
        fi
    fi

    printf '\n'

    # ── Paso 2: Drop caches ───────────────────────────────────────────────────
    c "$BOLD" "2/5  Liberando pagecache..."
    drop_caches

    printf '\n'

    # ── Paso 3: Gamemode ──────────────────────────────────────────────────────
    c "$BOLD" "3/5  Gamemode..."
    aviso_gamemode

    # ── Paso 4: Governor CPU ──────────────────────────────────────────────────
    c "$BOLD" "4/5  Governor CPU..."
    forzar_governor_performance

    printf '\n'

    # ── Paso 5: Hyprland ──────────────────────────────────────────────────────
    c "$BOLD" "5/5  Compositor Hyprland..."
    local hypr_previo
    hypr_previo=$(leer_opciones_hyprland)
    aplicar_hyprland_gaming

    printf '\n'

    # ── Guardar estado ────────────────────────────────────────────────────────
    # Construimos el JSON de estado con python3 para escapar correctamente
    local pids_json
    pids_json=$(python3 -c "
import json, sys
pids = sys.argv[1:]
print(json.dumps(pids))
" "${info_suspendidos[@]+"${info_suspendidos[@]}"}")

    local hypr_json="$hypr_previo"

    local estado_json
    estado_json=$(python3 -c "
import json, sys, datetime
pids_raw = json.loads(sys.argv[1])
hypr = json.loads(sys.argv[2])
gov = sys.argv[3]
data = {
    'activo': True,
    'activado_en': datetime.datetime.now().isoformat(),
    'procesos_suspendidos': pids_raw,
    'hyprland_previo': hypr,
    'governor_previo': gov,
}
print(json.dumps(data, indent=2))
" "$pids_json" "$hypr_json" "$(governor_actual)")

    guardar_estado "$estado_json"

    # ── Resumen final ─────────────────────────────────────────────────────────
    # Pequeña pausa para que el kernel haya procesado el drop_caches
    sleep 1

    local ram_despues_mb
    ram_despues_mb=$(ram_libre_mb)
    local ram_despues_h
    ram_despues_h=$(ram_disponible)
    local ram_liberada=$(( ram_despues_mb - ram_antes_mb ))

    printf '\n'
    c "$BOLD$GREEN" "╔══════════════════════════════════════════════╗"
    c "$BOLD$GREEN" "║         MODO GAMING ACTIVO                   ║"
    c "$BOLD$GREEN" "╚══════════════════════════════════════════════╝"
    printf '\n'
    printf '  RAM antes:     %s\n' "$ram_antes_h"
    printf '  RAM ahora:     %s\n' "$ram_despues_h"
    printf '  Liberada:      ~%d MB\n' "$ram_liberada"
    printf '\n'

    if (( ${#pids_suspendidos[@]} > 0 )); then
        printf '  Procesos suspendidos: %d (se reanudan con `modo-gaming off`)\n' "${#pids_suspendidos[@]}"
    fi

    printf '\n'
    c "$BOLD$YELLOW" "  RECORDATORIO para Bannerlord en Steam:"
    c "$CYAN" "  Opciones de lanzamiento → gamemoderun %command%"
    printf '\n'
    c "$DIM" "  Para desactivar: modo-gaming off"
}

# ── Subcomando OFF ────────────────────────────────────────────────────────────

cmd_off() {
    # P1 — Idempotencia: si ya está OFF, informar y salir limpio
    if ! esta_activo 2>/dev/null; then
        c "$DIM" "modo-gaming ya está INACTIVO. Nada que restaurar."
        exit 0
    fi

    printf '\n'
    c "$BOLD$MAGENTA" "╔══════════════════════════════════════════════╗"
    c "$BOLD$MAGENTA" "║         MODO GAMING — DESACTIVANDO           ║"
    c "$BOLD$MAGENTA" "╚══════════════════════════════════════════════╝"
    printf '\n'

    local estado
    estado=$(leer_estado)

    # ── Reanudar procesos suspendidos ─────────────────────────────────────────
    local procesos_json
    procesos_json=$(printf '%s' "$estado" | python3 -c "
import json, sys
d = json.load(sys.stdin)
for p in d.get('procesos_suspendidos', []):
    print(p)
")

    if [[ -n "$procesos_json" ]]; then
        c "$CYAN" "Reanudando procesos suspendidos..."
        while IFS=: read -r pid comm; do
            [[ -z "$pid" ]] && continue
            if reanudar_proceso "$pid"; then
                c "$GREEN" "  REANUDADO: $comm (PID $pid)"
            else
                c "$YELLOW" "  No se pudo reanudar PID $pid ($comm) — puede que ya no exista."
            fi
        done <<< "$procesos_json"
    else
        c "$DIM" "  No había procesos suspendidos registrados."
    fi

    printf '\n'

    # ── Restaurar Hyprland ────────────────────────────────────────────────────
    c "$CYAN" "Restaurando Hyprland..."
    local hypr_previo
    hypr_previo=$(printf '%s' "$estado" | python3 -c "
import json, sys
d = json.load(sys.stdin)
print(json.dumps(d.get('hyprland_previo', {'animations_enabled':1,'blur_enabled':1,'shadow_enabled':1})))
")
    restaurar_hyprland "$hypr_previo"

    printf '\n'

    # ── Limpiar estado ────────────────────────────────────────────────────────
    borrar_estado
    c "$BOLD$GREEN" "Estado limpiado. Modo gaming DESACTIVADO."
    printf '\n'
    c "$DIM" "  RAM disponible ahora: $(ram_disponible)"
}

# ── Subcomando STATUS ─────────────────────────────────────────────────────────

cmd_status() {
    printf '\n'
    c "$BOLD$CYAN" "── MODO GAMING — ESTADO ──────────────────────────"
    printf '\n'

    # Estado ON/OFF
    if esta_activo 2>/dev/null; then
        c "$BOLD$GREEN" "  Estado:    ACTIVO"
        local activado_en
        activado_en=$(leer_estado | python3 -c "
import json,sys
d=json.load(sys.stdin)
print(d.get('activado_en','desconocido'))
" 2>/dev/null || echo "desconocido")
        printf '  Activado:  %s\n' "$activado_en"
        printf '\n'

        # Procesos suspendidos
        local procs
        procs=$(leer_estado | python3 -c "
import json,sys
d=json.load(sys.stdin)
ps=d.get('procesos_suspendidos',[])
if ps:
    print('  Procesos suspendidos:')
    for p in ps:
        print('    -', p)
else:
    print('  Sin procesos suspendidos.')
" 2>/dev/null)
        printf '%s\n' "$procs"
    else
        c "$DIM" "  Estado:    INACTIVO"
    fi

    printf '\n'

    # Governor CPU
    local gov
    gov=$(governor_actual)
    printf '  Governor CPU:  %s' "$gov"
    if [[ "$gov" == "performance" ]]; then
        c "$GREEN" " (OK)"
    else
        c "$YELLOW" " (no es performance)"
    fi

    # RAM
    printf '  RAM disponible: %s\n' "$(ram_disponible)"
    printf '  RAM usada:      %s\n' "$(ram_usada)"

    printf '\n'

    # Gamemode
    if gamemode_instalado; then
        if gamemode_activo; then
            c "$GREEN" "  Gamemode:   INSTALADO y ACTIVO"
        else
            c "$GREEN" "  Gamemode:   INSTALADO (inactivo — se activa al lanzar el juego)"
        fi
    else
        c "$YELLOW" "  Gamemode:   NO INSTALADO"
        printf '\n'
        c "$BOLD" "  Para instalarlo:"
        c "$DIM" "    sudo pacman -S --needed gamemode lib32-gamemode"
    fi

    printf '\n'

    # Hyprland
    local anim blur shadow
    anim=$(hypr_opcion "animations:enabled")
    blur=$(hypr_opcion "decoration:blur:enabled")
    shadow=$(hypr_opcion "decoration:shadow:enabled")
    printf '  Hyprland:\n'
    printf '    Animaciones:  %s\n' "${anim:-desconocido}"
    printf '    Blur:         %s\n' "${blur:-desconocido}"
    printf '    Sombras:      %s\n' "${shadow:-desconocido}"

    printf '\n'
    c "$BOLD$YELLOW" "  RECORDATORIO Steam — Bannerlord:"
    c "$CYAN" "  Opciones de lanzamiento → gamemoderun %command%"
    printf '\n'
    c "$DIM" "  (Botón derecho en el juego → Propiedades → Opciones de lanzamiento)"
    printf '\n'
}

# ── Ayuda ─────────────────────────────────────────────────────────────────────

cmd_ayuda() {
    cat <<'EOF'

MODO-GAMING — Toggle de optimización para sesiones de juego
============================================================

OBJETIVO:
  Liberar RAM y reducir carga del sistema antes de jugar.
  Diseñado para Mount & Blade Bannerlord 2 via Steam/Proton.

USO:
  modo-gaming on  [--yes]    Activa el modo gaming
  modo-gaming off            Restaura el estado previo
  modo-gaming status         Muestra el estado actual

OPCIONES:
  --yes    Omite confirmaciones (modo desatendido)

QUÉ HACE 'on':
  1. Detecta y suspende procesos pesados (Chromium, Opera…)
     → Usa SIGSTOP (reversible); los procesos NO pierden datos.
  2. sync + drop_caches → libera pagecache del kernel.
  3. Avisa/instala gamemode (si lo autorizas).
  4. Confirma governor CPU en 'performance'.
  5. Desactiva animaciones/blur/sombras en Hyprland.

QUÉ HACE 'off':
  Restaura todo desde el estado guardado:
  SIGCONT a procesos suspendidos, config Hyprland original.

NOTA SOBRE GAMEMODE:
  Para que Bannerlord lo use, añade en Steam (propiedades del juego):
    Opciones de lanzamiento:  gamemoderun %command%

EOF
}

# ── Dispatcher principal ──────────────────────────────────────────────────────

main() {
    local subcomando="${1:-}"

    case "$subcomando" in
        on)
            cmd_on "${2:-}"
            ;;
        off)
            cmd_off
            ;;
        status|st)
            cmd_status
            ;;
        -h|--help|help|"")
            cmd_ayuda
            ;;
        *)
            c "$RED" "Subcomando desconocido: '$subcomando'"
            c "$DIM" "Uso: modo-gaming on|off|status"
            exit 1
            ;;
    esac
}

main "$@"
