output "entra_client_id" {
  description = "Application (client) ID. Use it with scripts/grant_site_access.sh and in the UC connection."
  value       = azuread_application.sp_ingest.client_id
}

output "entra_tenant_id" {
  value = var.tenant_id
}

output "secret_scope" {
  value = databricks_secret_scope.m365.name
}

output "vector_search_endpoint" {
  value = databricks_vector_search_endpoint.rag.name
}
