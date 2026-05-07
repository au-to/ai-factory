#!/usr/bin/env bash

set -euo pipefail

DEFAULT_PORTS=(3000 5173 8000 8080 8900 9000)

usage() {
  cat <<'EOF'
用法:
  ./scripts/dev-clean.sh
  ./scripts/dev-clean.sh -p 3000,5173,8900
  ./scripts/dev-clean.sh --ports 3000,5173

说明:
  - 默认扫描常见开发端口: 3000,5173,8000,8080,8900,9000
  - 交互式选择要关闭的端口，或输入 all 一键关闭
  - 先发送 TERM，若进程仍存在再发送 KILL
EOF
}

ports=("${DEFAULT_PORTS[@]}")

while [[ $# -gt 0 ]]; do
  case "$1" in
    -p|--ports)
      shift
      [[ $# -gt 0 ]] || { echo "错误: --ports 需要参数"; exit 1; }
      IFS=',' read -r -a ports <<< "$1"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "错误: 未知参数 $1"
      usage
      exit 1
      ;;
  esac
  shift
done

active_ports=()
active_entries=()

echo "扫描端口: ${ports[*]}"
echo

for port in "${ports[@]}"; do
  port="${port//[[:space:]]/}"
  [[ -n "$port" ]] || continue
  pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    active_ports+=("$port")
    while read -r pid; do
      [[ -n "$pid" ]] || continue
      active_entries+=("${port}:${pid}")
    done <<< "$pids"
  fi
done

if [[ ${#active_ports[@]} -eq 0 ]]; then
  echo "没有发现占用目标端口的监听进程。"
  exit 0
fi

echo "发现以下监听进程:"
for port in "${active_ports[@]}"; do
  for entry in "${active_entries[@]}"; do
    entry_port="${entry%%:*}"
    pid="${entry##*:}"
    [[ "$entry_port" == "$port" ]] || continue
    command_name="$(ps -p "$pid" -o comm= 2>/dev/null || true)"
    command_name="${command_name#"${command_name%%[![:space:]]*}"}"
    command_name="${command_name%"${command_name##*[![:space:]]}"}"
    echo "  端口 $port -> PID $pid (${command_name:-unknown})"
  done
done

echo
read -r -p "输入要关闭的端口（逗号分隔）、all 或 q 退出: " answer

if [[ "$answer" == "q" || "$answer" == "Q" ]]; then
  echo "已取消。"
  exit 0
fi

selected_ports=()
if [[ "$answer" == "all" || "$answer" == "ALL" ]]; then
  selected_ports=("${active_ports[@]}")
else
  IFS=',' read -r -a selected_ports <<< "$answer"
fi

normalize_port() {
  local input="$1"
  echo "${input//[[:space:]]/}"
}

close_pid() {
  local pid="$1"
  if ! kill -0 "$pid" 2>/dev/null; then
    return 0
  fi

  kill "$pid" 2>/dev/null || true
  sleep 0.5

  if kill -0 "$pid" 2>/dev/null; then
    kill -9 "$pid" 2>/dev/null || true
  fi
}

closed_any=0
for raw_port in "${selected_ports[@]}"; do
  port="$(normalize_port "$raw_port")"
  [[ -n "$port" ]] || continue

  has_listener=0
  for active_port in "${active_ports[@]}"; do
    if [[ "$active_port" == "$port" ]]; then
      has_listener=1
      break
    fi
  done

  if [[ "$has_listener" -eq 0 ]]; then
    echo "跳过端口 $port：未发现监听进程。"
    continue
  fi

  for entry in "${active_entries[@]}"; do
    entry_port="${entry%%:*}"
    pid="${entry##*:}"
    [[ "$entry_port" == "$port" ]] || continue
    command_name="$(ps -p "$pid" -o comm= 2>/dev/null || true)"
    command_name="${command_name#"${command_name%%[![:space:]]*}"}"
    command_name="${command_name%"${command_name##*[![:space:]]}"}"
    close_pid "$pid"
    if kill -0 "$pid" 2>/dev/null; then
      echo "关闭失败: 端口 $port PID $pid (${command_name:-unknown})"
    else
      echo "已关闭: 端口 $port PID $pid (${command_name:-unknown})"
      closed_any=1
    fi
  done
done

if [[ "$closed_any" -eq 0 ]]; then
  echo "没有关闭任何进程。"
fi
