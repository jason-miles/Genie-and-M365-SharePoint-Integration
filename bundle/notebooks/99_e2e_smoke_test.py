# Databricks notebook source
# MAGIC %md
# MAGIC # End-to-end smoke test (downstream half of Paths B / C)
# MAGIC Run after `02_parse_and_chunk` has produced chunks and the Vector Search index is online.
# MAGIC It creates a small `site_access` mapping, syncs the index and **asserts** the security behaviour:
# MAGIC relevant retrieval, site-level trimming, fail-closed on no groups, and that unparseable files never reach the index.
# MAGIC Expects the synthetic corpus described in `tests/README.md` (sites `SITE_POLICIES` and `SITE_HR`).

# COMMAND ----------

# MAGIC %pip install -q databricks-vectorsearch

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

dbutils.widgets.text("catalog", "m365")
dbutils.widgets.text("vs_endpoint", "vs-sharepoint-dev")
dbutils.widgets.text("vs_index", "m365.sharepoint_silver.chunks_index")
_params = {k: dbutils.widgets.get(k) for k in ("catalog", "vs_endpoint", "vs_index")}

# COMMAND ----------

# MAGIC %run ./lib_secure_search

# COMMAND ----------

import time

print(dbutils.notebook.run("./03_sync_vector_index", 900, _params))

# wait until the sync has landed (index row count matches the chunk table)
expected = spark.sql(f"SELECT count(*) c FROM {CATALOG}.sharepoint_silver.chunks").first().c
for _ in range(60):
    st = idx.describe().get("status", {})
    if st.get("ready") and st.get("indexed_row_count") == expected:
        break
    time.sleep(10)
assert idx.describe().get("status", {}).get("indexed_row_count") == expected, \
    f"index has not caught up to {expected} chunks: {idx.describe().get('status')}"

# COMMAND ----------

spark.sql(f"""CREATE OR REPLACE TABLE {CATALOG}.sharepoint_silver.site_access AS
  SELECT * FROM VALUES ('grp-claims','SITE_POLICIES'), ('grp-hr','SITE_POLICIES'), ('grp-hr','SITE_HR')
  AS t(group_name, site_id)""")

results = {}

# 1. Relevant retrieval for a permitted user
rows = secure_search("When does an MRI scan need pre-authorisation?", ["grp-claims"], k=3)
assert rows, "no results for a permitted user"
results["mri_top_path"] = rows[0][2].rsplit("/", 1)[-1]
assert "mri_preauthorisation" in rows[0][2], f"wrong top document: {rows[0][2]}"

# 2. Restricted site is invisible to a user without access
rows = secure_search("What is the salary range for band E?", ["grp-claims"], k=5)
leaked = [r[2] for r in rows if "SITE_HR" in r[2]]
assert not leaked, f"restricted documents leaked: {leaked}"

# 3. ...and visible to a user with access
rows = secure_search("What is the salary range for band E?", ["grp-hr"], k=5)
assert any("salary_bands" in r[2] for r in rows), "authorised user cannot find restricted document"

# 4. Fail closed: no groups, or groups with no mapping, return nothing
assert secure_search("anything", []) == []
assert secure_search("anything", ["grp-unknown"]) == []

# 5. Documents that failed to parse never reach the index
bad = spark.sql(f"SELECT count(*) c FROM {CATALOG}.sharepoint_silver.chunks WHERE path LIKE '%corrupt%'").first().c
assert bad == 0, "chunks exist for a document that failed to parse"

dbutils.notebook.exit("PASS " + str(results))
