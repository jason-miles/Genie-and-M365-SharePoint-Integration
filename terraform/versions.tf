terraform {
  required_version = ">= 1.5"

  required_providers {
    databricks = { source = "databricks/databricks" }
    azuread    = { source = "hashicorp/azuread" }
  }
}

# Databricks auth comes from the environment (DATABRICKS_HOST / OAuth M2M), not from this file.
provider "databricks" {
  host = var.databricks_host
}

provider "azuread" {
  tenant_id = var.tenant_id
}
