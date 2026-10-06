#!/usr/bin/env bash
# Quick local check that the tools the guide uses are installed. It does not touch any cloud resource.
set -u
ok=0
check() { if command -v "$1" >/dev/null 2>&1; then printf "  ok      %s (%s)\n" "$1" "$($1 $2 2>&1 | head -1)"; else printf "  MISSING %s  -> %s\n" "$1" "$3"; ok=1; fi; }

echo "Tooling"
check terraform version  "https://developer.hashicorp.com/terraform/install  (needed for Path B/C infrastructure)"
check databricks --version "https://docs.databricks.com/dev-tools/cli/install  (needed to deploy the bundle)"
check az --version       "https://learn.microsoft.com/cli/azure/install-azure-cli  (needed for Graph site grants)"
check python3 --version  "any 3.10+"

echo
echo "Next: confirm your workspace region supports Unity Gateway, ai_parse_document, Knowledge Assistant and"
echo "Vector Search: https://docs.databricks.com/aws/en/resources/feature-region-support"
exit $ok
