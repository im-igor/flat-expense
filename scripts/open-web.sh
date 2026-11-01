#!/usr/bin/env bash
# Run on your computer, not on the VM. SSH authenticates access to the private UI.
set -euo pipefail
ssh_target="${1:?Укажите SSH-подключение: bash scripts/open-web.sh user@VM_ADDRESS [local_port]}"
local_port="${2:-8318}"
if [[ ! "$local_port" =~ ^[0-9]+$ ]] || (( local_port < 1024 || local_port > 65535 )); then
  echo 'Локальный порт должен быть числом от 1024 до 65535.' >&2
  exit 1
fi
if [[ "$ssh_target" == -* ]]; then
  echo 'Укажите SSH-адрес вида user@VM_ADDRESS.' >&2
  exit 1
fi
printf 'После подключения откройте http://127.0.0.1:%s/\nОставьте терминал открытым. Ctrl+C закроет подключение.\n' "$local_port"
exec ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
  -L "127.0.0.1:$local_port:127.0.0.1:8000" "$ssh_target"
