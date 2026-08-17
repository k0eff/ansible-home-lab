#!/usr/bin/env bash
#
# switchover.sh — move live traffic from the cluster00 ingress to the Caddy
# stack on vm700, and move it back.
#
#   ./switchover.sh plan       # what would change; touches nothing (default)
#   ./switchover.sh apply      # perform the DNS half of the switchover
#   ./switchover.sh rollback   # put DNS back on the cluster
#   ./switchover.sh status     # where each hostname currently points
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
  plan|apply|rollback|status) ;;
  -h|--help) sed -n '2,45p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) die "unknown mode '$MODE' (use plan, apply, rollback or status)" ;;
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

  plan)
    show_status
    hdr 'What "apply" would do'
    printf '  %s Six A records %s -> %s:\n' "$(b '1.')" "$CLUSTER_INTERNAL" "$CADDY_IP"
    for h in "${LOCAL_HOSTS[@]}"; do printf '       %s\n' "$h"; done
    printf '\n  %s Router port-forward :80/:443 %s -> %s\n' "$(b '2.')" "$CLUSTER_PUBLIC" "$CADDY_IP"
    printf '       Manual. This script cannot reach the router and will stop and ask.\n'
    printf '\n  Not touched: the seven public A records (they point at the WAN address),\n'
    printf '  shareurl.local.koeff.com (orphan — no route serves it, 000 from every target),\n'
    printf '  and the cluster itself, which keeps running as the rollback path.\n'
    printf '\n  %s\n' "$(y 'Nothing has been changed. Run "apply" when you want it to happen.')"
    ;;

  apply)
    preflight
    hdr 'Phase 1 — internal hostnames'
    printf '  Lower stakes and reversible: these are only reachable from the LAN.\n'
    confirm "Repoint six *.local records from the cluster to Caddy?" "yes"
    fail=0
    for h in "${LOCAL_HOSTS[@]}"; do set_record "$h" "$CADDY_IP" || fail=1; done
    ((fail)) && die "at least one record did not update — fix it or run rollback"
    printf '\n  %s Propagation takes up to ~5 minutes (TTL auto = 300s).\n' "$(y '→')"
    printf '  Check with:  dig +short @1.1.1.1 grafana.local.koeff.com\n'

    hdr 'Phase 2 — public traffic (manual)'
    cat <<EOF
  On the router, the rule that forwards :80 and :443 must change its target:

        from   $CLUSTER_PUBLIC   (cluster Envoy)
        to     $CADDY_IP   (Caddy on vm700)

  Nothing else about the rule changes — same ports, same protocol, same WAN side.
EOF
    confirm "Have you changed the router forward to $CADDY_IP?" "done"

    hdr 'Post-switchover verification'
    sleep 5
    ok=0; bad=0
    for h in chat.koeff.com n8n.koeff.com share.koeff.com shareurl.koeff.com \
             overseerr.koeff.com musicengine.koeff.com omniroute.koeff.com; do
      code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 12 "https://$h/" 2>/dev/null)
      who=$(curl -s -I --max-time 12 "https://$h/" 2>/dev/null | grep -i '^server:' | tr -d '\r')
      if [[ "$code" != "000" ]]; then
        printf '  %s %-26s %s  %s\n' "$(g ✓)" "$h" "$code" "$who"; ok=$((ok+1))
      else
        printf '  %s %-26s no response\n' "$(r ✗)" "$h"; bad=$((bad+1))
      fi
    done
    printf '\n'
    if ((bad)); then
      printf '  %s %s of %s public hostnames are not answering.\n' "$(r 'PROBLEM.')" "$bad" "$((ok+bad))"
      printf '  Roll back now:  %s rollback   (then revert the router to %s)\n' "$0" "$CLUSTER_PUBLIC"
      exit 1
    fi
    printf '  %s All public hostnames answering through the new edge.\n' "$(g 'SWITCHED OVER.')"
    printf '  Certificates served here are the ones Caddy obtained; the browser check is\n'
    printf '  simply visiting https://chat.koeff.com/ with no warning.\n\n'
    printf '  %s Leave cluster00 running until Caddy has completed at least one\n' "$(y 'Do not decommission yet:')"
    printf '     certificate renewal. Rollback stays free until you do.\n'
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
