# Secrets, Unity Catalog objects and grants.
# The SharePoint Unity Catalog CONNECTION is created once in Catalog Explorer (the provider's
# databricks_connection resource does not list a SharePoint type). See README, Path B step 4.

resource "databricks_secret_scope" "m365" {
  name = "m365-sharepoint"
}

resource "databricks_secret" "tenant_id" {
  scope        = databricks_secret_scope.m365.name
  key          = "tenant_id"
  string_value = var.tenant_id
}

resource "databricks_secret" "client_id" {
  scope        = databricks_secret_scope.m365.name
  key          = "client_id"
  string_value = azuread_application.sp_ingest.client_id
}

resource "databricks_secret" "client_secret" {
  scope        = databricks_secret_scope.m365.name
  key          = "client_secret"
  string_value = azuread_application_password.sp_ingest.value
}

resource "databricks_secret_acl" "m365_readers" {
  scope      = databricks_secret_scope.m365.name
  principal  = var.engineer_group
  permission = "READ"
}

resource "databricks_catalog" "m365" {
  name = var.catalog_name
}

resource "databricks_schema" "bronze" {
  catalog_name = databricks_catalog.m365.name
  name         = "sharepoint_bronze"
}

resource "databricks_schema" "silver" {
  catalog_name = databricks_catalog.m365.name
  name         = "sharepoint_silver"
}

# Landing area for files downloaded by the custom Graph pipeline (Path C)
resource "databricks_volume" "raw" {
  catalog_name = databricks_catalog.m365.name
  schema_name  = databricks_schema.bronze.name
  name         = "raw_files"
  volume_type  = "MANAGED"
}

# Principals need USE_CATALOG as well as schema privileges
resource "databricks_grants" "catalog" {
  catalog = databricks_catalog.m365.name

  grant {
    principal  = var.engineer_group
    privileges = ["USE_CATALOG"]
  }
  grant {
    principal  = var.consumer_group
    privileges = ["USE_CATALOG"]
  }
}

resource "databricks_grants" "bronze" {
  schema = "${databricks_catalog.m365.name}.${databricks_schema.bronze.name}"

  grant {
    principal  = var.engineer_group
    privileges = ["USE_SCHEMA", "SELECT", "MODIFY", "CREATE_TABLE", "READ_VOLUME", "WRITE_VOLUME"]
  }
}

resource "databricks_grants" "silver" {
  schema = "${databricks_catalog.m365.name}.${databricks_schema.silver.name}"

  grant {
    principal  = var.engineer_group
    privileges = ["USE_SCHEMA", "SELECT", "MODIFY", "CREATE_TABLE"]
  }
  grant {
    principal  = var.consumer_group
    privileges = ["USE_SCHEMA", "SELECT"]
  }
}
