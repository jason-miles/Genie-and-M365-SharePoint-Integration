# Databricks notebook source
# MAGIC %md
# MAGIC # Vector Search sync and security-trimmed query
# MAGIC Syncs the TRIGGERED Delta Sync index, then (in the demo cell) runs a query filtered to the sites the caller may read.
# MAGIC Exits cleanly if the index does not exist yet (Knowledge-Assistant-only setups, or before the second terraform apply).

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
    dbutils.notebook.exit(f"Index {INDEX} not found on {ENDPOINT}; skipping sync ({type(e).__name__})")

idx.sync()                                  # TRIGGERED pipeline: pick up new / changed chunks
status = idx.describe().get("status", {})
if not status.get("ready", True):           # surface a stuck index to the job (alerting)
    print(f"WARNING: index status not ready: {status}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Query-time security trimming (demo)
# MAGIC Library / site level: map the caller's Entra groups to the SharePoint sites they may read with a table **you**
# MAGIC maintain (`site_access`: `group_name`, `site_id`), and pass those sites as a filter. **Fail closed**: no mapping, no results.
# MAGIC Document level (Option C only): over-fetch, then drop any chunk whose `item_id` has no ACL row for the caller's principals.

# COMMAND ----------

def allowed_sites_for(groups):
    if not groups:
        return []
    in_list = ",".join("'" + g.replace("'", "''") + "'" for g in groups)
    rows = spark.sql(f"""SELECT DISTINCT site_id FROM m365.sharepoint_silver.site_access
                         WHERE group_name IN ({in_list})""").collect()
    return [r.site_id for r in rows]


def secure_search(question, groups, principal_ids=None, k=8):
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
    if principal_ids:                       # Option C: per-document ACL post-filter
        in_list = ",".join("'" + p.replace("'", "''") + "'" for p in principal_ids)
        ok = {r.item_id for r in spark.sql(
            f"SELECT DISTINCT item_id FROM m365.sharepoint_bronze.file_acls WHERE principal_id IN ({in_list})").collect()}
        rows = [r for r in rows if r[1] in ok][:k]
    return rows


# Example (replace with the signed-in user's groups from SCIM / Graph /me/memberOf in your app):
# for row in secure_search("What is the pre-authorisation rule for MRI scans?", ["grp-claims"]):
#     print(row[2], "|", row[3][:200])
