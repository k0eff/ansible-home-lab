# Forgejo on vm700 (T-1821)

The self-hosted forge for every repo under `koeff-gitroot-main`. GitHub stays an upstream mirror
for finished code only. Owner, 2026-09-20: «ок съм с forgejo по принцип», confirmed on
2026-09-21 and 2026-09-22 (T-1821 history in imotbgScraper2026).

| What | Where |
|---|---|
| Tasks | `roles/ops-settings/tasks/forgejo.yaml`, tag `forgejo_stack` |
| Compose | `templates/forgejo/docker-compose.yml.j2` → `/opt/forgejo/docker-compose.yml` |
| Data (repos, sqlite, config, secrets it generated) | `/var/lib/docker-data/forgejo` on vm700 |
| Web | `https://forgejo.koeff.com` via the Caddy edge (`30-internal.caddy.j2`), certificate by DNS-01 |
| Git over SSH | port `2222` on the same name |
| Image | `codeberg.org/forgejo/forgejo`, v15 LTS, pinned by digest in `defaults/main.yaml` |

`forgejo.koeff.com` is an A record to vm700's **private** address, so it works on the LAN only.
The certificate still issues, because Caddy proves the name through Cloudflare DNS, not HTTP.

## Deploy

```sh
# Forgejo itself
ansible-playbook -i protected/inventories/inventory-main.yaml playbook-index.yaml \
  -l ops --tags forgejo_stack -e forgejo_enabled=true

# the Caddy route (needs the Cloudflare credentials in the SAME shell — see docs/caddy.md §11)
. ../helmfile-home-lab/protected/main.sh cluster00
ansible-playbook -i protected/inventories/inventory-main.yaml playbook-index.yaml \
  -l ops --tags caddy_stack -e caddy_enabled=true
```

The play fails unless `/api/healthz` answers 200 and `/api/v1/version` reports the pinned version.

## First administrator — the owner runs this, not an agent

Registration is closed (`DISABLE_REGISTRATION`) and the web installer is locked (`INSTALL_LOCK`),
so nobody on the LAN can claim the instance first. The one account is created by CLI:

```sh
ssh vm700 docker exec -u git forgejo forgejo admin user create \
  --admin --username <name> --email <address> --random-password --must-change-password
```

It prints a one-time password; log in at the web address and set a real one. Agents do not create
accounts or tokens. An API token for automation (T-1822) is created by the owner in the web UI
(Settings → Applications) and handed to the script through the environment, never committed.

## Rollback

Forgejo is additive — nothing else depends on it yet. To remove it:

```sh
ssh vm700 'cd /opt/forgejo && docker compose down'
```

and set `forgejo_enabled: false` (the default). The data directory is left in place on purpose;
deleting `/var/lib/docker-data/forgejo` destroys every repository and issue in it and needs the
owner's own approval. A version rollback follows the Portainer rule: Forgejo migrates its database
forward on upgrade, so an older image cannot read a newer database — restore the data directory
from a backup taken before the upgrade.
