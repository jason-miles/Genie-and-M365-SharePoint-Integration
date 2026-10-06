# Databricks notebook source
# MAGIC %md
# MAGIC # Sync the Vector Search index
# MAGIC Triggers the TRIGGERED Delta Sync index to pick up new / changed / deleted chunks.
# MAGIC Exits cleanly if the index does not exist yet (Knowledge-Assistant-only setups, or before the second terraform apply).
# MAGIC Parameters: `vs_endpoint`, `vs_index`.
# MAGIC
# MAGIC `databricks-vectorsearch` is not preinstalled on serverless; the bundle jobs declare it as an environment dependency
# MAGIC and this notebook also installs it so it works interactively.

# COMMAND ----------

# MAGIC %pip install -q databricks-vectorsearch

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

from databricks.vector_search.client import VectorSearchClient

dbutils.widgets.text("vs_endpoint", "vs-sharepoint-dev")
dbutils.widgets.text("vs_index", "m365.sharepoint_silver.chunks_index")
ENDPOINT = dbutils.widgets.get("vs_endpoint")
INDEX = dbutils.widgets.get("vs_index")

vsc = VectorSearchClient()
try:
    idx = vsc.get_index(endpoint_name=ENDPOINT, index_name=INDEX)
except Exception as e:                      # index not created yet
    dbutils.notebook.exit(f"SKIPPED: index {INDEX} not found on {ENDPOINT} ({type(e).__name__})")

idx.sync()                                  # TRIGGERED pipeline: pick up new / changed / deleted chunks
dbutils.notebook.exit(f"SYNC_TRIGGERED {INDEX}")
