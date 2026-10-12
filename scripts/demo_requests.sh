#!/usr/bin/env bash
# Reproduce the two agentic demo tasks against a running app (local or deployed) with curl.
#   ./scripts/demo_requests.sh                                   # http://localhost:8000
#   ./scripts/demo_requests.sh https://<your-app>.onrender.com   # deployed (first call may take ~1 min: cold start)
set -euo pipefail
BASE="${1:-http://localhost:8000}"
pretty() { python3 -c 'import json,sys; r=json.load(sys.stdin); print(r["answer"]); print("\nMCP tools called:", " -> ".join(r["tools_called"])); print("Citations:", ", ".join(c["citation"] for c in r["citations"])); print("Pending action:", (r.get("pending_action") or {}).get("action_id")); print("Session:", r["session_id"])'; }

echo "== Health"; curl -fsS "$BASE/health"; echo; echo

echo "== Demo 1: remote work eligibility (E1003, Portugal, six weeks)"
R1=$(curl -fsS -X POST "$BASE/chat" -H 'Content-Type: application/json' \
  -d '{"message":"Can I work remotely from Portugal for six weeks this summer?","employee_id":"E1003"}')
echo "$R1" | pretty
ACTION=$(echo "$R1" | python3 -c 'import json,sys; print((json.load(sys.stdin).get("pending_action") or {}).get("action_id",""))')
SESSION=$(echo "$R1" | python3 -c 'import json,sys; print(json.load(sys.stdin)["session_id"])')
if [ -n "$ACTION" ]; then
  echo; echo "== Demo 1b: confirm the mock Global Mobility ticket"
  curl -fsS -X POST "$BASE/chat" -H 'Content-Type: application/json' \
    -d "{\"message\":\"\",\"session_id\":\"$SESSION\",\"confirm_action_id\":\"$ACTION\"}" | pretty
fi

echo; echo "== Demo 2: PTO request guidance (E1002, 3 days next week, draft to manager)"
curl -fsS -X POST "$BASE/chat" -H 'Content-Type: application/json' \
  -d '{"message":"Can I take 3 days of PTO next week? Please draft a note to my manager.","employee_id":"E1002"}' | pretty
