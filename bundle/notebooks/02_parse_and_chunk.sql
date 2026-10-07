-- Databricks notebook source
-- MAGIC %md
-- MAGIC # Path B: incremental parse and chunk of the Lakeflow Connect bronze table
-- MAGIC Parameters (job parameters): `catalog`, `bronze_table`.
-- MAGIC
-- MAGIC Only **new or modified** documents are parsed (and billed). Chunks for modified or deleted documents are removed first,
-- MAGIC so the index never serves stale text. Documents that fail to parse keep a row in `parsed_docs` with `error_status` set
-- MAGIC and produce no chunks (see `04_monitoring_queries.sql`).
-- MAGIC
-- MAGIC **Validate first:** run `DESCRIBE TABLE <catalog>.sharepoint_bronze.<bronze_table>` and confirm the columns used here
-- MAGIC (`path`, `modificationTime`, `length`, `content`, `_sharepoint_metadata.site_id`) match your pipeline's actual output.

-- COMMAND ----------

CREATE WIDGET TEXT catalog DEFAULT 'm365';
CREATE WIDGET TEXT bronze_table DEFAULT 'sharepoint_files';

-- COMMAND ----------

CREATE TABLE IF NOT EXISTS IDENTIFIER(:catalog || '.sharepoint_silver.parsed_docs') (
  doc_id            STRING,
  path              STRING,
  modification_time TIMESTAMP,
  source_scope      STRING,     -- SharePoint site ID, used for security filtering
  parsed            VARIANT,
  error_status      STRING,
  parsed_at         TIMESTAMP
);

-- COMMAND ----------

-- item_id is NULL for Path B (no per-item Graph ID); the column keeps the schema identical to Path C.
CREATE TABLE IF NOT EXISTS IDENTIFIER(:catalog || '.sharepoint_silver.chunks') (
  chunk_id     STRING,
  doc_id       STRING,
  item_id      STRING,
  path         STRING,
  source_scope STRING,
  chunk_text   STRING
) TBLPROPERTIES (delta.enableChangeDataFeed = true);   -- required by Delta Sync indexes

-- COMMAND ----------

-- 1. Remove chunks of documents that were modified or deleted in SharePoint
DELETE FROM IDENTIFIER(:catalog || '.sharepoint_silver.chunks')
WHERE doc_id IN (
  SELECT p.doc_id
  FROM IDENTIFIER(:catalog || '.sharepoint_silver.parsed_docs') p
  WHERE NOT EXISTS (
    SELECT 1 FROM IDENTIFIER(:catalog || '.sharepoint_bronze.' || :bronze_table) b
    WHERE sha2(b.path, 256) = p.doc_id AND b.modificationTime = p.modification_time));

-- COMMAND ----------

-- 2. Remove their parsed rows
DELETE FROM IDENTIFIER(:catalog || '.sharepoint_silver.parsed_docs') p
WHERE NOT EXISTS (
  SELECT 1 FROM IDENTIFIER(:catalog || '.sharepoint_bronze.' || :bronze_table) b
  WHERE sha2(b.path, 256) = p.doc_id AND b.modificationTime = p.modification_time);

-- COMMAND ----------

-- 3. Parse only documents we have not parsed yet (new or modified). Parser limit: 100 MB / 500 pages.
INSERT INTO IDENTIFIER(:catalog || '.sharepoint_silver.parsed_docs')
SELECT
  sha2(path, 256),
  path,
  modificationTime,
  _sharepoint_metadata.site_id,
  parsed,
  CAST(parsed:error_status AS STRING),
  current_timestamp()
FROM (
  SELECT b.path, b.modificationTime, b._sharepoint_metadata,
         ai_parse_document(b.content, map('version', '2.0')) AS parsed
  FROM IDENTIFIER(:catalog || '.sharepoint_bronze.' || :bronze_table) b
  WHERE b.length < 100 * 1024 * 1024
    AND NOT EXISTS (
      SELECT 1 FROM IDENTIFIER(:catalog || '.sharepoint_silver.parsed_docs') p
      WHERE p.doc_id = sha2(b.path, 256))
);

-- COMMAND ----------

-- 4. Chunk documents that parsed cleanly and have no chunks yet (one chunk per parsed element;
--    merge very small elements downstream if retrieval quality needs it)
INSERT INTO IDENTIFIER(:catalog || '.sharepoint_silver.chunks')
SELECT
  concat(d.doc_id, '_', e.pos),
  d.doc_id,
  CAST(NULL AS STRING),
  d.path,
  d.source_scope,
  e.value:content::STRING
FROM IDENTIFIER(:catalog || '.sharepoint_silver.parsed_docs') d,
     LATERAL variant_explode(d.parsed:document:elements) AS e
WHERE d.error_status IS NULL
  AND length(e.value:content::STRING) > 50
  AND NOT EXISTS (
    SELECT 1 FROM IDENTIFIER(:catalog || '.sharepoint_silver.chunks') c WHERE c.doc_id = d.doc_id);
