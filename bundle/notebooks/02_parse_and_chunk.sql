-- Databricks notebook source
-- MAGIC %md
-- MAGIC # Option B: parse and chunk the Lakeflow Connect bronze table
-- MAGIC Parameters (job parameters): `catalog`, `bronze_table`.
-- MAGIC **Validate first:** run `DESCRIBE TABLE <catalog>.sharepoint_bronze.<bronze_table>` and confirm the
-- MAGIC `_sharepoint_metadata` field names used below (`site_id`) match your pipeline's actual output.

-- COMMAND ----------

CREATE WIDGET TEXT catalog DEFAULT 'm365';
CREATE WIDGET TEXT bronze_table DEFAULT 'sharepoint_files';

-- COMMAND ----------

CREATE OR REPLACE TABLE IDENTIFIER(:catalog || '.sharepoint_silver.parsed_docs') AS
SELECT
  sha2(path, 256)                                   AS doc_id,
  path,
  _sharepoint_metadata                              AS sp_meta,
  ai_parse_document(content, map('version', '2.0')) AS parsed
FROM IDENTIFIER(:catalog || '.sharepoint_bronze.' || :bronze_table)
WHERE length < 100 * 1024 * 1024;                   -- parser limit: 100 MB / 500 pages

-- COMMAND ----------

-- One chunk per parsed element. Merge very small elements downstream if retrieval quality needs it.
-- item_id is NULL for Option B (no per-item Graph ID); the column keeps the schema identical to Option C.
CREATE OR REPLACE TABLE IDENTIFIER(:catalog || '.sharepoint_silver.chunks')
  TBLPROPERTIES (delta.enableChangeDataFeed = true) AS
SELECT
  concat(d.doc_id, '_', e.pos)                      AS chunk_id,
  d.doc_id,
  CAST(NULL AS STRING)                              AS item_id,
  d.path,
  d.sp_meta.site_id                                 AS source_scope,   -- used for security filtering
  e.value:content::STRING                           AS chunk_text
FROM IDENTIFIER(:catalog || '.sharepoint_silver.parsed_docs') d,
     LATERAL variant_explode(d.parsed:document:elements) AS e
WHERE length(e.value:content::STRING) > 50;
