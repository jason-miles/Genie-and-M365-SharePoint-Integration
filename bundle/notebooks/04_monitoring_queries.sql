-- Databricks notebook source
-- MAGIC %md
-- MAGIC # Operations monitoring queries
-- MAGIC Turn each into a SQL alert. Parameters: `catalog`, `bronze_table`. Check column and product names against your account:
-- MAGIC system table labels evolve.

-- COMMAND ----------

CREATE WIDGET TEXT catalog DEFAULT 'm365';
CREATE WIDGET TEXT bronze_table DEFAULT 'sharepoint_files';

-- COMMAND ----------

-- Pipeline health (last 7 days) for SharePoint ingestion
SELECT pipeline_id, update_id, result_state, period_start_time, period_end_time
FROM system.lakeflow.pipeline_update_timeline
WHERE period_start_time > current_timestamp() - INTERVAL 7 DAYS
ORDER BY period_start_time DESC;

-- COMMAND ----------

-- Job failures (last 7 days)
SELECT job_id, run_id, result_state, period_start_time, termination_code
FROM system.lakeflow.job_run_timeline
WHERE result_state NOT IN ('SUCCEEDED') AND period_start_time > current_date() - 7;

-- COMMAND ----------

-- Cost by product for the RAG workloads (compare against your per-user cost cap)
SELECT usage_date, billing_origin_product, sum(usage_quantity) AS dbus
FROM system.billing.usage
WHERE usage_date > current_date() - 30
  AND billing_origin_product IN ('LAKEFLOW_CONNECT','VECTOR_SEARCH','AGENT_BRICKS','MODEL_SERVING','SERVERLESS_COMPUTE')
GROUP BY ALL ORDER BY usage_date DESC;

-- COMMAND ----------

-- Freshness SLA (Path B): newest SharePoint modification landed in bronze. Alert if older than ~26 h.
SELECT max(modificationTime) AS newest_file, current_timestamp() AS now,
       timestampdiff(HOUR, max(modificationTime), current_timestamp()) AS hours_behind
FROM IDENTIFIER(:catalog || '.sharepoint_bronze.' || :bronze_table);

-- COMMAND ----------

-- Skipped-file report: files the parser or Knowledge Assistant will not take (100 MB or larger)
SELECT path, length
FROM IDENTIFIER(:catalog || '.sharepoint_bronze.' || :bronze_table)
WHERE length >= 100 * 1024 * 1024
ORDER BY length DESC;

-- COMMAND ----------

-- Parse failures: documents the parser rejected (send to content owners weekly; alert if the count jumps)
SELECT path, error_status, parsed_at
FROM IDENTIFIER(:catalog || '.sharepoint_silver.parsed_docs')
WHERE error_status IS NOT NULL
ORDER BY parsed_at DESC;

-- COMMAND ----------

-- Silently empty documents: parsed cleanly but produced no chunks (for example only very short elements)
SELECT d.path
FROM IDENTIFIER(:catalog || '.sharepoint_silver.parsed_docs') d
LEFT ANTI JOIN IDENTIFIER(:catalog || '.sharepoint_silver.chunks') c ON c.doc_id = d.doc_id
WHERE d.error_status IS NULL;

-- COMMAND ----------

-- Corpus size per security scope (sanity check that no site is missing or unexpectedly empty)
SELECT source_scope, count(DISTINCT doc_id) AS documents, count(*) AS chunks
FROM IDENTIFIER(:catalog || '.sharepoint_silver.chunks')
GROUP BY source_scope ORDER BY source_scope;

-- COMMAND ----------

-- Path C: files recorded by the Graph pipeline that never reached parsed_docs (for example a path-matching problem in 02b)
SELECT f.item_id, f.path
FROM IDENTIFIER(:catalog || '.sharepoint_bronze.files') f
LEFT ANTI JOIN IDENTIFIER(:catalog || '.sharepoint_silver.parsed_docs') p ON p.doc_id = f.item_id
WHERE NOT f.is_deleted;
