#!/usr/bin/env bash
# Manage the DGX Spark Qwen 3.8 27B + ComfyUI/FLUX coexistence experiment.
#
# Default is read-only. --apply changes only user-level systemd drop-ins; it
# never starts/stops services. --revert restores exactly the backed-up state.
# Flash remains deliberately untouched: stop it explicitly before starting 27B.
set -euo pipefail

BASE_VLLM_UNIT="vllm-122b.service"
VLLM_UNIT="qwen38-27b-hybrid.service"
COMFY_UNIT="comfyui.service"
UNIT_DIR="${HOME}/.config/systemd/user"
COMFY_DROPIN_DIR="${UNIT_DIR}/comfyui.service.d"
COMFY_DROPIN="${COMFY_DROPIN_DIR}/90-hybrid-qwen-flux.conf"
HYBRID_UNIT="${UNIT_DIR}/${VLLM_UNIT}"
HYBRID_SECRET_DIR="${HOME}/.config/qwen38-27b-hybrid"
HYBRID_ENV="${HYBRID_SECRET_DIR}/env"
HYBRID_RUNNER="${HYBRID_SECRET_DIR}/serve.sh"
BACKUP_ROOT="${HOME}/kathawin-work/backups/hybrid-qwen-flux"

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
usage() {
  cat <<'EOF'
Usage: hybrid-qwen-flux.sh [--status|--apply|--revert]

  --status  Read-only prerequisite and service report (default).
  --apply   Install coexistence drop-ins, but do not start/stop anything.
  --revert  Remove only this script's drop-ins and restore its latest backup.

After --apply, Flash must be stopped explicitly before starting Qwen:
  sudo systemctl stop qwen38-flash.service qwen38-keepalive.service
  systemctl --user start qwen38-27b-hybrid.service
  systemctl --user start comfyui.service

Hybrid safety envelope:
  Qwen: GPU_UTIL=0.45, MAX_SEQS=1, MAX_LEN=262144
  Comfy starts only after 40 GiB is available.

This creates capacity for one 262K Qwen sequence plus a FLUX generation, but
has not been load-tested. Do not run concurrent subagents on Qwen in this mode.
EOF
}

need_ssh_host() {
  [ -x "${HOME}/kathawin-work/bin/gpu_residency.sh" ] || fail "Run on the DGX Spark: gpu_residency.sh is missing."
  systemctl --user cat "$BASE_VLLM_UNIT" >/dev/null || fail "$BASE_VLLM_UNIT is not installed."
  systemctl --user cat "$COMFY_UNIT" >/dev/null || fail "$COMFY_UNIT is not installed."
}

status() {
  need_ssh_host
  echo '== Services =='
  printf 'Flash: '; systemctl is-active qwen38-flash.service 2>/dev/null || true
  printf 'Qwen:  '; systemctl --user is-active "$VLLM_UNIT" 2>/dev/null || true
  printf 'Comfy: '; systemctl --user is-active "$COMFY_UNIT" 2>/dev/null || true
  echo '== Memory =='; free -h | head -3
  echo '== Hybrid files =='
  for f in "$HYBRID_UNIT" "$COMFY_DROPIN" "$HYBRID_ENV" "$HYBRID_RUNNER"; do
    if [ -f "$f" ]; then echo "present: $f"; else echo "absent:  $f"; fi
  done
  if [ -f "$HYBRID_ENV" ] && grep -q '^VLLM_API_KEY=.' "$HYBRID_ENV"; then
    echo 'Qwen API authentication: configured'
  else
    echo 'Qwen API authentication: MISSING'
  fi
  echo '== Required image assets (must be validated separately) =='
  for f in \
    "${HOME}/models/flux2-dev-nvfp4-hf/flux2-dev-nvfp4-mixed.safetensors" \
    "${HOME}/models/flux2-dev-comfy-org/split_files/text_encoders/mistral_3_small_flux2_fp4_mixed.safetensors" \
    "${HOME}/models/flux2-dev-comfy-org/split_files/vae/flux2-vae.safetensors"; do
    if [ -f "$f" ]; then printf 'present: '; du -h "$f" | cut -f1; else echo "MISSING: $f"; fi
  done
  echo '== Local APIs =='
  echo 'Qwen:  http://127.0.0.1:8000/v1/chat/completions (when vllm-122b is active)'
  echo 'Comfy: http://127.0.0.1:8188/prompt (when comfyui is active)'
}

apply() {
  need_ssh_host
  local stamp backup
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  backup="${BACKUP_ROOT}/${stamp}"
  mkdir -p "$backup" "$COMFY_DROPIN_DIR" "$HYBRID_SECRET_DIR"
  chmod 0700 "$HYBRID_SECRET_DIR"
  [ -f "$HYBRID_UNIT" ] && cp -a "$HYBRID_UNIT" "$backup/hybrid-unit-existing.service" || true
  [ -f "$COMFY_DROPIN" ] && cp -a "$COMFY_DROPIN" "$backup/comfy-existing.conf" || true
  [ -f "$HYBRID_ENV" ] && cp -a "$HYBRID_ENV" "$backup/hybrid-env-existing" || true
  [ -f "$HYBRID_RUNNER" ] && cp -a "$HYBRID_RUNNER" "$backup/hybrid-runner-existing.sh" || true
  systemctl --user is-enabled --quiet "$BASE_VLLM_UNIT" && printf 'enabled\n' > "$backup/base-vllm-was-enabled" || true
  printf '%s\n' "$backup" > "${BACKUP_ROOT}/LATEST"

  if [ ! -s "$HYBRID_ENV" ]; then
    umask 077
    printf 'VLLM_API_KEY=%s\n' "$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')" > "$HYBRID_ENV"
  fi
  chmod 0600 "$HYBRID_ENV"
  cat > "$HYBRID_RUNNER" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
: "${VLLM_API_KEY:?hybrid Qwen API key is missing}"
exec /bin/bash /home/karthik/vllm-env/serve-qwen38-27b.sh --api-key "$VLLM_API_KEY"
EOF
  chmod 0700 "$HYBRID_RUNNER"

  cat > "$HYBRID_UNIT" <<'EOF'
[Unit]
Description=Qwen 3.8 27B NVFP4 coexistence server on DGX Spark
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStartPre=/bin/bash /home/karthik/kathawin-work/bin/gpu_residency.sh 55
Environment=GPU_UTIL=0.45
Environment=MAX_SEQS=1
Environment=MAX_LEN=262144
EnvironmentFile=/home/karthik/.config/qwen38-27b-hybrid/env
ExecStart=/home/karthik/.config/qwen38-27b-hybrid/serve.sh
Restart=on-failure
RestartSec=15
TimeoutStartSec=1200
KillMode=mixed

[Install]
WantedBy=default.target
EOF

  cat > "$COMFY_DROPIN" <<'EOF'
[Unit]
# The original Qwen service's Conflicts= cannot be reset by a drop-in. This
# service has no conflict with qwen38-27b-hybrid.service.

[Service]
# Do not evict Qwen. Wait only for coexistence headroom.
ExecStartPre=
ExecStartPre=/bin/bash /home/karthik/kathawin-work/bin/gpu_residency.sh 40
EOF

  systemctl --user daemon-reload
  # Prevent the old mutually-exclusive unit from being auto-started on reboot.
  systemctl --user disable "$BASE_VLLM_UNIT" >/dev/null || true
  systemctl --user enable "$VLLM_UNIT" >/dev/null
  echo "Installed hybrid unit; backup: $backup"
  echo 'No running service was changed.'
  status
}

revert() {
  need_ssh_host
  [ -f "${BACKUP_ROOT}/LATEST" ] || fail 'No recorded hybrid backup exists.'
  local backup
  backup="$(<"${BACKUP_ROOT}/LATEST")"
  rm -f "$HYBRID_UNIT" "$COMFY_DROPIN" "$HYBRID_ENV" "$HYBRID_RUNNER"
  [ -f "$backup/hybrid-unit-existing.service" ] && cp -a "$backup/hybrid-unit-existing.service" "$HYBRID_UNIT"
  [ -f "$backup/comfy-existing.conf" ] && cp -a "$backup/comfy-existing.conf" "$COMFY_DROPIN"
  [ -f "$backup/hybrid-env-existing" ] && cp -a "$backup/hybrid-env-existing" "$HYBRID_ENV"
  [ -f "$backup/hybrid-runner-existing.sh" ] && cp -a "$backup/hybrid-runner-existing.sh" "$HYBRID_RUNNER"
  systemctl --user daemon-reload
  systemctl --user disable "$VLLM_UNIT" >/dev/null || true
  [ -f "$backup/base-vllm-was-enabled" ] && systemctl --user enable "$BASE_VLLM_UNIT" >/dev/null || true
  echo "Reverted hybrid unit using backup: $backup"
  echo 'No running service was changed.'
  status
}

case "${1:---status}" in
  --status) status ;;
  --apply) apply ;;
  --revert) revert ;;
  -h|--help) usage ;;
  *) usage >&2; exit 2 ;;
esac
