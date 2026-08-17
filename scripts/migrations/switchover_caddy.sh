#!/usr/bin/env bash
#
# switchover_caddy.sh — move live traffic from the cluster00 ingress to the
# Caddy stack on vm700, and move it back.
#
#   ./switchover_caddy.sh plan           what would change; touches nothing (default)
#   ./switchover_caddy.sh status         where each hostname points right now
#   ./switchover_caddy.sh apply          both halves, interactive
#   ./switchover_caddy.sh apply-dns      the six *.local records only
#   ./switchover_caddy.sh verify-public  did the router move yet
#   ./switchover_caddy.sh rollback       put DNS back on the cluster
#
# Requires CERT_MANAGER_CLOUDFLARE_TOKEN in the environment — the same token
# cert-manager and Caddy already use:
#     cd helmfile-home-lab && . ./protected/main.sh cluster00
#
# ── What a switchover actually consists of ──────────────────────────────────
#
# TWO changes, and only one of them is scriptable.
#
#   1. Six *.local.koeff.com A records: 192.168.31.237 -> 192.168.31.152
#      These are public Cloudflare records holding a private IP. This script
#      does them.
#
#   2. The router's :80/:443 port-forward: 192.168.31.235 -> 192.168.31.152
#      This script CANNOT do this and will not pretend to. It prints the change
#      and waits for you to confirm you have made it.
#
# The seven public hostnames need NO DNS change: they already resolve to the WAN
# address, and it is the router that decides which internal host that lands on.
#
# ── Why this is low-risk ────────────────────────────────────────────────────
#
# The cluster keeps running throughout and keeps its own certificates renewed.
# Rollback is repointing at it — no rebuild, no restore. Cloudflare TTL on these
# records is "auto" (300s for DNS-only), so both directions take effect within
# about five minutes.

set -uo pipefail

CADDY_IP="192.168.31.152"
CLUSTER_INTERNAL="192.168.31.237"   # Envoy koeff-public-v2, serves *.local today
CLUSTER_PUBLIC="192.168.31.235"     # Envoy koeff-public, current router target
ZONE="koeff.com"
API="https://api.cloudflare.com/client/v4"

# The six records this script owns. Everything else in the zone is left alone.
LOCAL_HOSTS=(
  grafana.local.koeff.com
  prometheus.local.koeff.com
  litellm.local.koeff.com
  qdrant.local.koeff.com
  headroom.local.koeff.com
  portainer.local.koeff.com
)

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERIFY="$HERE/verify_caddy.sh"   # sibling in scripts/migrations/

g(){ printf '\033[32m%s\033[0m' "$1"; }
r(){ printf '\033[31m%s\033[0m' "$1"; }
y(){ printf '\033[33m%s\033[0m' "$1"; }
b(){ printf '\033[1m%s\033[0m' "$1"; }
hdr(){ printf '\n\033[1m%s\033[0m\n' "$1"; }
die(){ printf '\n%s %s\n' "$(r 'ABORT:')" "$1"; exit 1; }

MODE="${1:-plan}"
case "$MODE" in
  plan|apply|apply-dns|verify-public|rollback|status) ;;
  -h|--help) sed -n '2,45p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) die "unknown mode '$MODE' (plan, status, apply, apply-dns, verify-public, rollback)" ;;
esac

[[ -n "${CERT_MANAGER_CLOUDFLARE_TOKEN:-}" ]] || die \
  "CERT_MANAGER_CLOUDFLARE_TOKEN is not set.
     cd /Users/krasi/koeff-gitroot-main/helmfile-home-lab && . ./protected/main.sh cluster00
     then run this script from the SAME shell."
TOKEN="$CERT_MANAGER_CLOUDFLARE_TOKEN"

cf(){ curl -s --max-time 20 -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" "$@"; }

ZID=$(cf "$API/zones?name=$ZONE" | python3 -c \
  "import json,sys; res=json.load(sys.stdin).get('result') or []; print(res[0]['id'] if res else '')")
[[ -n "$ZID" ]] || die "could not resolve the Cloudflare zone id for $ZONE — is the token still valid?"

# record_id <fqdn>  /  record_ip <fqdn>
_rec(){ cf "$API/zones/$ZID/dns_records?type=A&name=$1" | python3 -c \
  "import json,sys; res=json.load(sys.stdin).get('result') or []; print(res[0]['$2'] if res else '')"; }
record_id(){ _rec "$1" id; }
record_ip(){ _rec "$1" content; }

set_record(){ # set_record <fqdn> <ip>
  local id; id=$(record_id "$1")
  [[ -n "$id" ]] || { printf '  %s %s — no A record found, skipped\n' "$(r ✗)" "$1"; return 1; }
  local out; out=$(cf -X PATCH "$API/zones/$ZID/dns_records/$id" --data "{\"content\":\"$2\"}")
  if python3 -c "import json,sys; sys.exit(0 if json.loads('''$out''')['success'] else 1)" 2>/dev/null; then
    printf '  %s %-32s -> %s\n' "$(g ✓)" "$1" "$2"; return 0
  fi
  printf '  %s %-32s FAILED: %s\n' "$(r ✗)" "$1" "$(echo "$out" | head -c 200)"; return 1
}

show_status(){
  hdr 'Where each hostname points right now'
  printf '  %-34s %-18s %s\n' 'RECORD' 'VALUE' 'MEANING'
  for h in "${LOCAL_HOSTS[@]}"; do
    ip=$(record_ip "$h")
    case "$ip" in
      "$CADDY_IP")         m="$(g 'Caddy (vm700)')" ;;
      "$CLUSTER_INTERNAL") m="$(y 'cluster')" ;;
      *)                   m="$(r "unexpected")" ;;
    esac
    printf '  %-34s %-18s %s\n' "$h" "$ip" "$m"
  done
  hdr 'Public hostnames — no DNS change involved'
  printf '  All seven resolve to the WAN address; the router decides where that lands.\n'
  printf '  Live answer for chat.koeff.com right now: '
  srv=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 8 https://chat.koeff.com/ 2>/dev/null)
  # Envoy and Caddy both answer 200; the Server header is what distinguishes them.
  who=$(curl -sk -I --max-time 8 https://chat.koeff.com/ 2>/dev/null | grep -i '^server:' | tr -d '\r' | cut -d' ' -f2-)
  printf 'HTTP %s, Server: %s\n' "$srv" "${who:-<none>}"
  printf '  %s router still forwards to the cluster if that Server header is absent;\n' "$(y '→')"
  printf '     Caddy identifies itself as "Caddy".\n'
}

preflight(){
  hdr 'Pre-flight — the new stack must already be at full parity'
  [[ -x "$VERIFY" ]] || die "scripts/migrations/verify_caddy.sh not found next to this script"
  if "$VERIFY" >/tmp/_switchover_verify.log 2>&1; then
    # scripts/migrations/verify_caddy.sh colours its counters, so the digits are wrapped in escape
    # sequences — strip them before reading the number back out.
    summary=$(sed 's/\x1b\[[0-9;]*m//g' /tmp/_switchover_verify.log | grep -oE 'passed [0-9]+ +failed [0-9]+' | tail -1)
    printf '  %s scripts/migrations/verify_caddy.sh passed (%s)\n' "$(g ✓)" "${summary:-see /tmp/_switchover_verify.log}"
  else
    printf '  %s scripts/migrations/verify_caddy.sh FAILED:\n' "$(r ✗)"
    grep -E '✗' /tmp/_switchover_verify.log | sed 's/^/     /'
    die "not switching over a stack that is not at parity. Full log: /tmp/_switchover_verify.log"
  fi
}

confirm(){ # confirm <prompt> <expected-word>
  printf '\n  %s %s\n  Type %s to continue, anything else to stop: ' "$(y '?')" "$1" "$(b "$2")"
  read -r ans
  [[ "$ans" == "$2" ]] || die "cancelled — nothing was changed"
}

case "$MODE" in

  status) show_status ;;

  # The two halves run separately because only one of them is scriptable. The
  # router change needs a person, and this script will not accept a typed
  # confirmation on their behalf.
  apply-dns)
    preflight
    hdr 'Internal hostnames — the scriptable half'
    fail=0
    for h in "${LOCAL_HOSTS[@]}"; do set_record "$h" "$CADDY_IP" || fail=1; done
    ((fail)) && die "at least one record did not update — fix it or run rollback"
    printf '\n  %s Propagation takes up to ~5 minutes (TTL auto = 300s).\n' "$(y '→')"
    printf '  Then:  %s verify-public   after the router forward is moved.\n' "$0"
    ;;

  verify-public)
    hdr 'Public hostnames — which stack actually answers'
    # The discriminator is the TLS certificate serial. An earlier version of this
    # compared the Server header, which proved worthless: most backends set their
    # own (nginx on the QNAP, granian for musicengine) and both proxies pass it
    # through unchanged, so it reported "still on the cluster" for hostnames that
    # had already moved. Caddy and cert-manager each obtained their own
    # certificate for every name, so the serials differ and cannot be confused.
    serial(){ echo | openssl s_client -connect "$1" -servername "$2" 2>/dev/null \
              | openssl x509 -noout -serial 2>/dev/null | cut -d= -f2; }
    ok=0; oncluster=0; bad=0
    for h in chat.koeff.com n8n.koeff.com overseerr.koeff.com musicengine.koeff.com \
             omniroute.koeff.com share.koeff.com shareurl.koeff.com; do
      pub=$(serial "$h:443" "$h")
      cad=$(serial "$CADDY_IP:443" "$h")
      clu=$(serial "$CLUSTER_PUBLIC:443" "$h")
      code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 12 "https://$h/" 2>/dev/null)
      if [[ -z "$pub" ]]; then
        printf '  %s %-24s no TLS response\n' "$(r ✗)" "$h"; bad=$((bad+1))
      elif [[ "$pub" == "$cad" ]]; then
        printf '  %s %-24s %s  Caddy\n' "$(g ✓)" "$h" "$code"; ok=$((ok+1))
      elif [[ "$pub" == "$clu" ]]; then
        printf '  %s %-24s %s  still on the cluster\n' "$(y '!')" "$h" "$code"; oncluster=$((oncluster+1))
      else
        printf '  %s %-24s %s  unknown certificate\n' "$(r ✗)" "$h" "$code"; bad=$((bad+1))
      fi
    done
    printf '\n'
    if ((bad)); then
      printf '  %s %s hostname(s) broken — roll back.\n' "$(r 'PROBLEM.')" "$bad"; exit 1
    elif ((oncluster)); then
      printf '  %s %s of 7 still served by the cluster — the router forward has not taken effect.\n' \
        "$(y 'PARTIAL.')" "$oncluster"; exit 1
    fi
    printf '  %s All seven public hostnames served by Caddy.\n' "$(g 'SWITCHED OVER.')"
    ;;

  rollback)
    hdr 'Rollback — point everything back at the cluster'
    printf '  The cluster was never stopped, so this is a repoint, not a restore.\n'
    confirm "Repoint six *.local records back to the cluster ($CLUSTER_INTERNAL)?" "yes"
    for h in "${LOCAL_HOSTS[@]}"; do set_record "$h" "$CLUSTER_INTERNAL"; done
    hdr 'Router — manual, do this now'
    printf '  Change the :80/:443 forward target back:  %s -> %s\n' "$CADDY_IP" "$CLUSTER_PUBLIC"
    printf '\n  %s Public traffic keeps hitting Caddy until you do.\n' "$(y '→')"
    ;;
esac
