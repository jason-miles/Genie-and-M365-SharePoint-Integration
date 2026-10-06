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

variable "consumer_group" {
  description = "Databricks group that may read the curated (silver) tables"
  type        = string
  default     = "grp-rag-consumers"
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
