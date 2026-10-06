# Vector Search endpoint and Delta Sync index (only for the custom-RAG variant; skip if you use
# Agent Bricks Knowledge Assistant alone).

resource "databricks_vector_search_endpoint" "rag" {
  name          = "vs-sharepoint-${var.env}"
  endpoint_type = "STANDARD"
}

# Created only when var.create_vector_index = true, i.e. after the parse job has produced the
# chunks table with Change Data Feed enabled.
resource "databricks_vector_search_index" "chunks" {
  count = var.create_vector_index ? 1 : 0

  name          = "${databricks_catalog.m365.name}.${databricks_schema.silver.name}.chunks_index"
  endpoint_name = databricks_vector_search_endpoint.rag.name
  primary_key   = "chunk_id"
  index_type    = "DELTA_SYNC"

  delta_sync_index_spec {
    source_table  = "${databricks_catalog.m365.name}.${databricks_schema.silver.name}.chunks"
    pipeline_type = "TRIGGERED"

    embedding_source_columns {
      name                          = "chunk_text"
      embedding_model_endpoint_name = var.embedding_model_endpoint
    }
  }
}
