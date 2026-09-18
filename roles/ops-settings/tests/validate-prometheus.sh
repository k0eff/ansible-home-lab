#!/usr/bin/env bash
# T-1620 — validate the koeff-ai-stack prometheus config and its
# self-blindness rules with promtool, in the same image the host runs.
#
# AGENTS.md: "A guard nobody can run is a guard nobody tested." The rule that
# catches an empty target list is worth exactly as much as the check that
# proves the rule still parses and still fires, so that check lives in the
# repo next to the thing it guards and takes no arguments to run.
#
# Both templates are deliberately Jinja-free, so what promtool reads here is
# byte-for-byte what ansible writes to the host — no render step to drift.
#
# Read-only. Touches no host, starts no stack, and needs no vm700.
#
#   roles/ops-settings/tests/validate-prometheus.sh
set -euo pipefail

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROLE_DIR="$(cd "${TESTS_DIR}/.." && pwd)"
IMAGE="${PROM_IMAGE:-prom/prometheus:latest}"

echo "promtool image: ${IMAGE}"
echo "role dir:       ${ROLE_DIR}"

# --user root: the staging copy below writes into /etc/prometheus inside the
# container so the config's ABSOLUTE rule_files path resolves at exactly the
# path the deployment uses. Validating a rewritten path would validate a file
# that never ships.
docker run --rm --user root \
  --entrypoint /bin/sh \
  -v "${ROLE_DIR}:/role:ro" \
  "${IMAGE}" -euc '
    mkdir -p /etc/prometheus/rules
    cp /role/templates/headroom/prometheus.yml.j2        /etc/prometheus/prometheus.yml
    cp /role/templates/headroom/prometheus-rules.yml.j2  /etc/prometheus/rules/self-blindness.yml

    echo "--- promtool check config"
    promtool check config /etc/prometheus/prometheus.yml

    echo "--- promtool check rules"
    promtool check rules /etc/prometheus/rules/self-blindness.yml

    echo "--- promtool test rules"
    cd /role/tests
    promtool test rules prometheus-self-blindness.test.yml
  '

echo "OK — config, rules and unit tests all pass"
