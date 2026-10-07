# Entra ID app for Paths B (Lakeflow Connect, OAuth M2M) and C (custom Graph pipeline).
# Least privilege: Sites.Selected. Per-site access is granted separately by scripts/grant_site_access.sh,
# run by an ADMIN identity. The app cannot grant itself access to any site.

data "azuread_application_published_app_ids" "well_known" {}

data "azuread_service_principal" "msgraph" {
  client_id = data.azuread_application_published_app_ids.well_known.result["MicrosoftGraph"]
}

resource "azuread_application" "sp_ingest" {
  display_name = "Databricks SharePoint Ingestion (${var.env})"

  required_resource_access {
    resource_app_id = data.azuread_service_principal.msgraph.client_id

    resource_access {
      id   = data.azuread_service_principal.msgraph.app_role_ids["Sites.Selected"]
      type = "Role"
    }
  }
}

resource "azuread_service_principal" "sp_ingest" {
  client_id = azuread_application.sp_ingest.client_id
}

resource "azuread_application_password" "sp_ingest" {
  application_id = azuread_application.sp_ingest.id
  display_name   = "databricks"
  end_date       = timeadd(timestamp(), "${var.secret_lifetime_hours}h")

  lifecycle {
    # timestamp() changes on every plan; without this the secret would be rotated on every apply.
    # To rotate deliberately: terraform apply -replace=azuread_application_password.sp_ingest
    ignore_changes = [end_date]
  }
}

# Admin consent for the Sites.Selected application permission
resource "azuread_app_role_assignment" "sites_selected" {
  app_role_id         = data.azuread_service_principal.msgraph.app_role_ids["Sites.Selected"]
  principal_object_id = azuread_service_principal.sp_ingest.object_id
  resource_object_id  = data.azuread_service_principal.msgraph.object_id
}
