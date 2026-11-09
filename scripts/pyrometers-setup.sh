#!/bin/bash
# =============================================================================
# pyrometers-setup.sh
# Device registry management for USB pyrometers (Silicon Labs 10c4:834b)
#
# Usage:
#   sudo ./scripts/pyrometers-setup.sh --discover
#   sudo ./scripts/pyrometers-setup.sh --add <serial> <microeps|optris> [display_name]
#   sudo ./scripts/pyrometers-setup.sh --list
#   sudo ./scripts/pyrometers-setup.sh --remove <id>
# =============================================================================

set -e

VENDOR="10c4"
PRODUCT="834b"
UDEV_RULE="/etc/udev/rules.d/99-pyrometers.rules"
LEGACY_MODPROBE_CONF="/etc/modprobe.d/cp210x-834b.conf"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REGISTRY="${PYROMETERS_REGISTRY_HOST:-$PROJECT_ROOT/runtime/pyrometers-devices.json}"
REGISTRY_TEMPLATE="${PYROMETERS_REGISTRY_TEMPLATE:-$PROJECT_ROOT/config/pyrometers-devices.example.json}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info()  { echo -e "${BLUE}[INFO]${NC}  $1"; }
log_ok()    { echo -e "${GREEN}[OK]${NC}    $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_item()  { echo -e "    ${CYAN}→${NC} $1"; }

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

require_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "Требуются права root. Запустите: sudo bash $0 $*"
        exit 1
    fi
}

require_python() {
    if ! command -v python3 &>/dev/null; then
        log_error "python3 не найден"
        exit 1
    fi
}

ensure_registry() {
    local registry_dir
    registry_dir="$(dirname "$REGISTRY")"
    mkdir -p "$registry_dir"
    if [[ -f "$REGISTRY" ]]; then
        return
    fi
    if [[ -f "$REGISTRY_TEMPLATE" ]]; then
        cp "$REGISTRY_TEMPLATE" "$REGISTRY"
    else
        printf '[]\n' > "$REGISTRY"
    fi
    log_ok "Local pyrometer registry initialized: $REGISTRY"
}

ensure_driver() {
    if ! lsmod | grep -q "^cp210x"; then
        log_info "Загрузка модуля cp210x..."
        modprobe cp210x
        sleep 0.5
    fi
    local new_id="/sys/bus/usb-serial/drivers/cp210x/new_id"
    if [[ -f "$new_id" ]]; then
        echo "${VENDOR} ${PRODUCT}" > "$new_id" 2>/dev/null || true
    fi
}

remove_legacy_modprobe_conf() {
    if [[ -f "$LEGACY_MODPROBE_CONF" ]]; then
        rm -f "$LEGACY_MODPROBE_CONF"
        log_ok "Removed legacy duplicate modprobe hook: $LEGACY_MODPROBE_CONF"
    fi
}

get_connected_devices() {
    ensure_driver
    for port in /dev/ttyUSB*; do
        [[ -e "$port" ]] || continue
        local serial vendor_id product_id
        serial=$(udevadm info "$port" 2>/dev/null | grep "ID_SERIAL_SHORT=" | cut -d= -f2)
        vendor_id=$(udevadm info "$port" 2>/dev/null | grep "ID_VENDOR_ID=" | cut -d= -f2)
        product_id=$(udevadm info "$port" 2>/dev/null | grep "ID_MODEL_ID=" | cut -d= -f2)
        if [[ "$vendor_id" == "$VENDOR" && "$product_id" == "$PRODUCT" ]]; then
            echo "$port $serial"
        fi
    done
}

registry_serials() {
    python3 -c "
import json, sys
try:
    data = json.load(open('$REGISTRY'))
    for e in data: print(e['serial'])
except: pass
" 2>/dev/null
}

registry_ids() {
    python3 -c "
import json, sys
try:
    data = json.load(open('$REGISTRY'))
    for e in data: print(e['id'])
except: pass
" 2>/dev/null
}

next_id_for_type() {
    local type="$1"
    local prefix
    case "$type" in
        microeps) prefix="microeps" ;;
        optris)   prefix="optris" ;;
        *)        prefix="$type" ;;
    esac
    local existing
    existing=$(registry_ids | grep "^${prefix}" | grep -oP '\d+$' | sort -n | tail -1)
    echo "${prefix}$((${existing:-0} + 1))"
}

next_symlink_for_type() {
    local type="$1"
    local base
    case "$type" in
        microeps) base="ttyMICROEPS" ;;
        optris)   base="ttyOPTRIS" ;;
        *)        base="ttyPYRO" ;;
    esac
    local existing
    existing=$(python3 -c "
import json
try:
    data = json.load(open('$REGISTRY'))
    nums = []
    for e in data:
        s = e.get('symlink','')
        if s.startswith('$base'):
            suffix = s[len('$base'):]
            if suffix.isdigit(): nums.append(int(suffix))
    print(max(nums) if nums else 0)
except: print(0)
" 2>/dev/null)
    echo "${base}$((${existing:-0} + 1))"
}

registry_add() {
    local serial="$1" type="$2" device_id="$3" symlink="$4" display_name="$5"
    PYRO_REGISTRY="$REGISTRY" \
    PYRO_SERIAL="$serial" \
    PYRO_TYPE="$type" \
    PYRO_DEVICE_ID="$device_id" \
    PYRO_SYMLINK="$symlink" \
    PYRO_DISPLAY_NAME="$display_name" \
    python3 - <<'PY'
import json
import os

path = os.environ["PYRO_REGISTRY"]
device_type = os.environ["PYRO_TYPE"]
data = []
if os.path.exists(path):
    with open(path) as f:
        data = json.load(f)
data.append({
    "serial": os.environ["PYRO_SERIAL"],
    "type": device_type,
    "id": os.environ["PYRO_DEVICE_ID"],
    "symlink": os.environ["PYRO_SYMLINK"],
    "display_name": os.environ["PYRO_DISPLAY_NAME"],
    "mode": "burst",
    "stream_frame_format": "marked_aaaa",
    "burst_command_set": "classic_ct",
    "checksum_enabled": True,
    "burst_channels": ["target", "head", "box", "target"],
})
with open(path, 'w') as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
    f.write('\n')
print('ok')
PY
}

registry_remove() {
    local device_id="$1"
    python3 -c "
import json, os
path = '$REGISTRY'
if not os.path.exists(path):
    print('not found'); exit(1)
with open(path) as f:
    data = json.load(f)
before = len(data)
data = [e for e in data if e.get('id') != '$device_id']
if len(data) == before:
    print('id not found'); exit(1)
with open(path, 'w') as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
    f.write('\n')
print('ok')
"
}

regenerate_udev() {
    log_info "Обновление udev-правил: $UDEV_RULE"
    {
        echo "# ============================================================="
        echo "# Pyrometers: Silicon Labs ${VENDOR}:${PRODUCT}"
        echo "# Generated by pyrometers-setup.sh — do not edit manually"
        echo "# ============================================================="
        echo ""
        echo "# Load driver and register PID on USB connect"
        echo "SUBSYSTEM==\"usb\", ATTRS{idVendor}==\"${VENDOR}\", ATTRS{idProduct}==\"${PRODUCT}\", \\"
        echo "    RUN+=\"/sbin/modprobe cp210x\", \\"
        echo "    RUN+=\"/bin/sh -c 'echo ${VENDOR} ${PRODUCT} > /sys/bus/usb-serial/drivers/cp210x/new_id 2>/dev/null || true'\""
        echo ""
        echo "# Persistent symlinks by serial number"
        python3 -c "
import json
try:
    data = json.load(open('$REGISTRY'))
    for e in data:
        print(f\"SUBSYSTEM==\\\"tty\\\", ATTRS{{serial}}==\\\"{e['serial']}\\\", SYMLINK+=\\\"{e['symlink']}\\\", GROUP=\\\"dialout\\\", MODE=\\\"0660\\\"\")
except: pass
"
    } > "$UDEV_RULE"

    udevadm control --reload-rules
    udevadm trigger --action=change --subsystem-match=tty
    sleep 1
    log_ok "udev-правила обновлены"
}

# -----------------------------------------------------------------------------
# Commands
# -----------------------------------------------------------------------------

cmd_discover() {
    echo ""
    echo "================================================="
    echo " Обнаружение USB пирометров (${VENDOR}:${PRODUCT})"
    echo "================================================="
    echo ""

    local count=0
    local known_serials
    known_serials=$(registry_serials)

    while IFS=" " read -r port serial; do
        [[ -z "$port" ]] && continue
        count=$((count + 1))
        local status="неизвестно"
        if echo "$known_serials" | grep -qx "$serial"; then
            status="${GREEN}зарегистрировано${NC}"
        else
            status="${YELLOW}НЕ зарегистрировано${NC}"
        fi
        echo -e "  $port  S/N: ${CYAN}${serial}${NC}  [$status]"
    done < <(get_connected_devices)

    if [[ $count -eq 0 ]]; then
        log_warn "Устройства ${VENDOR}:${PRODUCT} не найдены. Проверьте подключение."
    else
        echo ""
        log_info "Найдено устройств: $count"
    fi
    echo ""
}

cmd_list() {
    echo ""
    echo "================================================="
    echo " Реестр пирометров"
    echo "================================================="
    echo ""

    require_python
    ensure_registry
    python3 -c "
import json, os
path = '$REGISTRY'
if not os.path.exists(path):
    print('  (реестр пуст или не найден)')
    exit()
data = json.load(open(path))
if not data:
    print('  (реестр пуст)')
    exit()
for e in data:
    symlink = '/dev/' + e.get('symlink','?')
    present = ' [подключено]' if os.path.exists(symlink) else ' [отключено]'
    print(f\"  {e['id']:12s}  S/N: {e['serial']:15s}  {symlink:20s}  {e['display_name']}{present}\")
" 2>/dev/null || echo "  (ошибка чтения реестра)"
    echo ""
}

cmd_add() {
    local serial="$1"
    local type="$2"
    local display_name="$3"

    if [[ -z "$serial" || -z "$type" ]]; then
        log_error "Использование: --add <serial> <microeps|optris> [display_name]"
        exit 1
    fi
    if [[ "$type" != "microeps" && "$type" != "optris" ]]; then
        log_error "Тип должен быть: microeps или optris"
        exit 1
    fi
    ensure_registry

    # Check already registered
    if registry_serials | grep -qx "$serial"; then
        log_warn "Устройство с S/N $serial уже зарегистрировано"
        cmd_list
        exit 0
    fi

    local device_id symlink
    device_id=$(next_id_for_type "$type")
    symlink=$(next_symlink_for_type "$type")

    if [[ -z "$display_name" ]]; then
        case "$type" in
            microeps) display_name="Micro-Epsilon thermoMETER CT ${device_id//microeps/}" ;;
            optris)   display_name="Optris CT ${device_id//optris/}" ;;
        esac
    fi

    echo ""
    echo "================================================="
    echo " Регистрация устройства"
    echo "================================================="
    log_item "S/N:          $serial"
    log_item "Тип:          $type"
    log_item "ID:           $device_id"
    log_item "Симлинк:      /dev/$symlink"
    log_item "Display name: $display_name"
    echo ""

    registry_add "$serial" "$type" "$device_id" "$symlink" "$display_name"
    log_ok "Добавлено в реестр: $REGISTRY"

    regenerate_udev

    # Add user to dialout if needed
    local real_user="${SUDO_USER:-$USER}"
    if ! groups "$real_user" | grep -q "dialout"; then
        usermod -aG dialout "$real_user"
        log_ok "$real_user добавлен в группу dialout"
    fi

    echo ""
    log_ok "Готово! /dev/$symlink → устройство $serial"
    echo ""
    log_info "Перезапусти контейнер чтобы применить:"
    echo "    docker restart pyrometer-collector"
    echo ""
}

cmd_remove() {
    local device_id="$1"
    if [[ -z "$device_id" ]]; then
        log_error "Использование: --remove <id>"
        exit 1
    fi

    local result
    ensure_registry
    result=$(registry_remove "$device_id")
    if [[ "$result" == "id not found" ]]; then
        log_error "ID '$device_id' не найден в реестре"
        exit 1
    fi

    log_ok "Устройство '$device_id' удалено из реестра"
    regenerate_udev
    echo ""
    log_info "Перезапусти контейнер чтобы применить:"
    echo "    docker restart pyrometer-collector"
    echo ""
}

# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------

case "${1:-}" in
    --discover)
        require_root "$@"
        cmd_discover
        ;;
    --add)
        require_root "$@"
        cmd_add "$2" "$3" "$4"
        ;;
    --list)
        cmd_list
        ;;
    --remove)
        require_root "$@"
        cmd_remove "$2"
        ;;
    --apply)
        require_root "$@"
        remove_legacy_modprobe_conf
        ensure_registry
        ensure_driver
        regenerate_udev
        log_ok "Готово"
        ;;
    *)
        echo ""
        echo "Использование:"
        echo "  sudo bash $0 --discover                             # показать подключённые устройства"
        echo "  sudo bash $0 --add <serial> <microeps|optris>       # зарегистрировать устройство"
        echo "  sudo bash $0 --add <serial> <type> \"Display Name\"   # с кастомным именем"
        echo "       bash $0 --list                                 # показать реестр"
        echo "  sudo bash $0 --remove <id>                          # удалить из реестра"
        echo "  sudo bash $0 --apply                                # применить реестр → udev (после ручной правки)"
        echo ""
        ;;
esac
