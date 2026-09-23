#!/usr/bin/env bash
# Dry-run one tag of playbook-index.yaml against the ops group (T-1864).
# READ-ONLY BY DEFAULT: runs ansible-playbook --check --diff.
# Mutating requires the literal flag --apply-for-real typed by the caller.
#
# Usage: scripts/ansible-dry-run.sh <tag> [--apply-for-real] [-- extra ansible args]
set -euo pipefail

cd "$(dirname "$0")/.."

TAG="${1:-}"
if [[ -z "$TAG" || "$TAG" == -* ]]; then
  echo "usage: $0 <tag> [--apply-for-real] [-- extra ansible-playbook args]" >&2
  exit 2
fi
shift

MODE=(--check --diff)
if [[ "${1:-}" == "--apply-for-real" ]]; then
  MODE=(--diff)
  shift
  echo ">>> APPLY MODE: tag '$TAG' WILL change hosts in group ops" >&2
fi
[[ "${1:-}" == "--" ]] && shift

INVENTORY=protected/inventories/inventory-main.yaml
if [[ ! -f "$INVENTORY" ]]; then
  echo "missing $INVENTORY (protected/ submodule not checked out)" >&2
  exit 2
fi

# OBJC flag: same node_exporter fork-safety fix as the Makefile 'ops' target.
OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES exec ansible-playbook \
  -i "$INVENTORY" playbook-index.yaml -l ops --tags "$TAG" "${MODE[@]}" "$@"
