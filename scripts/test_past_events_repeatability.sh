#!/bin/bash
# Test repeatability of search_past_events responses.
# Usage:
#   export KG_TOKEN=<bearer token>
#   export KG_BASE=http://localhost:8030/v1/kg-agents
#   export INSTANCE_ID=<irc5 instance id>
#   ./test_past_events_repeatability.sh
set -e

KG_BASE="${KG_BASE:-http://localhost:8030/v1/kg-agents}"
INSTANCE_ID="${INSTANCE_ID:?Set INSTANCE_ID to the IRC5 instance id}"
KG_TOKEN="${KG_TOKEN:?Set KG_TOKEN to a valid bearer token}"

extract_fields() {
  local json="$1"
  local occ
  local narr_len
  local top_sig
  occ=$(echo "$json" | jq -r '.past_cases_summary.occurrence_count // 0')
  narr_len=$(echo "$json" | jq -r '(.past_cases_summary.narrative_summary // "") | length')
  top_sig=$(echo "$json" | jq -r '.past_cases_summary.top_event_signature_id // "null"')
  echo "$occ|$narr_len|$top_sig"
}

declare -a QUESTIONS=(
  "encoder errors last 7 days"
  "thermal events on axis_2 past month"
  "drive motor overspeed how was it fixed before"
  "packet loss on Ethernet"
  "show me past cases of severity critical from last year"
  "which past events were escalated"
)

declare -a RUN1
declare -a RUN2

run_batch() {
  local label="$1"
  local -n out=$2
  echo "=== $label ==="
  for i in "${!QUESTIONS[@]}"; do
    q="${QUESTIONS[$i]}"
    resp=$(curl -sS -X POST \
      "$KG_BASE/instances/$INSTANCE_ID/chat" \
      -H "Authorization: Bearer $KG_TOKEN" \
      -H "Content-Type: application/json" \
      -d "$(jq -nc --arg m "$q" '{message:$m, behavior_mode:"search_past_events"}')")
    fields=$(extract_fields "$resp")
    out[$i]="$fields"
    printf "Q%d: %-60s => %s\n" "$i" "$q" "$fields"
  done
}

run_batch "Run 1" RUN1
sleep 3
run_batch "Run 2" RUN2

echo ""
echo "=== Diff (Run1 vs Run2) ==="
for i in "${!QUESTIONS[@]}"; do
  q="${QUESTIONS[$i]}"
  if [[ "${RUN1[$i]}" != "${RUN2[$i]}" ]]; then
    echo "MISMATCH Q$i: $q"
    echo "  Run1: ${RUN1[$i]}"
    echo "  Run2: ${RUN2[$i]}"
  else
    echo "OK       Q$i: $q"
  fi
done
