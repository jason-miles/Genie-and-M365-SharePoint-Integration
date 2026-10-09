#!/usr/bin/env bash
# Print the Microsoft Graph site ID for a SharePoint site.
#   ./scripts/get_site_id.sh contoso.sharepoint.com /sites/Policies
# Requires: az CLI logged in as a user who can read the site (az login).
set -euo pipefail

HOST="${1:?usage: get_site_id.sh <tenant>.sharepoint.com </sites/SiteName>}"
SITE_PATH="${2:?usage: get_site_id.sh <tenant>.sharepoint.com </sites/SiteName>}"
case "$SITE_PATH" in /*) ;; *) SITE_PATH="/$SITE_PATH";; esac   # Graph needs the leading slash

az rest --method GET \
  --url "https://graph.microsoft.com/v1.0/sites/${HOST}:${SITE_PATH}" \
  --query "{id:id, name:displayName, webUrl:webUrl}" -o json
