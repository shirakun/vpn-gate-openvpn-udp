#!/usr/bin/env bash
# Validate one shard of the current catalog batch (see docs/adr/0002).
#
# Reads this run's nodes from MySQL table vpn_nodes (batch_id = RUN_ID),
# splits them across SHARD_COUNT parallel runners, and for each node:
#   OpenVPN connect (route-nopull) -> tun0 egress -> in-tunnel unlock tests
#   (Netflix / Abema / Bahamut Anime / OpenAI ChatGPT).
# Results are appended to /tmp/results.tsv, one row per node, 14 TSV columns:
#   IDX CC HOST PROTO IP PORT STATUS EGRESS ELAPSED NF ABEMA BAHAMUT GPT NODE_ID
#
# Env: DB_HOST, DB_USER, DB_NAME, MYSQL_PWD, RUN_ID,
#      SHARD_INDEX, SHARD_COUNT, TIMEOUT_PER_NODE
# Exit: 0 whenever node tests ran to completion. A shard whose nodes are all
# unreachable simply has no usable node right now - that outcome is recorded as
# results and shown in the summary, it never fails the run. Non-zero exits are
# left to genuine operational/script errors (missing tools/env, crash).
set -euo pipefail

SHARD_INDEX="${SHARD_INDEX:-0}"
SHARD_COUNT="${SHARD_COUNT:-1}"
TIMEOUT_PER_NODE="${TIMEOUT_PER_NODE:-25}"

: "${DB_HOST:?set DB_HOST}"
: "${DB_USER:?set DB_USER}"
: "${DB_NAME:?set DB_NAME}"
: "${MYSQL_PWD:?set MYSQL_PWD}"
: "${RUN_ID:?set RUN_ID}"

SUMMARY="${GITHUB_STEP_SUMMARY:-}"

echo "== shard ${SHARD_INDEX}/${SHARD_COUNT}, batch=${RUN_ID}, timeout=${TIMEOUT_PER_NODE}s =="

# --- 1. read current-batch nodes from MySQL --------------------------------
if ! mysql --host="$DB_HOST" --user="$DB_USER" --batch --raw --skip-column-names \
     --default-character-set=utf8mb4 --connect-timeout=20 \
     -e "SELECT id, host, ip, country_short, cfg_proto, cfg_port, config_base64 FROM vpn_nodes WHERE batch_id = ${RUN_ID} AND cfg_port IS NOT NULL AND config_base64 IS NOT NULL ORDER BY id" \
     "$DB_NAME" > /tmp/nodes.tsv 2>/tmp/nodes.err; then
  echo "::error::cannot read nodes from MySQL for batch ${RUN_ID}; skipping shard (DB failures do not fail the run)."
  sed 's/^/    /' /tmp/nodes.err >&2 || true
  exit 0
fi

total_db=$(wc -l < /tmp/nodes.tsv)
echo "current-batch config nodes in DB: ${total_db}"

if [ "${total_db}" -eq 0 ]; then
  echo "No nodes for batch ${RUN_ID} (store step may have failed or DB was empty). Skipping validation."
  if [ -n "$SUMMARY" ]; then
    echo "**Shard ${SHARD_INDEX}: skipped — no nodes in DB for batch ${RUN_ID}.**" >> "$SUMMARY"
  fi
  exit 0
fi

# --- 2. split into this shard's slice and materialize .ovpn files ----------
python3 - "${SHARD_INDEX}" "${SHARD_COUNT}" <<'PY'
import base64
import sys

idx = int(sys.argv[1])
cnt = int(sys.argv[2])
lines = [ln.rstrip('\n') for ln in open('/tmp/nodes.tsv', encoding='utf-8') if ln.strip()]

rows = []
for line in lines[idx::cnt]:
    fields = line.split('\t')
    if len(fields) < 7:
        continue
    node_id, host, ip, cc, proto, port, b64 = fields[:7]
    if not b64 or not port.isdigit():
        continue
    try:
        cfg = base64.b64decode(b64).decode('utf-8', 'replace')
    except Exception as exc:
        print('skip node %s (%s): bad base64: %s' % (node_id, host, exc))
        continue
    rows.append((node_id, cc, host, ip, port, proto, cfg))

with open('/tmp/cands.tsv', 'w', encoding='utf-8') as cands:
    for n, (node_id, cc, host, ip, port, proto, cfg) in enumerate(rows):
        with open('/tmp/vpn_%d.ovpn' % n, 'w', newline='\n') as cfg_file:
            cfg_file.write(cfg)
        cands.write('%d\t%s\t%s\t%s\t%s\t%s\t%s\n' % (n, node_id, cc, host, ip, port, proto))
print('shard candidates: %d' % len(rows))
PY

if [ ! -s /tmp/cands.tsv ]; then
  echo "No candidates for this shard; nothing to validate."
  exit 0
fi

# --- helpers ----------------------------------------------------------------
# unlock-test line parsing: take the first line that starts with the prefix,
# strip the prefix and any leading ':'/space. Input is colour-stripped.
pick() {
  awk -v p="$1" 'index($0,p)==1 { s=substr($0,length(p)+1); sub(/^[[:space:]]*:?[[:space:]]*/,"",s); print s; exit }'
}
# summary display: "YES (Region: X)" shows just X; empty values show '-'
compact() {
  local t="$1" r
  r=$(printf '%s' "$t" | sed -nE 's/^.*\(Region:[[:space:]]*([^)]*)\).*$/\1/p')
  if [ -n "$r" ]; then printf '%s' "$r"; elif [ -n "$t" ]; then printf '%s' "$t"; else printf '%s' '-'; fi
}

# --- 3. validate every candidate in this shard ------------------------------
TOTAL=0
OK=0
FAIL=0
SKIP=0
: > /tmp/results.tsv

while IFS=$'\t' read -r IDX NODE_ID CC HOST IP PORT PROTO; do
  [ -n "$IDX" ] || continue
  OVPN="/tmp/vpn_${IDX}.ovpn"
  TOTAL=$((TOTAL+1))
  NF='-'; ABEMA='-'; BAHAMUT='-'; GPT='-'
  echo "================ [$TOTAL] shard ${SHARD_INDEX}: ${CC} ${HOST} (${PROTO} ${IP}:${PORT}) ================"

  if [ "$PROTO" = "tcp" ]; then
    if timeout 6 nc -z -w 5 "$IP" "$PORT" 2>/dev/null; then
      echo ">>> raw-tcp: OPEN"
    else
      echo ">>> raw-tcp: CLOSED (skip)"
      printf '%s\t%s\t%s\t%s\t%s\t%s\tport_closed\t-\t0\t%s\t%s\t%s\t%s\t%s\n' \
        "$IDX" "$CC" "$HOST" "$PROTO" "$IP" "$PORT" "$NF" "$ABEMA" "$BAHAMUT" "$GPT" "$NODE_ID" >> /tmp/results.tsv
      SKIP=$((SKIP+1))
      continue
    fi
  fi

  LOG="/tmp/vpn_${IDX}.log"
  sudo rm -f "$LOG"
  sed -i '/^[[:space:]]*client[[:space:]]*$/a route-nopull' "$OVPN"
  sudo openvpn --config "$OVPN" --daemon --log "$LOG" \
      --verb 3 --connect-retry 1 --connect-timeout 8 --auth-nocache \
      --user nobody --group nogroup
  sudo chmod 666 "$LOG" 2>/dev/null || true

  START=$SECONDS
  UP=0
  while [ $((SECONDS-START)) -lt "$TIMEOUT_PER_NODE" ]; do
    if grep -q "Initialization Sequence Completed" "$LOG" 2>/dev/null; then UP=1; break; fi
    if ! pgrep -x openvpn >/dev/null 2>&1; then break; fi
    sleep 2
  done
  ELAPSED=$((SECONDS-START))

  EGRESS=""
  if [ "$UP" = "1" ]; then
    EGRESS=$(curl --interface tun0 --max-time 8 -sS https://api64.ipify.org 2>/dev/null || true)
  fi

  if [ -n "$EGRESS" ]; then
    echo ">>> RESULT: OK  egress=${EGRESS}  (${ELAPSED}s)"
    UNLOCK_RAW=$(sudo timeout 90 unlock-test -I tun0 -m 4 \
      -test 'Netflix,Abema,Bahamut Anime,OpenAI ChatGPT' 2>&1 || true)
    UNLOCK_OUT=$(printf '%s\n' "$UNLOCK_RAW" | sed -r $'s/\x1B\[[0-9;]*[mK]//g')
    NF=$(printf '%s\n' "$UNLOCK_OUT" | pick 'Netflix')
    [ -n "$NF" ] || NF='-'
    ABEMA=$(printf '%s\n' "$UNLOCK_OUT" | pick 'Abema')
    [ -n "$ABEMA" ] || ABEMA='-'
    BAHAMUT=$(printf '%s\n' "$UNLOCK_OUT" | pick 'Bahamut Anime')
    [ -n "$BAHAMUT" ] || BAHAMUT='-'
    GPT=$(printf '%s\n' "$UNLOCK_OUT" | pick 'OpenAI ChatGPT')
    if [ -z "$GPT" ]; then GPT=$(printf '%s\n' "$UNLOCK_OUT" | pick 'ChatGPT'); fi
    [ -n "$GPT" ] || GPT='-'
    echo ">>> UNLOCK: Netflix=[$NF] Abema=[$ABEMA] Bahamut=[$BAHAMUT] ChatGPT=[$GPT]"
    printf '%s\t%s\t%s\t%s\t%s\t%s\tok\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$IDX" "$CC" "$HOST" "$PROTO" "$IP" "$PORT" "$EGRESS" "$ELAPSED" "$NF" "$ABEMA" "$BAHAMUT" "$GPT" "$NODE_ID" >> /tmp/results.tsv
    OK=$((OK+1))
  else
    if [ "$UP" = "1" ]; then
      echo ">>> RESULT: FAIL  (init ok but no egress, ${ELAPSED}s)"
      ST="no_egress"
    elif pgrep -x openvpn >/dev/null 2>&1; then
      echo ">>> RESULT: FAIL  (timeout, ${ELAPSED}s)"
      ST="timeout"
    else
      echo ">>> RESULT: FAIL  (openvpn exited, ${ELAPSED}s)"
      ST="died"
    fi
    grep -E 'AUTH_FAILED|TLS Error|Fatal|Options error|Cannot|exiting' "$LOG" 2>/dev/null | tail -n 5 || true
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t-\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$IDX" "$CC" "$HOST" "$PROTO" "$IP" "$PORT" "$ST" "$ELAPSED" "$NF" "$ABEMA" "$BAHAMUT" "$GPT" "$NODE_ID" >> /tmp/results.tsv
    FAIL=$((FAIL+1))
  fi

  sudo pkill -x openvpn 2>/dev/null || true
  sleep 2
done < /tmp/cands.tsv

echo "================ shard ${SHARD_INDEX} summary ================"
echo "shard_total=${TOTAL} ok=${OK} fail=${FAIL} skipped=${SKIP}"

if [ -n "$SUMMARY" ]; then
  {
    echo "## Shard ${SHARD_INDEX} 验证结果 (batch=${RUN_ID})"
    echo
    echo "| # | 国家 | 主机 | 协议 | 服务器:端口 | 结果 | 出口IP | Netflix | Abema | 動畫瘋 | ChatGPT | 耗时(s) |"
    echo "|---|---|---|---|---|---|---|---|---|---|---|---|"
    while IFS=$'\t' read -r N CC HOST PROTO IP PORT ST EGRESS EL NF ABEMA BAHAMUT GPT NODE_ID; do
      case "$ST" in
        ok) EMOJI=":white_check_mark:" ;;
        port_closed) EMOJI=":no_entry_sign:" ;;
        *) EMOJI=":x:" ;;
      esac
      printf '| %s | %s | %s | %s | %s:%s | %s %s | %s | %s | %s | %s | %s | %s |\n' \
        "$N" "$CC" "$HOST" "$PROTO" "$IP" "$PORT" "$EMOJI" "$ST" "$EGRESS" "$(compact "$NF")" "$(compact "$ABEMA")" "$(compact "$BAHAMUT")" "$(compact "$GPT")" "$EL"
    done < /tmp/results.tsv
    echo
    echo "**shard total=${TOTAL}, ok=${OK}, fail=${FAIL}, skipped=${SKIP}**"
    if [ "${TOTAL}" -gt 0 ] && [ "${OK}" -eq 0 ]; then
      echo "**本 shard 无可用节点(全部失败)— 已记录结果,不视为运行失败。**"
    fi
  } >> "$SUMMARY"
fi

# No usable node is a result, not an error: an all-failed shard means this batch
# has no reachable node right now, which is recorded (and shown above) instead
# of failing the run. Unexpected operational errors still abort via `set -e`.
if [ "$TOTAL" -gt 0 ] && [ "$OK" -eq 0 ]; then
  echo "No usable node in this shard (total=${TOTAL}, ok=0, fail=${FAIL}, skipped=${SKIP}); recorded as results."
fi
exit 0
