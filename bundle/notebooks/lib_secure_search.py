# Databricks notebook source
# MAGIC %md
# MAGIC # Security-trimmed retrieval helpers
# MAGIC `%run ./lib_secure_search` from your agent or app notebook (after `%pip install databricks-vectorsearch` and a Python restart),
# MAGIC then call `secure_search(question, groups)`.
# MAGIC Parameters: `catalog`, `vs_endpoint`, `vs_index`.
# MAGIC
# MAGIC **Site / library level:** map the caller's Entra groups to the SharePoint sites they may read with a table **you** maintain
# MAGIC (`<catalog>.sharepoint_silver.site_access`: `group_name`, `site_id`) and pass those sites as a filter. **Fail closed**: no mapping, no results.
# MAGIC
# MAGIC **Document level (Path C only):** pass `principal_ids`; the function over-fetches, then drops any chunk whose `item_id`
# MAGIC has no row in `file_acls` for the caller's principals.

# COMMAND ----------

from databricks.vector_search.client import VectorSearchClient

dbutils.widgets.text("catalog", "m365")
dbutils.widgets.text("vs_endpoint", "vs-sharepoint-dev")
dbutils.widgets.text("vs_index", "m365.sharepoint_silver.chunks_index")
CATALOG = dbutils.widgets.get("catalog")
ENDPOINT = dbutils.widgets.get("vs_endpoint")
INDEX = dbutils.widgets.get("vs_index")

idx = VectorSearchClient().get_index(endpoint_name=ENDPOINT, index_name=INDEX)


def _quote_list(values):
    return ",".join("'" + str(v).replace("'", "''") + "'" for v in values)


def allowed_sites_for(groups):
    if not groups:
        return []
    rows = spark.sql(f"""SELECT DISTINCT site_id FROM {CATALOG}.sharepoint_silver.site_access
                         WHERE group_name IN ({_quote_list(groups)})""").collect()
    return [r.site_id for r in rows]


def secure_search(question, groups, principal_ids=None, k=8):
    """Return up to k rows [chunk_id, item_id, path, chunk_text, score] the caller is allowed to see."""
    sites = allowed_sites_for(groups)
    if not sites:
        return []                           # fail closed
    res = idx.similarity_search(
        query_text=question,
        columns=["chunk_id", "item_id", "path", "chunk_text"],
        filters={"source_scope": sites},    # IN-filter on a scalar column
        num_results=k * 4 if principal_ids else k,
        query_type="HYBRID")                # vector + keyword
    rows = res["result"]["data_array"]
    if principal_ids:                       # Path C: per-document ACL post-filter
        ok = {r.item_id for r in spark.sql(
            f"""SELECT DISTINCT item_id FROM {CATALOG}.sharepoint_bronze.file_acls
                WHERE principal_id IN ({_quote_list(principal_ids)})""").collect()}
        rows = [r for r in rows if r[1] in ok][:k]
    return rows
