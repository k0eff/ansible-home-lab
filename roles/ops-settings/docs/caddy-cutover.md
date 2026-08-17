# Caddy Edge Stack — Cutover Runbook

This document covers the transition from the cluster's Envoy Gateway (192.168.31.235 public, 192.168.31.237 internal/v2) to the Caddy edge stack on vm700 (192.168.31.152).

---

## Phase 1: Parallel Run (Caddy up, DNS unchanged)

Caddy is running on vm700 but public DNS still points at the cluster VIPs. This window allows full verification before touching DNS.

### 1.1 Prerequisites

In `helmfile-home-lab`, source the cluster secrets in your shell:

```sh
cd helmfile-home-lab
. ./protected/main.sh cluster00
```

Verify the Cloudflare credentials are loaded:

```sh
echo $CERT_MANAGER_CLOUDFLARE_TOKEN    # should print a long token, NOT empty
echo $CERT_MANAGER_CLOUDFLARE_EMAIL    # should print an email
```

**Do not close this shell.** `lookup('env', ...)` is evaluated on the Ansible controller (your Mac), not on vm700.

### 1.2 Deploy to staging (strongly recommended first)

Test the whole flow without touching production LE rate limits:

```sh
cd ../ansible-home-lab
ansible-playbook -i protected/inventories/inventory-main.yaml playbook-index.yaml \
  -l ops --tags caddy_stack \
  -e caddy_enabled=true \
  -e caddy_acme_ca=https://acme-staging-v02.api.letsencrypt.org/directory \
  -e caddy_data_dir=/var/lib/docker-data/caddy-staging
```

Wait for the play to complete. The health gate waits up to 300 seconds for DNS-01 issuance of 18 staging certificates.

### 1.3 Verify the staging deployment

Once `ansible-playbook` returns, Caddy is running. Verify connectivity and certificate issuance:

```sh
# Test HTTP on a few public hosts (should not redirect to HTTPS)
curl -i --resolve chat.koeff.com:80:192.168.31.152 http://chat.koeff.com/
curl -i --resolve shareurl.koeff.com:80:192.168.31.152 http://shareurl.koeff.com/

# Test HTTPS on an internal host (production cert should not be present yet)
curl -i --resolve grafana.local.koeff.com:443:192.168.31.152 https://grafana.local.koeff.com/
# Should show an untrusted staging cert; this is expected and correct.

# Check certificate details
openssl s_client -connect 192.168.31.152:443 \
  -servername chat.koeff.com < /dev/null | grep -A 2 "subject="
# Look for "CN = *.koeff.com (Fake LE)" — the staging issuer.
```

Once satisfied, clean up the staging deployment:

```sh
# SSH to vm700 and remove staging containers
ssh root@192.168.31.152 'docker compose -p caddy -C /var/lib/docker-data/caddy-staging down'
rm -rf /var/lib/docker-data/caddy-staging
```

### 1.4 Deploy to production

From the same shell where you sourced `protected/main.sh cluster00`:

```sh
ansible-playbook -i protected/inventories/inventory-main.yaml playbook-index.yaml \
  -l ops --tags caddy_stack \
  -e caddy_enabled=true
```

This uses `caddy_acme_ca=https://acme-v02.api.letsencrypt.org/directory` (production) and
`caddy_data_dir=/var/lib/docker-data/caddy` by default. The health gate waits up to 300 seconds for production certificate issuance.

---

## Phase 2: Verification on Caddy

### 2.1 Port connectivity

```sh
nc -z -G 2 192.168.31.152 80
nc -z -G 2 192.168.31.152 443
```

Both should print nothing (success). If either fails, deployment did not complete or a port is blocked.

### 2.2 HTTP — 11 hostnames that answer on :80

Verify that plain HTTP is served **without a redirect to HTTPS**. This is the single most likely regression.

```sh
# Public hostnames
curl -i --resolve chat.koeff.com:80:192.168.31.152 http://chat.koeff.com/ | head -1
curl -i --resolve n8n.koeff.com:80:192.168.31.152 http://n8n.koeff.com/ | head -1
curl -i --resolve overseerr.koeff.com:80:192.168.31.152 http://overseerr.koeff.com/ | head -1
curl -i --resolve share.koeff.com:80:192.168.31.152 http://share.koeff.com/ | head -1
curl -i --resolve shareurl.koeff.com:80:192.168.31.152 http://shareurl.koeff.com/ | head -1

# v2 parallel-run hostnames
curl -i --resolve chat-v2.koeff.com:80:192.168.31.152 http://chat-v2.koeff.com/ | head -1
curl -i --resolve n8n-v2.koeff.com:80:192.168.31.152 http://n8n-v2.koeff.com/ | head -1
curl -i --resolve overseerr-v2.koeff.com:80:192.168.31.152 http://overseerr-v2.koeff.com/ | head -1
curl -i --resolve share-v2.koeff.com:80:192.168.31.152 http://share-v2.koeff.com/ | head -1
curl -i --resolve shareurl-v2.koeff.com:80:192.168.31.152 http://shareurl-v2.koeff.com/ | head -1
curl -i --resolve koeff-public-v2.koeff.com:80:192.168.31.152 http://koeff-public-v2.koeff.com/ | head -1
```

**All headers must start with `HTTP/1.1 200`, `HTTP/2 200`, or `HTTP/1.1 307/404`** (overseerr redirects to /login, share serves 404 at root).
**NONE must contain a 301/302/307/308 redirect to HTTPS.** If you see a redirect, the cutover is not ready.

Compare these against today's Gateway VIP (while it is still live):

```sh
curl -i --resolve chat.koeff.com:80:192.168.31.235 http://chat.koeff.com/ | head -1
# Should be identical to the Caddy result above
```

### 2.3 HTTPS — all 18 hostnames + fallback

Test that HTTPS answers and carries a production Let's Encrypt certificate:

```sh
# Sample a few: public, internal, v2
curl -i --resolve chat.koeff.com:443:192.168.31.152 https://chat.koeff.com/ | head -1
curl -i --resolve grafana.local.koeff.com:443:192.168.31.152 https://grafana.local.koeff.com/ | head -1
curl -i --resolve chat-v2.koeff.com:443:192.168.31.152 https://chat-v2.koeff.com/ | head -1
```

All should return 200 or a valid backend status (200, 307, etc.), **not a certificate error**.

Check the certificate issuer to confirm it is production (not staging):

```sh
openssl s_client -connect 192.168.31.152:443 \
  -servername chat.koeff.com < /dev/null | grep "Issuer:"
# Should show: Issuer: C = US, O = Let's Encrypt, CN = R6
# NOT: Issuer: C = US, O = (Staging) Let's Encrypt
```

### 2.4 share.koeff.com path matrix

The QNAP proxy must serve the exact path matrix (or a 404 fallback). Test all four paths:

```sh
curl -i --resolve share.koeff.com:443:192.168.31.152 https://share.koeff.com/qumagie/ | head -1
# Expected: HTTP/2 200

curl -i --resolve share.koeff.com:443:192.168.31.152 https://share.koeff.com/share/ | head -1
# Expected: HTTP/2 200

curl -i --resolve share.koeff.com:443:192.168.31.152 https://share.koeff.com/sl/ | head -1
# Expected: HTTP/2 200

curl -i --resolve share.koeff.com:443:192.168.31.152 https://share.koeff.com/v3_menu/ | head -1
# Expected: HTTP/2 403

curl -i --resolve share.koeff.com:443:192.168.31.152 https://share.koeff.com/ | head -1
# Expected: HTTP/2 404
```

### 2.5 Injected headers (share sidecar)

The nginx sidecar injects two markers into the response:

```sh
# Test for X-Disabled-CSP header (nginx removes the CSP header and adds this marker)
curl -i --resolve share.koeff.com:443:192.168.31.152 https://share.koeff.com/share/ 2>&1 | grep -i "X-Disabled-CSP"
# Should print: X-Disabled-CSP: true

# Test for injected og:title (OpenGraph override for social sharing)
curl -s --resolve share.koeff.com:443:192.168.31.152 https://share.koeff.com/share/ 2>&1 | grep -i "og:title"
# Should print a meta tag with og:title="Koeff Share"
```

### 2.6 ACME renewal (certificates are persisted)

Verify that certificates are stored and persisted across restarts:

```sh
# SSH to vm700
ssh root@192.168.31.152

# List stored certificates
ls -lh /var/lib/docker-data/caddy/data/caddy/certificates/

# Should show multiple directories, one per ACME account.
# Certificate files are auto-renewed by Caddy; no manual renewal needed.
```

### 2.7 HTTP/3 is OFF

Verify that `Alt-Svc` is absent (HTTP/3 is disabled):

```sh
curl -i --resolve chat.koeff.com:443:192.168.31.152 https://chat.koeff.com/ 2>&1 | grep -i "alt-svc"
# Should print nothing (absent). If present, HTTP/3 is enabled and should be disabled.
```

### 2.8 Fallback for unknown hostnames

An unknown hostname on :80 should get the koeff-404 fallback page (status 200, not 404):

```sh
curl -i --resolve unknown-host.invalid:80:192.168.31.152 http://unknown-host.invalid/ | head -3
# Expected:
# HTTP/1.1 200 OK
# Content-Type: text/html; charset=utf-8
# (body: the nice fallback page)
```

---

## Phase 3: DNS Cutover

Once all verifications pass, update DNS records one at a time in this order (lowest-stakes first):

| # | hostname | note | current IP | new IP |
|---|---|---|---|---|
| 1 | prometheus.local.koeff.com | monitoring | 192.168.31.237 | 192.168.31.152 |
| 2 | grafana.local.koeff.com | monitoring | 192.168.31.237 | 192.168.31.152 |
| 3 | litellm.local.koeff.com | internal LLM | 192.168.31.237 | 192.168.31.152 |
| 4 | headroom.local.koeff.com | internal (600s timeout) | 192.168.31.237 | 192.168.31.152 |
| 5 | qdrant.local.koeff.com | internal vector DB | 192.168.31.237 | 192.168.31.152 |
| 6 | portainer.local.koeff.com | container management | 192.168.31.237 | 192.168.31.152 |
| 7 | share.koeff.com | QNAP proxy | 192.168.31.235 | 192.168.31.152 |
| 8 | shareurl.koeff.com | static site | 192.168.31.235 | 192.168.31.152 |
| 9 | omniroute.koeff.com | routing dashboard | 192.168.31.235 | 192.168.31.152 |
| 10 | musicengine.koeff.com | music granian | 192.168.31.235 | 192.168.31.152 |
| 11 | overseerr.koeff.com | media management | 192.168.31.235 | 192.168.31.152 |
| 12 | n8n.koeff.com | workflows **[LATE]** | 192.168.31.235 | 192.168.31.152 |
| 13 | chat.koeff.com | RocketChat **[LAST]** | 192.168.31.235 | 192.168.31.152 |

**For each cutover:**

1. Update the DNS A record in Cloudflare.
2. Wait 5 seconds for the TTL to begin expiring.
3. Verify the new IP is resolving: `dig +short <hostname>`
4. Test via the public hostname (through Cloudflare): `curl -i https://<hostname>/`
5. Confirm the status matches the parallel-run test.

**Do chat.koeff.com and n8n.koeff.com last** because they carry real-time traffic (WebSocket, automated monitors). Early-cutover failures on these are more disruptive.

### 3.1 Verifying DNS propagation

After updating each record, allow time for the TTL to expire (currently 1 hour; adjust for faster cutover if TTL is reduced). Check propagation:

```sh
# Flush your local DNS cache (macOS)
dscacheutil -flushcache

# Verify the new IP
dig +short chat.koeff.com
# Should return 192.168.31.152

# Test through the public DNS (not --resolve override)
curl -i https://chat.koeff.com/ | head -1
# Should succeed through Caddy on 192.168.31.152
```

---

## Phase 4: Cleanup and Verification

Once all 13 hostnames have been cut over and verified for 24 hours without incident:

### 4.1 Decommission the cluster ingress

The Gateway VIPs 192.168.31.235 and 192.168.31.237 remain live and untouched throughout the cutover. If a rollback is needed, DNS can be reverted instantly. After a day of successful operation, you may remove the Gateway ingress.

### 4.2 Enable v2 in parallel or sunset it

The five `-v2` hostnames (chat-v2, n8n-v2, overseerr-v2, share-v2, shareurl-v2) are for the parallel-run phase. If you are keeping both edges running for a gradual migration, leave `caddy_v2_enabled: true`. To sunset the v2 endpoints:

```sh
ansible-playbook ... --tags caddy_stack -e caddy_v2_enabled=false
```

This reloads Caddy without the five -v2 site blocks but keeps the other 18 hostnames running.

### 4.3 Run the verification script regularly

The verification script `scripts/migrations/verify_caddy.sh` (created separately) runs from the controller and asserts all 18 hostnames plus the fallback. Add it to monitoring or cron:

```sh
cd ansible-home-lab
./scripts/migrations/verify_caddy.sh
```

Should exit 0 if all probes pass.

---

## Rollback (if needed)

Caddy is running on vm700 and DNS points to it, but production is broken.

### 4.1 Revert one hostname at a time (in reverse order)

```sh
# In Cloudflare, change chat.koeff.com A record back to 192.168.31.235
# Wait for TTL to expire (1 hour)
dig +short chat.koeff.com
# Should return 192.168.31.235
```

### 4.2 Full rollback (if Caddy is down)

If vm700 or Caddy itself is broken and you need to restore service immediately:

1. **Do not wait for DNS TTL.** Update every hostname back to the cluster VIPs:
   - Public: 192.168.31.235 (chat, n8n, overseerr, musicengine, omniroute, share, shareurl)
   - Internal: 192.168.31.237 (grafana, prometheus, litellm, qdrant, headroom, portainer)

2. The cluster Envoy Gateway remains live and untouched. Traffic reverts instantly.

3. Once Caddy is repaired or rebuilt, you can re-run the cutover.

### 4.3 Inspect Caddy logs (debugging)

```sh
ssh root@192.168.31.152

# See Caddy's JSON logs
docker logs -f caddy-caddy-1 | jq .

# Or raw (no JSON parsing)
docker logs -f caddy-caddy-1

# Check the admin API (from inside the container)
docker exec caddy-caddy-1 curl localhost:2019/config/
```

---

## Known Blockers and Workarounds

### Rate limiting is absent

`musicengine.koeff.com` and `omniroute.koeff.com` lose their 50 req/s rate limit during this phase. The cluster's `BackendTrafficPolicy` remains in force; see README.md §9 for options in a later phase.

### ACME rate limits

If you re-run the production deployment more than a few times on the same day, Let's Encrypt may rate-limit your account for that week. Use the staging deployment (§1.2) for most tests, and the production deployment only once, per week, per version change.

### The cluster Gateway remains live

Do not delete the Gateway or its VIPs until the Caddy cutover is complete and verified. They are your rollback path.
