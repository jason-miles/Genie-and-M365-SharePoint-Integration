-- Databricks notebook source
-- MAGIC %md
-- MAGIC # Operations monitoring queries
-- MAGIC Turn each into a SQL alert. Check column and product names against your account: system table labels evolve.

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

-- Freshness SLA (Option B): newest SharePoint modification landed in bronze. Alert if older than ~26 h.
SELECT max(modificationTime) AS newest_file, current_timestamp() AS now
FROM m365.sharepoint_bronze.sharepoint_files;

-- COMMAND ----------

-- Skipped-file report: files the parser or Knowledge Assistant will not take (over 100 MB)
SELECT path, length
FROM m365.sharepoint_bronze.sharepoint_files
WHERE length >= 100 * 1024 * 1024
ORDER BY length DESC;

-- COMMAND ----------

-- Parse failures: documents that produced no chunks
SELECT d.path
FROM m365.sharepoint_silver.parsed_docs d
LEFT ANTI JOIN m365.sharepoint_silver.chunks c ON c.doc_id = d.doc_id;
