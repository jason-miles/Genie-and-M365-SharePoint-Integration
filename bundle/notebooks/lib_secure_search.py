# Databricks notebook source
# MAGIC %md
# MAGIC # Security-trimmed retrieval helpers
# MAGIC `%run ./lib_secure_search` from your agent or app notebook (after `%pip install databricks-vectorsearch` and a Python restart),
# MAGIC then call `secure_search(question, groups)`.
# MAGIC Parameters: `catalog`, `vs_endpoint`, `vs_index`, `site_access_table` (optional; default `<catalog>.sharepoint_silver.site_access`).
# MAGIC
# MAGIC **Site / library level:** map the caller's Entra groups to the SharePoint sites they may read with a table **you** maintain
# MAGIC (`group_name`, `site_id`) and pass those sites as a filter. **Fails closed**: no mapping, no results.
# MAGIC
# MAGIC **Document level (Path C only):** pass `document_acls=True` and the caller's `principal_ids`. The function over-fetches, then drops any
# MAGIC chunk whose `item_id` has no row in `file_acls` for those principals. With `document_acls=True`, `principal_ids=None` is an error and
# MAGIC `principal_ids=[]` returns nothing, so a failed principal lookup cannot silently widen access. Without it, only site-level trimming applies.
# MAGIC
# MAGIC **Important:** this trimming only protects callers who go through `secure_search`. Do not grant end users `SELECT` on the silver tables
# MAGIC or query access on the index directly: give those to the service principal that runs your agent.

# COMMAND ----------

from databricks.vector_search.client import VectorSearchClient
from pyspark.sql import functions as F

dbutils.widgets.text("catalog", "m365")
dbutils.widgets.text("vs_endpoint", "vs-sharepoint-dev")
dbutils.widgets.text("vs_index", "m365.sharepoint_silver.chunks_index")
try:                                        # a calling notebook may already have set this (the smoke test does)
    _site_access_override = dbutils.widgets.get("site_access_table")
except Exception:
    dbutils.widgets.text("site_access_table", "")
    _site_access_override = ""
CATALOG = dbutils.widgets.get("catalog")
ENDPOINT = dbutils.widgets.get("vs_endpoint")
INDEX = dbutils.widgets.get("vs_index")
SITE_ACCESS_TABLE = _site_access_override or f"{CATALOG}.sharepoint_silver.site_access"
ACL_TABLE = f"{CATALOG}.sharepoint_bronze.file_acls"

_idx = None


def get_idx():
    """Create the index client on first use, so the helpers can be loaded before the index exists."""
    global _idx
    if _idx is None:
        _idx = VectorSearchClient().get_index(endpoint_name=ENDPOINT, index_name=INDEX)
    return _idx


def allowed_sites_for(groups):
    """Sites the caller's groups may read. Uses the DataFrame API (no string-built SQL), so group names cannot inject SQL."""
    groups = [str(g) for g in (groups or [])]
    if not groups:
        return []
    rows = (spark.table(SITE_ACCESS_TABLE)
            .filter(F.col("group_name").isin(groups))
            .select("site_id").distinct().collect())
    return [r.site_id for r in rows]


def secure_search(question, groups, principal_ids=None, k=8, document_acls=False):
    """Return up to k rows [chunk_id, item_id, path, chunk_text, score] the caller is allowed to see."""
    if document_acls and principal_ids is None:
        raise ValueError("document_acls=True requires principal_ids (use [] to mean 'no principals' and get no results)")
    sites = allowed_sites_for(groups)
    if not sites:
        return []                           # fail closed
    if document_acls and not principal_ids:
        return []                           # fail closed: no principals, no document-level access
    res = get_idx().similarity_search(
        query_text=question,
        columns=["chunk_id", "item_id", "path", "chunk_text"],
        filters={"source_scope": sites},    # IN-filter on a scalar column
        num_results=k * 4 if document_acls else k,
        query_type="HYBRID")                # vector + keyword
    rows = res["result"]["data_array"]
    if document_acls:                       # Path C: per-document ACL post-filter
        ok = {r.item_id for r in (spark.table(ACL_TABLE)
                                  .filter(F.col("principal_id").isin([str(p) for p in principal_ids]))
                                  .select("item_id").distinct().collect())}
        rows = [r for r in rows if r[1] in ok][:k]
    return rows
