variable "databricks_host" {
  description = "Workspace URL, for example https://adb-123.4.azuredatabricks.net or https://<name>.cloud.databricks.com"
  type        = string
}

variable "tenant_id" {
  description = "Microsoft Entra directory (tenant) ID"
  type        = string
}

variable "env" {
  description = "Environment name used in resource names (dev, test, prod)"
  type        = string
  default     = "dev"
}

variable "catalog_name" {
  description = "Unity Catalog catalog that holds the SharePoint data"
  type        = string
  default     = "m365"
}

variable "engineer_group" {
  description = "Databricks group that builds and operates the pipeline"
  type        = string
  default     = "grp-rag-engineers"
}

variable "agent_principal" {
  description = <<-EOT
    Service principal (application ID) or group that runs your agent / app and may read the silver tables and query the index.
    End users must NOT get SELECT on the silver tables or query access on the index: secure_search() only protects callers who go through it.
    Leave empty to grant nothing.
  EOT
  type        = string
  default     = ""
}

variable "secret_reader" {
  description = "Principal allowed to READ the Entra client secret in the secret scope. Defaults to engineer_group; in production set it to the run-as service principal only (anyone with READ can print the secret from a notebook)."
  type        = string
  default     = null
}

variable "secret_lifetime_hours" {
  description = "Lifetime of the Entra client secret. 4320 h = 180 days. Rotate before expiry."
  type        = number
  default     = 4320
}

variable "create_vector_index" {
  description = <<-EOT
    Create the Delta Sync Vector Search index. Leave false on the first apply: the index needs the
    silver chunks table (with Change Data Feed) to exist, and that table is created by the first
    run of the parse job. Set true and apply again afterwards. Not needed if you only use Knowledge Assistant.
  EOT
  type        = bool
  default     = false
}

variable "embedding_model_endpoint" {
  description = "Embedding model serving endpoint used by the Vector Search index"
  type        = string
  default     = "databricks-gte-large-en"
}
