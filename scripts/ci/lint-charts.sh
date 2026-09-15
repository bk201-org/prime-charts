#!/usr/bin/env bash

set -euo pipefail

chart_csv=$(printf '%s\n' "${CHARTS}" | paste -sd, -)

if [[ "${STABLE_BRANCH}" == true && -n "${BASE_SHA}" ]]; then
  git update-ref refs/remotes/origin/ct-target "${BASE_SHA}"
  ct lint \
    --config tests/ct.yaml \
    --remote origin \
    --target-branch ct-target \
    --since "${GITHUB_SHA}"

  if [[ "${TEST_ALL}" == true ]]; then
    ct lint \
      --config tests/ct.yaml \
      --charts "${chart_csv}" \
      --check-version-increment=false
  fi
else
  ct lint \
    --config tests/ct.yaml \
    --charts "${chart_csv}" \
    --check-version-increment=false
fi
