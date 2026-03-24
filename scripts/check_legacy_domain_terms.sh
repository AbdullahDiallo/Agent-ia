#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${1:-.}"
EXCLUSIONS_FILE="scripts/legacy_scan_exclusions.txt"
PATTERN='(\bimmo\b|\bimmobili\w*\b|\bleads?\b|legacy_)'

RG_ARGS=(
  -n
  -i
  "${PATTERN}"
  "${ROOT_DIR}"
  --glob
  '!venv/**'
  --glob
  '!.venv312/**'
  --glob
  '!front/dashboard/package-lock.json'
  --glob
  '!logs/**'
  --glob
  '!scripts/check_legacy_domain_terms.sh'
  --glob
  '!scripts/legacy_scan_exclusions.txt'
)

if [[ -f "${EXCLUSIONS_FILE}" ]]; then
  while IFS= read -r line; do
    trimmed="$(echo "${line}" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    if [[ -z "${trimmed}" || "${trimmed}" == \#* ]]; then
      continue
    fi
    RG_ARGS+=(--glob "!${trimmed}")
  done < "${EXCLUSIONS_FILE}"
fi

if rg "${RG_ARGS[@]}"; then
  echo "Legacy domain terms detected outside allowed exclusions."
  exit 1
fi

echo "Legacy domain scan passed (no forbidden terms outside exclusions)."
