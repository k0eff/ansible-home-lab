#!/bin/sh
# Landing gate for the forge (#1564): wf-lander runs .forge-verify, which calls this, on the merge
# of a ticket branch into main. Exit 0 = may land. No host is contacted: the syntax check uses an
# inline localhost inventory and never connects.
#   1. yamllint over roles/ and the playbooks (.yamllint)
#   2. ansible-playbook --syntax-check on every playbook (a superset of the changed roles' plays)
#   3. py_compile + unittest (test_*.py) for the python shipped under roles/*/files
set -eu
export PYTHONDONTWRITEBYTECODE=1
cd "$(dirname "$0")/.."

echo "== yamllint"
if command -v uvx >/dev/null 2>&1; then
  uvx --quiet --from yamllint==1.35.1 yamllint -s roles playbook-*.yaml
elif python3 -m yamllint --version >/dev/null 2>&1; then
  python3 -m yamllint -s roles playbook-*.yaml
else
  echo "forge-verify: yamllint unavailable (need uvx or python3 -m yamllint)" >&2
  exit 2
fi

echo "== ansible-playbook --syntax-check"
# ansible refuses non-blocking stdio, which a piped runner can hand it: give it plain files.
log="$(mktemp)"
trap 'rm -f "$log"' EXIT
for playbook in playbook-*.yaml; do
  if ! ANSIBLE_LOCALHOST_WARNING=False ansible-playbook -i localhost, --syntax-check "$playbook" </dev/null >"$log" 2>&1; then
    cat "$log" >&2
    exit 1
  fi
  echo "ok $playbook"
done

echo "== role python"
tests=0
for dir in roles/*/files; do
  ls "$dir"/*.py >/dev/null 2>&1 || continue
  PYTHONPYCACHEPREFIX="${TMPDIR:-/tmp}/forge-verify-pycache" python3 -m py_compile "$dir"/*.py
  echo "compiled $dir"
  if ls "$dir"/test_*.py >/dev/null 2>&1; then
    python3 -m unittest discover -s "$dir" -p 'test_*.py'
    tests=$((tests + 1))
  fi
done
echo "forge-verify: green (unit-test dirs: $tests)"
