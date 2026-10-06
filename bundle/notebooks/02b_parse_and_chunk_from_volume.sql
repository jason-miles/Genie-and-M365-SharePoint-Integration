-- Databricks notebook source
-- MAGIC %md
-- MAGIC # Option C: parse and chunk the files the Graph pipeline landed in the UC Volume
-- MAGIC Joins the Volume files to the `files` metadata table so every chunk keeps its `item_id`
-- MAGIC (needed for per-document ACL filtering) and `site_id`.
-- MAGIC **Validate first:** the `path` returned by `read_files` may carry a `dbfs:` prefix; the regexp below strips it.
-- MAGIC Compare a few `r.path` values with `files.path` if the join returns no rows.
-- MAGIC If `read_files` rejects the concatenated path, hard-code the Volume path for your catalog.
-- MAGIC Note: this re-parses every file each run. Switch to an incremental MERGE once volumes grow.

-- COMMAND ----------

CREATE WIDGET TEXT catalog DEFAULT 'm365';

-- COMMAND ----------

CREATE OR REPLACE TABLE IDENTIFIER(:catalog || '.sharepoint_silver.parsed_docs') AS
SELECT
  f.item_id                                          AS doc_id,
  f.item_id,
  f.site_id,
  f.path,
  ai_parse_document(r.content, map('version', '2.0')) AS parsed
FROM (
  SELECT regexp_replace(path, '^dbfs:', '') AS path, content, length
  FROM read_files('/Volumes/' || :catalog || '/sharepoint_bronze/raw_files/',
                  format => 'binaryFile', recursiveFileLookup => true)
) r
JOIN IDENTIFIER(:catalog || '.sharepoint_bronze.files') f ON r.path = f.path
WHERE NOT f.is_deleted
  AND r.length < 100 * 1024 * 1024;                  -- parser limit: 100 MB / 500 pages

-- COMMAND ----------

CREATE OR REPLACE TABLE IDENTIFIER(:catalog || '.sharepoint_silver.chunks')
  TBLPROPERTIES (delta.enableChangeDataFeed = true) AS
SELECT
  concat(d.doc_id, '_', e.pos)                       AS chunk_id,
  d.doc_id,
  d.item_id,
  d.path,
  d.site_id                                          AS source_scope,
  e.value:content::STRING                            AS chunk_text
FROM IDENTIFIER(:catalog || '.sharepoint_silver.parsed_docs') d,
     LATERAL variant_explode(d.parsed:document:elements) AS e
WHERE length(e.value:content::STRING) > 50;
