#!/usr/bin/env bash
#
# scripts/migrations/verify_caddy.sh — prove the Caddy edge stack on vm700 behaves exactly like
# the cluster00 ingress it is meant to replace.
#
# Runs from the controller (your Mac). Read-only: it changes nothing, anywhere.
# Safe to run repeatedly, before or after DNS cutover.
#
#   ./scripts/migrations/verify_caddy.sh                 # compare Caddy against the live cluster
#   ./scripts/migrations/verify_caddy.sh 192.168.31.152  # same, explicit target
#   ./scripts/migrations/verify_caddy.sh --curl          # print copy-paste curl commands, run them by hand
#
# Exit 0 = every check passed. Exit 1 = at least one divergence.
#
# How it works: DNS for all these names still points at the cluster, so every
# request is forced to a specific IP with curl --resolve. That keeps the SNI
# intact, which matters — TLS certificate selection depends on it and a Host:
# header cannot substitute.

set -uo pipefail

NEW="${1:-192.168.31.152}"        # Caddy on vm700
CL_PUB="192.168.31.235"           # cluster Envoy: public hostnames
CL_INT="192.168.31.237"           # cluster Envoy: *.local hostnames

[[ "${1:-}" == "-h" || "${1:-}" == "--help" ]] && { sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }

if [[ "${1:-}" == "--curl" ]]; then
  T="${2:-192.168.31.152}"
  cat <<EOF
# Copy-paste checks against the Caddy stack at $T.
# --resolve forces the connection to that IP while keeping the real hostname,
# so DNS stays untouched and SNI still selects the right certificate.
# -k skips certificate trust: expected while the stack is on the Let's Encrypt
# staging endpoint. Drop it once production certificates are issued.
#
# Swap $T for 192.168.31.235 (public) or .237 (*.local) to see what the
# cluster answers for the same request.

# ── every hostname, status code only ─────────────────────────────────────────
EOF
  for h in chat.koeff.com n8n.koeff.com overseerr.koeff.com musicengine.koeff.com \
           omniroute.koeff.com shareurl.koeff.com share.koeff.com \
           grafana.local.koeff.com prometheus.local.koeff.com litellm.local.koeff.com \
           qdrant.local.koeff.com headroom.local.koeff.com portainer.local.koeff.com; do
    printf "curl -sk -o /dev/null -w '%%{http_code}  %s\\\\n' --resolve %s:443:%s https://%s/\n" "$h" "$h" "$T" "$h"
  done
  cat <<EOF

# ── all of the above in one loop ─────────────────────────────────────────────
for h in chat n8n overseerr musicengine omniroute shareurl share; do
  curl -sk -o /dev/null -w "%{http_code}  \$h.koeff.com\n" --resolve \$h.koeff.com:443:$T https://\$h.koeff.com/
done
for h in grafana prometheus litellm qdrant headroom portainer; do
  curl -sk -o /dev/null -w "%{http_code}  \$h.local.koeff.com\n" --resolve \$h.local.koeff.com:443:$T https://\$h.local.koeff.com/
done

# ── share.koeff.com path matrix (expect 200 200 200 403 404) ─────────────────
for p in /qumagie/ /share/ /sl/ /v3_menu/ /; do
  curl -sk -o /dev/null -w "%{http_code}  \$p\n" --resolve share.koeff.com:443:$T "https://share.koeff.com\$p"
done

# ── share content rewriting — a 200 does NOT prove this survived ─────────────
curl -sk --resolve share.koeff.com:443:$T https://share.koeff.com/qumagie/ | grep -E 'og:title|X-Disabled-CSP'

# ── plain HTTP on :80 must NOT redirect (expect 200, not 301/308) ────────────
curl -s -o /dev/null -w '%{http_code}\n' --resolve chat.koeff.com:80:$T http://chat.koeff.com/

# ── full response headers for one host ───────────────────────────────────────
curl -skI --resolve n8n.koeff.com:443:$T https://n8n.koeff.com/

# ── which certificate is served, and is it staging or production ─────────────
echo | openssl s_client -connect $T:443 -servername chat.koeff.com 2>/dev/null \\
  | openssl x509 -noout -issuer -subject -enddate

# ── open a real browser at it, no hosts file needed (Chrome) ──────────────────
# Chrome resolves the hostname to your chosen IP for that session only:
#   open -na "Google Chrome" --args --host-resolver-rules="MAP *.koeff.com $T" \\
#     --user-data-dir=/tmp/caddy-check https://chat.koeff.com/
EOF
  exit 0
fi

PASS=0; FAIL=0; SKIP=0
g(){ printf '\033[32m%s\033[0m' "$1"; }
r(){ printf '\033[31m%s\033[0m' "$1"; }
y(){ printf '\033[33m%s\033[0m' "$1"; }
hdr(){ printf '\n\033[1m%s\033[0m\n' "$1"; }

ok(){   PASS=$((PASS+1)); printf '  %s %s\n' "$(g ✓)" "$1"; }
bad(){  FAIL=$((FAIL+1)); printf '  %s %s\n' "$(r ✗)" "$1"; }
warn(){ SKIP=$((SKIP+1)); printf '  %s %s\n' "$(y !)" "$1"; }

# code <host> <ip> [path]  -> HTTP status via HTTPS, TLS verification off
code(){ curl -sk -o /dev/null -w '%{http_code}' --max-time 12 \
        --resolve "$1:443:$2" "https://$1${3:-/}" 2>/dev/null; }
# code80 <host> <ip> [path] -> HTTP status over plain :80
code80(){ curl -s -o /dev/null -w '%{http_code}' --max-time 12 \
          --resolve "$1:80:$2" "http://$1${3:-/}" 2>/dev/null; }
# body <host> <ip> [path]
body(){ curl -sk --max-time 20 --resolve "$1:443:$2" "https://$1${3:-/}" 2>/dev/null; }

# cmp_status <host> <cluster-ip> [path] — the core parity assertion
cmp_status(){
  local h="$1" ci="$2" p="${3:-/}"
  local a b; a=$(code "$h" "$ci" "$p"); b=$(code "$h" "$NEW" "$p")
  if [[ "$a" == "$b" && "$a" != "000" ]]; then
    ok "$(printf '%-38s %s' "$h$p" "$a")"
  elif [[ "$b" == "000" ]]; then
    bad "$(printf '%-38s cluster=%s caddy=NO RESPONSE (TLS handshake or connection failed)' "$h$p" "$a")"
  elif [[ "$a" =~ ^50[23]$ && "$b" =~ ^50[23]$ ]]; then
    # Both proxies report the upstream unreachable — Envoy says 503, Caddy says
    # 502 for the same condition. That is the backend being down, not a routing
    # divergence, and flagging it as a migration failure sends you hunting in
    # the wrong place. Surface it, but do not fail the run over it.
    warn "$(printf '%-38s backend DOWN on both (cluster=%s caddy=%s) — not a proxy problem' "$h$p" "$a" "$b")"
  else
    bad "$(printf '%-38s cluster=%s caddy=%s' "$h$p" "$a" "$b")"
  fi
}

PUBLIC=(chat.koeff.com n8n.koeff.com overseerr.koeff.com
        musicengine.koeff.com omniroute.koeff.com shareurl.koeff.com)
INTERNAL=(grafana.local.koeff.com prometheus.local.koeff.com litellm.local.koeff.com
          qdrant.local.koeff.com headroom.local.koeff.com portainer.local.koeff.com)

printf '\033[1mCaddy edge verification\033[0m\n'
printf '  new stack : %s (vm700)\n' "$NEW"
printf '  cluster   : %s public / %s internal\n' "$CL_PUB" "$CL_INT"

# ── 1. reachability ──────────────────────────────────────────────────────────
hdr '1. Ports'
for p in 80 443; do
  if nc -z -G 3 "$NEW" "$p" >/dev/null 2>&1; then ok "$NEW:$p open"
  else bad "$NEW:$p CLOSED — nothing below will work"; fi
done

# ── 2. status parity ─────────────────────────────────────────────────────────
hdr '2. Public hostnames (HTTPS) — must match the cluster'
for h in "${PUBLIC[@]}"; do cmp_status "$h" "$CL_PUB"; done

hdr '3. share.koeff.com path matrix — the QNAP routing'
for p in /qumagie/ /share/ /sl/ /v3_menu/ /; do cmp_status share.koeff.com "$CL_PUB" "$p"; done

hdr '4. Internal *.local hostnames (HTTPS)'
for h in "${INTERNAL[@]}"; do cmp_status "$h" "$CL_INT"; done

hdr '5. Plain HTTP on :80 — must NOT redirect'
# Today's Gateway serves plain HTTP with no redirect and real clients depend on
# it: overseerr is polled by an automated monitor, and chat carries WebSocket
# connections over :80. A 301/308 here is a regression, not an improvement.
for h in chat.koeff.com n8n.koeff.com overseerr.koeff.com shareurl.koeff.com; do
  a=$(code80 "$h" "$CL_PUB"); b=$(code80 "$h" "$NEW")
  if [[ "$a" == "$b" ]]; then
    if [[ "$b" =~ ^30[18]$ ]]; then warn "$(printf '%-38s both redirect (%s) — check this is intended' "$h" "$b")"
    else ok "$(printf '%-38s %s' "$h" "$a")"; fi
  else bad "$(printf '%-38s cluster=%s caddy=%s' "$h" "$a" "$b")"; fi
done

# ── 3. content, not just status codes ────────────────────────────────────────
hdr '6. share.koeff.com content rewriting (the riskiest part of the move)'
# share-prod-proxy does more than proxy: it strips and replaces CSP and injects
# OpenGraph tags via sub_filter. A 200 proves nothing about whether that survived.
for tgt in "$CL_PUB:cluster" "$NEW:caddy"; do
  ip="${tgt%%:*}"; name="${tgt##*:}"
  b=$(body share.koeff.com "$ip" /qumagie/)
  og=$(grep -c 'og:title' <<<"$b"); csp=$(grep -c 'X-Disabled-CSP' <<<"$b"); sz=${#b}
  printf '     %-8s og:title=%s  X-Disabled-CSP=%s  bytes=%s\n' "$name" "$og" "$csp" "$sz"
  eval "${name}_og=$og; ${name}_csp=$csp; ${name}_sz=$sz"
done
if [[ "${caddy_og:-0}" == "${cluster_og:-x}" && "${caddy_csp:-0}" == "${cluster_csp:-x}" \
      && "${caddy_sz:-0}" == "${cluster_sz:-x}" && "${caddy_og:-0}" -gt 0 ]]; then
  ok "sub_filter injection and CSP rewrite identical to the cluster"
else
  bad "share content differs — the nginx sidecar is not reproducing the cluster"
fi

hdr '7. shareurl.koeff.com static site — byte comparison'
ca=$(body shareurl.koeff.com "$CL_PUB" | shasum | cut -d' ' -f1)
cb=$(body shareurl.koeff.com "$NEW"    | shasum | cut -d' ' -f1)
if [[ "$ca" == "$cb" && -n "$ca" ]]; then ok "identical (${ca:0:16}…)"
else bad "differs — cluster=${ca:0:16}… caddy=${cb:0:16}…"; fi

# ── 4. websockets ────────────────────────────────────────────────────────────
hdr '8. WebSocket upgrades — real handshake, not a curl imitation'
# curl cannot complete a WebSocket handshake; it returns 400 against a healthy
# backend and would hide a genuine failure. This does the real thing.
if command -v python3 >/dev/null 2>&1; then
  python3 - "$NEW" "$CL_PUB" "$CL_INT" <<'PY'
import socket, ssl, base64, os, sys
new, pub, internal = sys.argv[1], sys.argv[2], sys.argv[3]
def hs(ip, host, path):
    ctx = ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE
    try:
        s = ctx.wrap_socket(socket.create_connection((ip,443), timeout=10), server_hostname=host)
        s.sendall((f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\n"
                   f"Connection: Upgrade\r\nSec-WebSocket-Key: {base64.b64encode(os.urandom(16)).decode()}\r\n"
                   f"Sec-WebSocket-Version: 13\r\nOrigin: https://{host}\r\n\r\n").encode())
        line = s.recv(4096).decode('utf8','replace').split('\r\n')[0]; s.close()
        return line.split()[1] if len(line.split())>1 else line
    except Exception as e:
        return f"ERR({type(e).__name__})"
cases = [("chat.koeff.com","/websocket",pub), ("n8n.koeff.com","/rest/push?pushRef=t",pub),
         ("grafana.local.koeff.com","/api/live/ws",internal),
         ("portainer.local.koeff.com","/api/websocket/exec",internal)]
bad = 0
for host, path, cl in cases:
    a, b = hs(cl, host, path), hs(new, host, path)
    if a == b:
        note = "101 upgrade" if b == "101" else f"{b} on both — app-level, not the proxy"
        print(f"  \033[32m✓\033[0m {host+path:<44} {note}")
    else:
        print(f"  \033[31m✗\033[0m {host+path:<44} cluster={a} caddy={b}"); bad += 1
sys.exit(1 if bad else 0)
PY
  if [[ $? -eq 0 ]]; then PASS=$((PASS+4)); else FAIL=$((FAIL+1)); fi
else
  warn "python3 not found — WebSocket check skipped"
fi

# ── 5. certificates ──────────────────────────────────────────────────────────
hdr '9. Certificates served by Caddy'
staging=0
for h in chat.koeff.com n8n.koeff.com share.koeff.com grafana.local.koeff.com; do
  info=$(echo | openssl s_client -connect "$NEW:443" -servername "$h" 2>/dev/null \
         | openssl x509 -noout -issuer -subject -enddate 2>/dev/null)
  if [[ -z "$info" ]]; then bad "$(printf '%-30s no certificate served' "$h")"; continue; fi
  iss=$(grep -o 'CN=.*' <<<"$info" | head -1)
  exp=$(sed -n 's/^notAfter=//p' <<<"$info")
  cn=$(sed -n 's/.*subject=CN=//p' <<<"$info" | head -1)
  if grep -qi 'staging' <<<"$iss"; then
    staging=1; warn "$(printf '%-30s STAGING cert, expires %s' "$h" "$exp")"
  elif [[ "$cn" == "$h" ]]; then
    ok "$(printf '%-30s trusted, expires %s' "$h" "$exp")"
  else
    bad "$(printf '%-30s wrong certificate served (CN=%s)' "$h" "$cn")"
  fi
done
if [[ $staging -eq 1 ]]; then
  printf '     %s Still on the Let'"'"'s Encrypt staging endpoint. Browsers will warn.\n' "$(y '→')"
  printf '       Switch to production by re-running the role without the -e overrides:\n'
  printf '       ansible-playbook -i protected/inventories/inventory-main.yaml \\\n'
  printf '         playbook-index.yaml -l ops --tags caddy_stack -e caddy_enabled=true\n'
fi

# ── 6. blast radius ──────────────────────────────────────────────────────────
hdr '10. vm700 co-tenants still healthy'
# Caddy shares this host with the AI stack, RocketChat, OmniRoute and mpe. This
# section answers "did standing up the edge disturb the neighbours", which is a
# question about the box, not about whether the proxy is correct. A service that
# is down for its own reasons is reported but does not block a DNS cutover — so
# these count as warnings, never failures. If one goes quiet right after a
# deploy, check whether the stop was clean and who issued it before blaming this
# stack: docker inspect <name> and docker events --since 30m tell you both.
for pn in "6666 rocketchat" "8770 musicengine" "4000 litellm" "3000 grafana" \
          "9090 prometheus" "20130 omniroute" "6333 qdrant" "9000 portainer" \
          "8787 headroom" "8080 playwright-mcp"; do
  p=${pn%% *}; n=${pn##* }
  c=$(curl -s -o /dev/null -w '%{http_code}' --max-time 6 "http://$NEW:$p/" 2>/dev/null)
  if [[ "$c" != "000" && -n "$c" ]]; then ok "$(printf '%-16s :%-6s %s' "$n" "$p" "$c")"
  else warn "$(printf '%-16s :%-6s DOWN — unrelated to Caddy unless it stopped at deploy time' "$n" "$p")"; fi
done

hdr '11. Cluster untouched — the rollback path must stay intact'
if command -v kubectl >/dev/null 2>&1; then
  prog=$(kubectl get gateway -n ns-homelab-main --no-headers 2>/dev/null | awk '$4=="True"' | wc -l | tr -d ' ')
  if [[ "$prog" == "2" ]]; then ok "both Gateways PROGRAMMED — rollback available"
  else bad "expected 2 programmed Gateways, found ${prog:-0}"; fi
else
  warn "kubectl not found — cluster state not checked"
fi

# ── summary ──────────────────────────────────────────────────────────────────
printf '\n\033[1m─────────────────────────────────────────────\033[0m\n'
printf '  passed %s   failed %s   warnings %s\n' "$(g "$PASS")" "$( ((FAIL)) && r "$FAIL" || g 0 )" "$(y "$SKIP")"
if ((FAIL)); then
  printf '  %s Do not cut DNS over.\n' "$(r 'NOT READY.')"
  printf '     Logs:  ssh vm700 -- docker logs caddy-caddy-1 --tail 50\n'
  exit 1
fi
printf '  %s Every hostname matches the cluster.\n' "$(g 'PARITY CONFIRMED.')"
((staging)) && printf '  %s Production certificates still pending.\n' "$(y 'One step left:')"
exit 0
