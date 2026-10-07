# Downstream smoke test (no Microsoft tenant needed)

Tests everything **after** SharePoint: Volume files, `ai_parse_document`, incremental chunking, Vector Search sync and
security-trimmed retrieval. It cannot exercise the SharePoint connector, the Entra app or the Graph calls, so a bronze table
and a Graph-style `files` table are **simulated** from files in a Volume.

Set `C` to a catalog you can create schemas in, and `--profile` to your profile.

```bash
C=<catalog>; P=<profile>
q() { databricks experimental aitools tools query "$1" --profile $P; }

# 1. Synthetic corpus -> Volume
pip install reportlab python-docx
python3 tests/make_synthetic_corpus.py /tmp/corpus
q "CREATE SCHEMA IF NOT EXISTS $C.sharepoint_bronze"; q "CREATE SCHEMA IF NOT EXISTS $C.sharepoint_silver"
q "CREATE VOLUME IF NOT EXISTS $C.sharepoint_bronze.raw_files"
databricks fs cp -r /tmp/corpus dbfs:/Volumes/$C/sharepoint_bronze/raw_files --profile $P

# 2. Simulated Path B bronze table (shape of the Lakeflow Connect output; VALIDATE against real output with DESCRIBE TABLE)
q "CREATE OR REPLACE TABLE $C.sharepoint_bronze.sharepoint_files AS
   SELECT path, modificationTime, length, content,
          named_struct('site_id', regexp_extract(path,'(SITE_[A-Z]+)',1), 'drive_id','simulated') AS _sharepoint_metadata
   FROM read_files('/Volumes/$C/sharepoint_bronze/raw_files/', format => 'binaryFile', recursiveFileLookup => true)"

# 3. Run 02_parse_and_chunk (import the notebook and run it as a serverless job with base_parameters catalog / bronze_table),
#    then create the Vector Search endpoint + index (see terraform/vector_search.tf for the spec).
# 4. Run 99_e2e_smoke_test (parameters: catalog, vs_endpoint, vs_index). It ends with "PASS ..." or fails an assertion.
```

## Scenarios worth running

| Scenario | How | Expected |
|---|---|---|
| Fresh | Run `02` on the base corpus | Every document parsed; chunks created; CDF enabled |
| No change | Run `02` again | Zero documents re-parsed (`parsed_at` unchanged) |
| Change | `make_synthetic_corpus.py --revised`, re-copy, rebuild the bronze table, run `02` | Modified file re-parsed and only its new text is indexed; deleted file's chunks removed; corrupt file recorded in `parsed_docs.error_status` with no chunks; untouched files not re-parsed |
| Security | `99_e2e_smoke_test` | Restricted site never returned to an unmapped group; unknown or empty groups return nothing |
| Path C | Build a `sharepoint_bronze.files` table from the Volume listing and run `02b` | Same results, with `item_id` populated on every chunk |

## Clean up

Drop the test schemas, delete the Vector Search index and endpoint (a running endpoint costs money), and remove the workspace folder you imported the notebooks into.
