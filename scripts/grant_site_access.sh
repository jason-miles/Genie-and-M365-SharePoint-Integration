#!/usr/bin/env bash
# Grant the ingestion app READ access to one SharePoint site (needed because the app only holds Sites.Selected).
#   ./scripts/grant_site_access.sh <site-id> <client-id> ["Display name"]
#
# Run as an ADMIN identity that is allowed to grant site permissions (for example an app or user holding
# Sites.FullControl.All, used once). The ingestion app cannot grant itself access.
# Keep the list of granted sites in version control and review it quarterly.
set -euo pipefail

SITE_ID="${1:?usage: grant_site_access.sh <site-id> <client-id> [display-name]}"
CLIENT_ID="${2:?usage: grant_site_access.sh <site-id> <client-id> [display-name]}"
NAME="${3:-Databricks SharePoint Ingestion}"

BODY=$(cat <<EOF
{"roles":["read"],"grantedToIdentities":[{"application":{"id":"${CLIENT_ID}","displayName":"${NAME}"}}]}
EOF
)

az rest --method POST \
  --url "https://graph.microsoft.com/v1.0/sites/${SITE_ID}/permissions" \
  --headers "Content-Type=application/json" \
  --body "${BODY}"
echo "Granted read on ${SITE_ID} to ${CLIENT_ID}"
