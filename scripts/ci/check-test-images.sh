#!/usr/bin/env bash

set -euo pipefail

unresolved=false
while IFS= read -r chart; do
  [[ -n "${chart}" ]] || continue
  if grep -RIn 'REPLACE_ME_' "${chart}/ci" 2>/dev/null; then
    unresolved=true
  fi
done <<< "${CHARTS}"

if [[ "${unresolved}" == true ]]; then
  echo '::error::Replace the REPLACE_ME_* image values before enabling kind installation tests.'
  exit 1
fi
