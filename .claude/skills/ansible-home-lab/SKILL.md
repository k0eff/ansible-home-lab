---
name: ansible-home-lab
description: Use when changing or inspecting host and Docker state on the home-lab VMs (vm700 / ops group, diskstorage, VPN, exo) — any role, compose stack, Caddy route, mount or systemd unit that ansible-home-lab owns. Also use before running any ansible-playbook from this repo.
---

# ansible-home-lab

## What it owns

One Tool Per Layer (root `AGENTS.md`): **ansible owns host and Docker state on VMs**.
Kubernetes is helmfile (`helmfile-home-lab`), VMs/Proxmox are terraform (`tf-home-lab`).
`ssh` and `docker` on a host are for reading; any change goes into a role here and is
applied with `ansible-playbook`. A hand fix in an emergency must be written back here the
same session.

## Layout

- `playbook-index.yaml` — main playbook; plays for `everyone`, `ops` (vm700), `diskstorage`.
- `playbook-vpn.yaml`, `playbook-exo.yaml`, `playbook-mac.yaml` — separate targets.
- `roles/` — `ops-settings` holds most vm700 stacks (tags such as `forgejo_stack`,
  `docker_daemon`); `bg-laws` (`bg_laws`), `nasdedup` (`nasdedup`).
- `protected/` — private submodule: inventories, group_vars, secrets. Never copy values out.
- `evals/` — eval JSON per change (see root `EVALS.md`).

## Commands (from the Makefile)

```bash
make galaxy        # ansible-galaxy install -r ./requirements.yaml
make ops           # full playbook-index.yaml, -l ops (vm700)  — MUTATES
make diskstorage   # -l diskstorage                           — MUTATES
make vpn / make exo
```

One role alone (documented in `playbook-index.yaml`):

```bash
ansible-playbook -i protected/inventories/inventory-main.yaml playbook-index.yaml -l ops --tags <tag>
```

## Always dry-run first

```bash
scripts/ansible-dry-run.sh forgejo_stack            # --check --diff, changes nothing
scripts/ansible-dry-run.sh forgejo_stack -- -e forgejo_enabled=true
scripts/ansible-dry-run.sh <tag> --apply-for-real   # only with owner-approved intent
```

Read `changed=` in PLAY RECAP and the diff. Tasks marked `check_mode: false` (e.g. in
`tasks/imot2026.yaml`) still execute under `--check` — read the tag's tasks first.

Connectivity check: `scripts/verify_vm700.sh`. Role-specific runbooks live in
`roles/*/docs/` (e.g. `roles/ops-settings/docs/forgejo.md`).
