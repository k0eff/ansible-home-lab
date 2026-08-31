# Caddy edge stack on vm700

Replaces the cluster ingress path (Envoy Gateway `koeff-public` / `koeff-public-v2`
plus five nginx Deployments in `ns-homelab-main`) with four Docker containers on
vm700 (192.168.31.152), managed by `roles/ops-settings/tasks/caddy.yaml`.

Deploy is `make ops` from `ansible-home-lab`, tag `caddy_stack`. Nothing here is
applied automatically — see **Enablement** below.

---

## 1. Why this shape

The stack was first written as a standalone compose project in
`koeff-gitroot-main/caddy-migration/`. The *routing* content of that work was
sound and is carried over. The *packaging* was wrong in four ways, each fixed
here:

| caddy-migration did | why it is wrong here | what this role does |
|---|---|---|
| `build: .` + a `Dockerfile` running `xcaddy build` | A `docker compose build` on vm700 peaked at 13.4 GB RSS on 2026-08-15 and OOM-killed RocketChat, LiteLLM and music-production-engine. See the header of `tasks/omniroute.yaml`. | Pull a published image by digest. **There is no Dockerfile in this role and there must never be one.** |
| Standalone compose project, run by hand | Not reproducible, not in the repo's conventions | Ansible role: `/opt/caddy` stack dir, `/var/lib/docker-data/caddy` state, `docker_image_pull` + `docker_compose_v2 (state: present, build: never)`, `uri` health gate |
| Targeted a hypothetical new host `192.168.31.230` | That host does not exist | Everything runs on vm700; `caddy_backend_host` = 192.168.31.152 |
| HTTP→HTTPS redirect left on (Caddy default) | Today's Gateway serves plain HTTP 200 on :80 with no redirect, and real traffic depends on it — see §5 | `auto_https disable_redirects` + explicit `http://` site blocks |

Two further corrections were made against the live cluster:

* `caddy-migration` copied `shareurl-index.html` with two edits (an added comment
  pair, a trimmed trailing space). This role ships the ConfigMap byte-for-byte.
* `caddy-migration` used `resolver 192.168.31.108` in the share nginx config.
  The QNAP is not a DNS server. See §7.

---

## 2. Image — pull, never build

```
ghcr.io/caddybuilds/caddy-cloudflare@sha256:a37a6472f8a47f9bc90b039ec8dec1eacd0115db666556f002662fca3fd8f397
```

= tag `2.11.4`, Caddy v2.11.4, linux/amd64, MIT, source
`github.com/CaddyBuilds/caddy-cloudflare`. Pinned by digest so a retag upstream
cannot change what runs at the edge.

**Modules compiled in: `caddy-dns/cloudflare` and `caddy-cloudflare-ip`. That is
all.** This has a hard consequence — see §9 (rate limiting).

Sidecar images:

| container | image | why this one |
|---|---|---|
| `nginx-share`, `nginx-share-v2`, `nginx-404` | `docker.io/nginx:1.27-alpine` | Exactly what the cluster Deployments run today |
| `nginx-shareurl` | `docker.io/bitnamilegacy/nginx:1.25.3-debian-11-r6` | See §8 — the `bitnami/` path now 404s from Docker Hub |

---

## 3. The 18 hostnames

`caddy_backend_host` = `192.168.31.152` (vm700). Every port below was read off
the live `Endpoints` objects in `ns-homelab-main`, not from the Service spec.

### Public — prod (7)

| # | hostname | backend | :80 today | non-default handling |
|---|---|---|---|---|
| 1 | `chat.koeff.com` | `{{caddy_backend_host}}:6666` (RocketChat) | **yes, 200** | WebSocket (native), unlimited body (Caddy default) |
| 2 | `n8n.koeff.com` | `192.168.31.110:5678` | **yes, 200** | WebSocket (native) |
| 3 | `overseerr.koeff.com` | `192.168.31.144:5055` | **yes, 307** | — |
| 4 | `musicengine.koeff.com` | `{{caddy_backend_host}}:8770` (granian) | no | rate limit **not reproduced** — §9 |
| 5 | `omniroute.koeff.com` | `{{caddy_backend_host}}:20130` | no | rate limit **not reproduced** — §9 |
| 6 | `share.koeff.com` | `nginx-share:8080` → `192.168.31.108:8080` (QNAP) | **yes, 404** | sidecar; `header_up Host share.koeff.com`; no `encode` — §7 |
| 7 | `shareurl.koeff.com` | `nginx-shareurl:8080` (static) | **yes, 200** | sidecar; bitnamilegacy image — §8 |

### Internal — `*.local.koeff.com` (3)

All three resolve to a **private** IP in public Cloudflare DNS. HTTPS only today;
no `:80` listener is defined for them, matching the Gateway.

koeff-ai-stack's LLM-routing services (`litellm.local.koeff.com`,
`qdrant.local.koeff.com`, `headroom.local.koeff.com`) were decommissioned
2026-08-31 — the VS Code Claude Code extension now talks to Anthropic
directly instead of through headroom → nginx-ingest → litellm. Only
prometheus + grafana remain from that stack.

| # | hostname | backend | non-default handling |
|---|---|---|---|
| 8 | `grafana.local.koeff.com` | `{{caddy_backend_host}}:3000` | WebSocket for live panels (native) |
| 9 | `prometheus.local.koeff.com` | `{{caddy_backend_host}}:9090` | — |
| 10 | `portainer.local.koeff.com` | `{{caddy_backend_host}}:9000` | WebSocket for container console/exec (native) |

### Public — v2 parallel-run (5)

Identical backends to their prod twins. `share-v2` gets its own sidecar because
its 404 body differs by one string (`share-v2.koeff.com` vs `share.koeff.com`).

| # | hostname | backend | :80 today |
|---|---|---|---|
| 14 | `chat-v2.koeff.com` | `{{caddy_backend_host}}:6666` | **yes** |
| 15 | `n8n-v2.koeff.com` | `192.168.31.110:5678` | **yes** |
| 16 | `overseerr-v2.koeff.com` | `192.168.31.144:5055` | **yes** |
| 17 | `share-v2.koeff.com` | `nginx-share-v2:8080` → `192.168.31.108:8080` | **yes** |
| 18 | `shareurl-v2.koeff.com` | `nginx-shareurl:8080` | **yes** |

### Not one of the 18

`koeff-public-v2.koeff.com` — HTTPRoute `koeff-public-v2-smoke`, **HTTP-only**,
backend `nginx-url-converter` (same as shareurl). It is the 11th hostname
answering on :80. Carried as `caddy_host_koeff_public_v2` so the `:80` parity
count is exact; set it to `""` to drop it.

### Fallback

`koeff-404` — the cluster runs it as a Deployment behind the legacy
nginx-Ingress `qnap-ingress-404`. Here it is the catch-all for unmatched
hostnames on **:80 only**. Note it answers **200**, not 404 (`try_files
/index.html =404` serves the file). No catch-all is defined on :443 — see §10.

---

## 4. TLS

* ACME **Let's Encrypt production**, DNS-01 via Cloudflare.
* Credentials are **referenced, never copied**. The role reads
  `CERT_MANAGER_CLOUDFLARE_TOKEN` and `CERT_MANAGER_CLOUDFLARE_EMAIL` from the
  controller's environment with `lookup('env', ...)` and asserts they are set.
  They are the same values cert-manager already uses, defined in
  `helmfile-home-lab/protected/envs/cluster00/vars.sh`. **Nothing is duplicated
  into `ansible-home-lab/protected/`** — this repo is public.
* Certificates and Caddy state live on a bind mount:
  `{{ caddy_data_dir }}/data` → `/data`, `{{ caddy_data_dir }}/config` → `/config`.
  Losing `/data` silently re-issues 18 certificates and burns Let's Encrypt rate
  limits.
* DNS-01 is **required**, not a preference. Six `*.local.koeff.com` names
  resolve to a private IP, so HTTP-01 can never validate them; and during the
  parallel run the router still forwards :80/:443 to the cluster, so HTTP-01 is
  unreachable for *every* hostname.
* `acme_dns cloudflare` is set with explicit `resolvers 1.1.1.1:53 8.8.8.8:53`
  and `propagation_timeout`. The LAN resolver 192.168.31.149 is down; leaving
  the propagation check on the host's default resolver is how DNS-01 hangs.

---

## 5. No HTTP→HTTPS redirect

Caddy redirects HTTP→HTTPS by default. **It is disabled here.** Measured, not assumed:

* Today's Gateway serves plain HTTP 200 on :80 with no redirect. Re-verified
  against 192.168.31.235 while writing this: `shareurl` 200, `chat` 200,
  `n8n` 200, `overseerr` 307 (its own login redirect, not a TLS redirect),
  `share` 404 (its own `location / { return 404; }`).
* 19 hours of Envoy access logs show real traffic on :80:
  `overseerr.koeff.com` 1247 requests (`/` → 307 → `/login` → 200, every ~1.6
  min — an automated monitor), `chat.koeff.com` 334 requests of which 77 have
  `response_code 0`, i.e. terminated WebSocket connections.
* A redirect can break a WebSocket upgrade on clients that do not follow
  redirects on upgrade.

Phase 1 mirrors today exactly. The redirect is a separate, later decision.

Mechanically this means, in `Caddyfile.j2`:

```
auto_https disable_redirects
```

plus an explicit `http://<host>` site block for each of the 11 hostnames that
answer on :80 today. A bare `host { ... }` block in Caddy binds :443 only once
redirects are disabled — the :80 blocks are not optional decoration, they are
the only thing serving plain HTTP.

Both schemes must proxy **identically**. That is enforced structurally: the
proxy body for each host is written once as a Caddyfile snippet in
`10-snippets.caddy.j2` and `import`ed by both the `https` and the `http` block.
Never inline a `reverse_proxy` in a site block.

---

## 6. Every non-default directive, and why

| directive | where | reason |
|---|---|---|
| `auto_https disable_redirects` | global | §5. Preserve plain-HTTP :80. |
| `servers { protocols h1 h2 }` | global | **Disables HTTP/3.** Caddy would otherwise listen on UDP/443 and advertise `Alt-Svc`, but the router forwards TCP only. Clients attempt H3, stall, fall back — intermittent slow first connections. Re-enable only after UDP/443 is forwarded. |
| `acme_dns cloudflare {env.CF_API_TOKEN}` | global | §4. Only DNS-01 can validate these names. |
| `resolvers 1.1.1.1:53 8.8.8.8:53` (inside `acme_dns`) | global | The LAN resolver is down; the propagation check must not use it. |
| `email {env.ACME_EMAIL}` | global | Same LE account contact cert-manager uses. |
| `admin localhost:2019` | global | Admin API is full config control. Bound to container loopback and **not published**. Health checks go through :80 with a `Host:` header instead. |
| `log { format json }` | global | Matches the Envoy access-log format the :80 traffic analysis was done against. |
| `header_up Host share.koeff.com` | `share`, `share-v2` | The nginx sidecar's `sub_filter` and CSP rewrite are written against that literal Host. `share-v2` also sends `share.koeff.com` — this matches the cluster ConfigMap, which is not a typo. |
| `header_up X-Forwarded-Proto https` / `X-Forwarded-For` | `share`, `share-v2` | The sidecar re-forwards these to the QNAP. |
| **no** `encode` on `share`/`share-v2` | `share`, `share-v2` | The sidecar sets `proxy_set_header Accept-Encoding ""` to force plaintext upstream so `sub_filter` can rewrite the body. Compressing at Caddy would not break that, but any Caddy-side `encode` on this path is a foot-gun; leave it off and let the sidecar own content handling. |
| *(nothing)* for WebSockets | `chat`, `n8n`, `grafana`, `portainer`, and their v2 twins | Caddy 2 proxies WebSocket upgrades natively. There is no directive. Listed here so a future reader does not "fix" its absence. |
| *(nothing)* for body size | `chat` | Caddy has **no** request-body limit by default. `proxy-body-size 0` needs no equivalent. Adding `request_body { max_size }` would be a regression. |

---

## 7. The share sidecars

`files/caddy/nginx/share-prod-proxy.conf` and `share-v2-proxy.conf` are the
ConfigMaps `share-prod-proxy-nginx` and `share-v2-proxy-nginx` from
`ns-homelab-main`, **verbatim** except for two mechanical substitutions:

```
resolver 169.254.25.10 valid=30s ipv6=off;
  ->  resolver 127.0.0.11 valid=30s ipv6=off;

http://qnap-qumagie-service.ns-homelab-main.svc.cluster00.local:8090/...
  ->  http://192.168.31.108:8080/...
```

* `169.254.25.10` is nodelocaldns — cluster-only. `127.0.0.11` is Docker's
  embedded resolver, its exact counterpart on a user-defined bridge network.
  It is in practice inert: every `proxy_pass` here uses a literal IP with no
  variables, so nginx resolves at config-load time and never consults the
  resolver. It is kept rather than deleted so the file stays diffable against
  the ConfigMap.
* The `:8090` → `:8080` change is **not** a typo fix. The k8s Service listens on
  8090 and its `Endpoints` object points at `192.168.31.108:8080`. Verified:
  `kubectl get endpoints -n ns-homelab-main qnap-qumagie-service`.

**Nothing else is edited.** The `sub_filter` chain, the CSP `add_header`, the
OpenGraph injection, the pinned `brand.js` commit hash, the inline `custom_404`
body, and the `/sl/ /share/ /qumagie/ /v3_menu/` path matrix are byte-identical.
Confirm with `diff` against the live ConfigMap before every change to these files.

The observable path matrix, which the verify script asserts:
`/qumagie/` 200 · `/share/` 200 · `/sl/` 200 · `/v3_menu/` 403 · `/` 404.

---

## 8. Known blocker — the shareurl image

The live `nginx-url-converter` Deployment runs
`docker.io/bitnami/nginx:1.25.3-debian-11-r6`, which now returns **HTTP 404**
from Docker Hub after Bitnami's 2025 restructuring. Verified working
replacement: `docker.io/bitnamilegacy/nginx:1.25.3-debian-11-r6` (HTTP 200).

This image's *built-in filesystem layout* is load-bearing:

* The ConfigMap `nginx-url-converter-overridden-http` replaces the **whole**
  `/opt/bitnami/nginx/conf` directory — all nine keys. That is why
  `files/caddy/nginx/shareurl-conf/` ships all nine files and is mounted as a
  directory, not file-by-file. Mounting only `nginx.conf` would leave the
  image's `bitnami/` and `server_blocks/` subdirectories in place and change
  behaviour.
* Inside that `nginx.conf`, `include ".../server_blocks/*.conf"` and
  `include ".../bitnami/*.conf"` both **glob to nothing**, so the single
  `server { listen 8080; ... }` block has **no `root` directive**. The served
  root therefore falls back to the image's compiled-in default, which resolves
  to `/app` via the image's own layout.
* The static site is consequently mounted at **`/app/index.html`**, which is
  where the cluster mounts it (`staticSiteConfigmap` → `/app`).
  `caddy-migration` mounted it at `/opt/bitnami/nginx/html/index.html`; that
  only works if the runtime follows the image's symlink at mount time. Do not
  rely on it. Mount `/app/index.html`.

Confirmed serving correctly today: `curl --resolve shareurl.koeff.com:80:192.168.31.235`
returns the converter page, 200.

---

## 9. Accepted behaviour delta — rate limiting

`musicengine.koeff.com` and `omniroute.koeff.com` each carry an Envoy
`BackendTrafficPolicy` with a local rate limit of **50 requests/second**
(`musicengine-ratelimit`, `omniroute-ratelimit`).

`caddy-migration/caddy/01-public.caddy` reproduced these with `rate_limit`
blocks. **Those must not be carried over.** `rate_limit` comes from
`github.com/mholt/caddy-ratelimit`, which is *not* compiled into
`ghcr.io/caddybuilds/caddy-cloudflare`. Caddy refuses to load a Caddyfile
containing an unknown directive, so shipping them would take the entire edge
down at startup — all 18 hostnames, not just those two.

Adding the module requires building the image, which is forbidden on this host
(§2). So Phase 1 ships **without** rate limiting on those two hostnames. Options
for a later phase, in preference order:

1. Find or publish a prebuilt image carrying both `caddy-dns/cloudflare` and
   `caddy-ratelimit`, and re-pin by digest.
2. Build the image somewhere that is not vm700 and push it to a registry.
3. Leave the cluster's `BackendTrafficPolicy` in force by keeping those two
   hostnames on the Gateway during cutover.

---

## 10. Deliberate omissions

* **No `:443` catch-all.** A wildcard HTTPS site would drag in on-demand TLS and
  let any SNI trigger a certificate order. Today's Gateway has per-hostname
  `HTTPS` listeners and fails the handshake for unknown SNI; Caddy does the same
  with no catch-all defined. `caddy-migration`'s `on_demand_tls { ask ... }`
  block is dropped.
* **No `storage file_system { root /data/caddy }`.** Caddy's default storage
  root under `/data` already yields `/data/caddy/`; the explicit block nests it
  one level deeper for no benefit and breaks any future `caddy` CLI expectation.
  Mount `/data` and leave storage at its default.
* **No published port for any nginx sidecar.** Host port 8080 on vm700 is taken
  by the `playwright-mcp` container. The sidecars are reachable only from Caddy
  over the stack's internal Docker network. `expose:` only, never `ports:`.
* **No Dockerfile.** §2.

---

## 11. Enablement and blast radius

This stack binds host **:80 and :443**. Both are free on vm700 today (verified
on the host). Nothing else in `ops-settings` binds a privileged port, so the
first `make ops` after this lands would otherwise start an edge proxy nobody
asked for.

`caddy_enabled` therefore defaults to **`false`**. Turn it on deliberately:

```sh
# in helmfile-home-lab, to get the Cloudflare credentials into the environment
. ./protected/main.sh cluster00

# then, in the SAME shell, from ansible-home-lab
make ops                      # caddy_stack is skipped while caddy_enabled=false
ansible-playbook -i protected/inventories/inventory-main.yaml playbook-index.yaml \
  -l ops --tags caddy_stack -e caddy_enabled=true
```

`lookup('env', ...)` is evaluated on the **Ansible controller** (the Mac), not
on vm700 — hence "the same shell".

**Do a staging dry run first.** Issuing 18 production certificates for
`koeff.com` in one go, while cert-manager still holds 18 of its own, moves
meaningfully toward Let's Encrypt's 50-certificates-per-registered-domain-per-week
ceiling, and a failed run that is retried is unrecoverable for a week:

```sh
ansible-playbook ... --tags caddy_stack \
  -e caddy_enabled=true \
  -e caddy_acme_ca=https://acme-staging-v02.api.letsencrypt.org/directory \
  -e caddy_data_dir=/var/lib/docker-data/caddy-staging
```

The separate `caddy_data_dir` matters: staging and production certificates must
not share a storage root, or the production run will find staging certs already
present and not re-issue.

---

## 12. Verification

`scripts/migrations/verify_caddy.sh` runs from the controller and takes no arguments;
it targets vm700 by IP and never depends on public DNS. It asserts, for all 18
hostnames plus the fallback:

* HTTPS on `192.168.31.152:443` with SNI, comparing the status code against the
  live Gateway VIP (`192.168.31.235` public, `192.168.31.237` internal/v2).
* Plain HTTP on `192.168.31.152:80` for the 11 hostnames that answer on :80
  today — asserting the body is served, **not a 301/308**. A redirect here is
  the single most likely regression.
* The `share.koeff.com` path matrix from §7.
* Certificate issuer and SAN per hostname, so a staging cert cannot be mistaken
  for a production one.
* `Alt-Svc` absent (HTTP/3 off) and no UDP/443 listener.

`scripts/verify_vm700.sh` gains 80 and 443 to its port sweep. That is the only change to
that file.

---

## 13. Provenance

Everything under `files/caddy/nginx/` was extracted read-only from the live
cluster on 2026-08-16 with:

```sh
kubectl get cm -n ns-homelab-main <name> -o jsonpath='{.data}'
```

| file | source ConfigMap | key | edited? |
|---|---|---|---|
| `share-prod-proxy.conf` | `share-prod-proxy-nginx` | `default.conf` | resolver + 4 × `proxy_pass` only |
| `share-v2-proxy.conf` | `share-v2-proxy-nginx` | `default.conf` | resolver + 4 × `proxy_pass` only |
| `shareurl-index.html` | `nginx-url-converter-static-site` | `index.html` | byte-identical |
| `shareurl-conf/*` (9 files) | `nginx-url-converter-overridden-http` | all keys | byte-identical |
| `koeff-404-default.conf` | `koeff-404-static` | `default.conf` | byte-identical |
| `koeff-404-index.html` | `koeff-404-static` | `index.html` | byte-identical |

Backend addresses came from `Endpoints`, not `Service`, objects — the Services
are headless or (for the QNAP) have a port number that differs from the target.
