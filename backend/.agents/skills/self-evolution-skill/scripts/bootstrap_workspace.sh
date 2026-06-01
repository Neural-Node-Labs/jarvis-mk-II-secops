#!/usr/bin/env bash
# version: 1.0.0
# changelog:
#   1.0.0 - 2026-06-01 - Bootstrap script for evolution workspace (Phase 1)
#
# Usage:
#   bash bootstrap_workspace.sh <slug> <origin_path>
#   Example: bash bootstrap_workspace.sh "upgrade-auth-handler" "/app/skills/auth"

set -euo pipefail

SLUG="${1:-unnamed-evolution}"
ORIGIN_PATH="${2:-}"
TIMESTAMP=$(date -u +"%Y%m%d-%H%M")
WORKSPACE_ID="ws-${TIMESTAMP}-${SLUG}"
WORKSPACE_ROOT="/tmp/evo/${WORKSPACE_ID}"
LOG_TIME=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

echo "──────────────────────────────────────────"
echo " Self-Evolution Skill — Phase 1 Bootstrap"
echo "──────────────────────────────────────────"
echo " Workspace ID : ${WORKSPACE_ID}"
echo " Origin       : ${ORIGIN_PATH:-<not specified>}"
echo " Created at   : ${LOG_TIME}"
echo ""

# Create directory tree
mkdir -p \
  "${WORKSPACE_ROOT}/src" \
  "${WORKSPACE_ROOT}/tests" \
  "${WORKSPACE_ROOT}/reports/backup" \
  "${WORKSPACE_ROOT}/experienced"

# Initialize evolution.log
cat > "${WORKSPACE_ROOT}/evolution.log" << EOF
[${LOG_TIME}] EVOLUTION SESSION STARTED
Workspace : ${WORKSPACE_ROOT}
Task      : ${SLUG}
Origin    : ${ORIGIN_PATH:-<not specified>}
Phase     : 0 (Experience Lookup pending)

EOF

# Initialize experienced/index.md
cat > "${WORKSPACE_ROOT}/experienced/index.md" << EOF
# Experience Index
<!-- version: 1.0.0 -->

| EXP-ID | Category | Title | Status | Date |
|--------|----------|-------|--------|------|
EOF

echo "[${LOG_TIME}] [PHASE-1] WORKSPACE_CREATED  id=${WORKSPACE_ID}" >> "${WORKSPACE_ROOT}/evolution.log"

echo "✓ Workspace created: ${WORKSPACE_ROOT}"
echo ""
echo "Directory structure:"
find "${WORKSPACE_ROOT}" -type d | sed 's|'"${WORKSPACE_ROOT}"'|  .|'

# Export for use by subsequent scripts
echo ""
echo "WORKSPACE_ID=${WORKSPACE_ID}"
echo "WORKSPACE_ROOT=${WORKSPACE_ROOT}"
echo ""
echo "To source these variables:"
echo "  eval \$(bash bootstrap_workspace.sh \"${SLUG}\" \"${ORIGIN_PATH}\")"
