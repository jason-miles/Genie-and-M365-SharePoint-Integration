-- Databricks notebook source
-- MAGIC %md
-- MAGIC # Option C: incremental parse and chunk of files landed in the UC Volume by the Graph pipeline
-- MAGIC Same design and output schema as `02_parse_and_chunk.sql` (Option B), so the index and search code are identical.
-- MAGIC The difference: files come from the Volume, joined to `sharepoint_bronze.files` so every chunk keeps its `item_id`
-- MAGIC (needed for per-document ACL filtering) and `site_id`. `doc_id` is the stable Graph `item_id`, so a rename does not re-parse.
-- MAGIC
-- MAGIC Only new or modified documents are parsed (and billed). Parameter: `catalog`.
-- MAGIC
-- MAGIC **Validate first:** the `path` returned by `read_files` may carry a `dbfs:` prefix (stripped below). If the join returns no rows,
-- MAGIC compare a few values with `files.path`. If `read_files` rejects the concatenated Volume path, hard-code it for your catalog.
-- MAGIC Known optimisation: `read_files` lists the whole Volume each run; parsing is still limited to new/changed documents.

-- COMMAND ----------

CREATE WIDGET TEXT catalog DEFAULT 'm365';

-- COMMAND ----------

CREATE TABLE IF NOT EXISTS IDENTIFIER(:catalog || '.sharepoint_silver.parsed_docs') (
  doc_id            STRING,
  path              STRING,
  modification_time TIMESTAMP,
  source_scope      STRING,
  parsed            VARIANT,
  error_status      STRING,
  parsed_at         TIMESTAMP
);

-- COMMAND ----------

CREATE TABLE IF NOT EXISTS IDENTIFIER(:catalog || '.sharepoint_silver.chunks') (
  chunk_id     STRING,
  doc_id       STRING,
  item_id      STRING,
  path         STRING,
  source_scope STRING,
  chunk_text   STRING
) TBLPROPERTIES (delta.enableChangeDataFeed = true);

-- COMMAND ----------

-- 1. Remove chunks of documents that were modified or deleted in SharePoint
DELETE FROM IDENTIFIER(:catalog || '.sharepoint_silver.chunks')
WHERE doc_id IN (
  SELECT p.doc_id
  FROM IDENTIFIER(:catalog || '.sharepoint_silver.parsed_docs') p
  WHERE NOT EXISTS (
    SELECT 1 FROM IDENTIFIER(:catalog || '.sharepoint_bronze.files') f
    WHERE f.item_id = p.doc_id AND NOT f.is_deleted AND CAST(f.modified AS TIMESTAMP) = p.modification_time));

-- COMMAND ----------

-- 2. Remove their parsed rows
DELETE FROM IDENTIFIER(:catalog || '.sharepoint_silver.parsed_docs') p
WHERE NOT EXISTS (
  SELECT 1 FROM IDENTIFIER(:catalog || '.sharepoint_bronze.files') f
  WHERE f.item_id = p.doc_id AND NOT f.is_deleted AND CAST(f.modified AS TIMESTAMP) = p.modification_time);

-- COMMAND ----------

-- 3. Parse only documents not parsed yet. Parser limit: 100 MB / 500 pages.
INSERT INTO IDENTIFIER(:catalog || '.sharepoint_silver.parsed_docs')
SELECT
  item_id,
  path,
  CAST(modified AS TIMESTAMP),
  site_id,
  parsed,
  CAST(parsed:error_status AS STRING),
  current_timestamp()
FROM (
  SELECT f.item_id, f.path, f.modified, f.site_id,
         ai_parse_document(r.content, map('version', '2.0')) AS parsed
  FROM IDENTIFIER(:catalog || '.sharepoint_bronze.files') f
  JOIN (
    SELECT regexp_replace(path, '^dbfs:', '') AS path, content, length
    FROM read_files('/Volumes/' || :catalog || '/sharepoint_bronze/raw_files/',
                    format => 'binaryFile', recursiveFileLookup => true)
  ) r ON r.path = f.path
  WHERE NOT f.is_deleted
    AND r.length < 100 * 1024 * 1024
    AND NOT EXISTS (
      SELECT 1 FROM IDENTIFIER(:catalog || '.sharepoint_silver.parsed_docs') p WHERE p.doc_id = f.item_id)
);

-- COMMAND ----------

-- 4. Chunk documents that parsed cleanly and have no chunks yet
INSERT INTO IDENTIFIER(:catalog || '.sharepoint_silver.chunks')
SELECT
  concat(d.doc_id, '_', e.pos),
  d.doc_id,
  d.doc_id,                      -- item_id = doc_id for Option C (used by per-document ACL filtering)
  d.path,
  d.source_scope,
  e.value:content::STRING
FROM IDENTIFIER(:catalog || '.sharepoint_silver.parsed_docs') d,
     LATERAL variant_explode(d.parsed:document:elements) AS e
WHERE d.error_status IS NULL
  AND length(e.value:content::STRING) > 50
  AND NOT EXISTS (
    SELECT 1 FROM IDENTIFIER(:catalog || '.sharepoint_silver.chunks') c WHERE c.doc_id = d.doc_id);
