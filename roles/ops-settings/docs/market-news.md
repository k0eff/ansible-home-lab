# market-news stack on vm700 (`market_news_stack`)

Runs market-news `deploy/docker-compose.yml` (#1) on vm700 as its own compose project
`market-news`, from `/opt/market-news/src-tree/deploy`. Task file:
`roles/ops-settings/tasks/market-news.yaml`; defaults under `# --- market-news` in
`roles/ops-settings/defaults/main.yaml`. Kafka stays in the stack (D-0002). Live run authorised by
D-0003.

## Web — https://market-news.koeff.com (LAN only)

Caddy edge site in `templates/caddy/30-internal.caddy.j2` (T-34), snippet `market_news_proxy`,
defaults `caddy_host_market_news` / `caddy_market_news_port` (18736, the compose `WEB_PORT`).
Same pattern as `forgejo.koeff.com`: an A record to vm700's **private** address — the same
`caddy_backend_host` value forgejo's record uses — with the certificate by DNS-01 through
Cloudflare, so it works on the LAN only. The DNS record and the Caddy reload are the
orchestrator's (D-0003); deploy with `--tags caddy_stack -e caddy_enabled=true`.

**Known gap — the route is dead until market-news changes its bind.** market-news
`deploy/docker-compose.yml` publishes `web` on `127.0.0.1:18736` (T-33). Caddy runs in its own
bridge network and reaches backends at `caddy_backend_host`, not the host's loopback, so the
site answers 502 until `web` is published on vm700's LAN address (imot2026's
`imot2026_web_bind_host` pattern) — a change in the market-news repo.

## Deploy

```sh
ansible-playbook -i protected/inventories/inventory-main.yaml playbook-index.yaml -l ops \
  --tags market_news_stack -e market_news_enabled=true -e market_news_loki_url=http://<loki-host>:3100
```

`market_news_enabled` defaults to `false`, so a plain `make ops` never touches this stack.
`market_news_loki_url` has no default: the compose file requires `LOKI_URL` and the play asserts it.

## What the play does

1. Generates any missing secret (Postgres admin, `marketnews` DB user, Grafana admin) into
   `protected/market-news/` on the controller — the private submodule. Existing files are reused,
   never rotated. Commit them in `protected/` after the first run.
2. Resolves origin's sha of `market_news_git_ref` (not the local checkout's HEAD), `git archive`s
   `deploy/` + `worker/` (worker-metrics mounts `../worker`), rsyncs to vm700.
3. Renders `deploy/.env` from `templates/market-news/env.j2` (mode 0600) and starts the project
   with `docker_compose_v2` (`pull: missing`, `build: never` — all images are upstream).
4. Health gate — every step fails the play:
   - every service from `docker compose config --services` is `running`;
   - Postgres answers `select 1`;
   - Kafka produce/consume round trip of a unique token on topic `ansible-healthcheck`;
   - Temporal UI (`127.0.0.1:18733`) and Grafana `/api/health` (`127.0.0.1:18735`) return 200;
   - Prometheus (`127.0.0.1:18734`) `/api/v1/targets` has every active target `up`.

## Rollback

Not automated yet: a `state: absent` switch for this task file is the owned path (no hand-run
`docker compose` on vm700 — ansible owns Docker state there). Named volumes (`pgdata`, `kafkadata`, …) survive
`down`; backups land in `/var/lib/docker-data/market-news-backups`.
