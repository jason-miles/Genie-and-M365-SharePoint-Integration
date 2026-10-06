# Genie & Microsoft 365 / SharePoint Integration on Databricks

A step-by-step engineering guide, with working infrastructure code, for connecting Microsoft 365 and SharePoint to Databricks. It covers three ways to do it, how to choose between them, and how to run the result in production.

> **Status check:** capabilities, limits and release statuses below were verified against Databricks and Microsoft documentation on **6 October 2026**. They change often. Re-check the [linked documentation](#references) before production sign-off.

---

## Contents

1. [Choose your path](#1-choose-your-path)
2. [Before you start](#2-before-you-start)
3. [Repository layout](#3-repository-layout)
4. [Path A: Genie One Microsoft 365 connector](#4-path-a-genie-one-microsoft-365-connector-zero-copy)
5. [Path B: Lakeflow Connect SharePoint ingestion](#5-path-b-lakeflow-connect-sharepoint-ingestion-managed-rag-pipeline)
6. [Path C: Custom Microsoft Graph pipeline](#6-path-c-custom-microsoft-graph-pipeline)
7. [Serve the ingested data (Knowledge Assistant or Vector Search)](#7-serve-the-ingested-data)
8. [Validate before you roll out](#8-validate-before-you-roll-out)
9. [Operate it in production](#9-operate-it-in-production)
10. [Troubleshooting](#10-troubleshooting)
11. [Security checklist](#11-security-checklist)
12. [References](#references)

---

## 1. Choose your path

"SharePoint" maps to three very different back-ends. Name the one you mean in every design conversation.

| | **A. Genie One M365 connector** | **B. Lakeflow Connect SharePoint** | **C. Custom Microsoft Graph pipeline** |
|---|---|---|---|
| What it is | Managed MCP connector; live search-in-place | Managed, incremental ingestion pipeline | Your own Python job calling Graph REST APIs |
| Where data lives | Stays in SharePoint (zero-copy) | Copied into Unity Catalog Delta tables | Copied into UC Volumes and Delta tables |
| Retrieval | Microsoft Search index (keyword, Microsoft ranking) | Knowledge Assistant or Vector Search over your data | Knowledge Assistant or Vector Search over your data |
| Access control | **Per-user, live** (M365 permissions enforced on every query) | Unity Catalog grants on destination tables (**SharePoint ACLs are not ingested**) | UC grants plus ACLs you capture and filter on yourself |
| PDFs / scans | Not supported | Yes (binary ingest + `ai_parse_document`) | Yes |
| File size | Max 25 MB | Binary files over 100 MB can fail the update; filter them out. Parser: 100 MB / 500 pages | Parser: 100 MB / 500 pages |
| Complete answers ("list all 14 contracts") | No, top-ranked snippets only | Yes | Yes |
| Setup effort | Minutes | Low to medium | Medium to high |
| Best for | Ad-hoc Q&A over Office/text docs users can already open | Persistent RAG at scale, PDFs, analytics | Per-document ACL trimming, custom metadata, cases B cannot cover |

### Decision guide

Work down the questions and stop at the first **Yes**.

| # | Question | If yes |
|---|---|---|
| 1 | Is the content mostly PDFs, scans or files over 25 MB? | **Path B** |
| 2 | Do users need complete answers ("list / summarise all …")? | **Path B** |
| 3 | Must per-document SharePoint permissions be enforced inside an ingested corpus? | **Path C** (or Path A if live search is enough) |
| 4 | Is it ad-hoc Q&A over Office/text docs users can already open? | **Path A** |
| 5 | None of the above / unsure | **Start with Path A**, then run the [validation plan](#8-validate-before-you-roll-out) |

**Most teams run A and B together:** turn on A now for everyday questions (no build), and ingest the curated, high-value, PDF-heavy libraries with B.

### What this means for very large libraries (for example 65,000 documents)

- **Path A needs no vectorisation.** Microsoft already indexes the library; the connector reuses that index. Library size creates no work for you.
- **But it is search, not browse.** Only the top-ranked slice of hits reaches the model, it returns snippets rather than full documents, it cannot read PDFs/images/binaries or files over 25 MB, and a just-uploaded file may not appear until Microsoft indexes it. "List everything in this folder" returns a partial set.
- **Path B scales, with planning.** There is no documented hard item limit. Plan for a long first backfill (hours, not minutes), run it off-peak so Graph throttling does not hurt other apps in the tenant, and remember the connector ingests **everything** in the folder/drive/site you point it at: you cannot select individual files.

### Release status (6 October 2026)

| Capability | Status | Notes |
|---|---|---|
| Genie One Microsoft 365 connector | GA (10 Sep 2026) | Runs through Unity Gateway, so only in Unity Gateway-supported regions. Per-user OAuth. |
| Lakeflow Connect SharePoint (managed) | GA (4 Aug 2026) | `LIST` entities and structured-file ingestion are still Beta. `BINARYFILE` ingestion is the production path for PDFs/Office files. |
| `ai_parse_document` | GA (16 Apr 2026) | 100 MB and 500 pages per document. Region-limited. |
| Agent Bricks Knowledge Assistant | GA | Up to 10 knowledge sources. Region-limited. |
| Vector Search (AI Search) | GA | Region-limited. Only needed for the custom-RAG variant. |
| Terraform `databricks_pipeline` for SharePoint | Known provider gap | Use the Asset Bundle in this repo (or the Pipelines REST API) for the ingestion pipeline. |

---

## 2. Before you start

### 2.1 Region check (do this first)

Several services are region-gated, and this decides where you build.

| Service | Needed for | What to check |
|---|---|---|
| Unity Gateway | Path A | Your workspace region is on the supported list |
| `ai_parse_document` | Paths B and C | Region is marked available (v2) |
| Knowledge Assistant | Paths B and C (option 1) | Region is available; note whether it needs cross-geo processing |
| Vector Search (AI Search) | Paths B and C (option 2) | Region is available |

Check [Features with limited regional availability](https://docs.databricks.com/aws/en/resources/feature-region-support) for your cloud. If your data-protection rules restrict where inference may run, record that decision once and apply it to every workload here.

### 2.2 Prerequisites

| Item | Owner | Path |
|---|---|---|
| Unity Catalog-enabled workspace with serverless compute | Databricks admin | A, B, C |
| Genie One enabled; users have at least consumer access | Workspace admin | A |
| M365 Global / Privileged Role admin available for **tenant-wide admin consent** | M365 / Entra admin | A, B, C |
| Entra Conditional Access reviewed (CA, MFA and IP policies can silently block the OAuth flow) | Security | A |
| `CREATE CONNECTION` on the metastore | Metastore admin | B |
| `USE CATALOG`, `CREATE SCHEMA` on the destination catalog | Data platform | B, C |
| A secret scope for Entra credentials. Never hard-code secrets; no long-lived PATs | Data platform | B, C |
| 3 to 5 pilot users from different security groups | Project lead | A |

> **Tip:** book the Entra admin on day one. Waiting for tenant-wide consent is the most common source of delay.

### 2.3 Tooling

```bash
./scripts/preflight.sh
```

Checks that `terraform`, `databricks` (CLI), `az` and `python3` are installed. Nothing in the cloud is touched.

---

## 3. Repository layout

```
.
├── README.md
├── terraform/                     Entra app, secrets, Unity Catalog, grants, Vector Search
│   ├── versions.tf  variables.tf  outputs.tf  terraform.tfvars.example
│   ├── entra.tf                   Entra app (Sites.Selected) + client secret + admin consent
│   ├── databricks.tf              secret scope, catalog, schemas, volume, grants
│   └── vector_search.tf           endpoint + Delta Sync index (index gated by a variable)
├── bundle/                        Databricks Asset Bundle
│   ├── databricks.yml             variables and targets
│   ├── resources/option_b.yml     Lakeflow Connect pipeline + nightly job
│   ├── resources/option_c.yml     Custom Graph job
│   └── notebooks/
│       ├── 01_graph_delta_ingest.py            Option C: Graph delta + ACL capture
│       ├── 02_parse_and_chunk.sql              Option B: incremental parse + chunk
│       ├── 02b_parse_and_chunk_from_volume.sql Option C: incremental parse + chunk from Volume
│       ├── 03_sync_vector_index.py             trigger the Vector Search sync
│       ├── lib_secure_search.py                secure_search(): security-trimmed retrieval helpers
│       ├── 04_monitoring_queries.sql           alert queries
│       └── 99_e2e_smoke_test.py                asserting end-to-end test (see tests/)
├── tests/                         synthetic corpus generator + how to run the smoke test
└── scripts/
    ├── preflight.sh               tool check
    ├── get_site_id.sh             find a site's Graph ID
    └── grant_site_access.sh       grant the app read on one site (Sites.Selected)
```

**Division of labour.** Terraform handles Entra, secrets, Unity Catalog, grants and Vector Search. The Asset Bundle handles the ingestion pipeline, jobs and notebooks. This split is deliberate: the Terraform provider cannot yet create the SharePoint ingestion pipeline cleanly, and `databricks_connection` does not list a SharePoint type, so the **Unity Catalog connection is created once in Catalog Explorer**.

---

## 4. Path A: Genie One Microsoft 365 connector (zero-copy)

Time: about 30 minutes. No code required.

### How it works

1. A user asks a question in Genie One chat.
2. The agent decides whether it needs a document search (tool calling is conditional).
3. It calls the Microsoft 365 `search` tool with a keyword query.
4. Databricks' managed proxy (a Unity Catalog connection) attaches **that user's** OAuth token.
5. Microsoft Graph / Microsoft Search runs the query over Microsoft's index, security-trims to what that user can open, and ranks results.
6. Ranked snippets and metadata (title, path, modified date) return to Genie.
7. Genie reads the snippets and writes the answer.

### Steps

1. **Enable the connection (workspace admin).** Confirm *Microsoft 365* is available under Genie One external sources. If it is not visible, check the workspace **Previews** page and your region's Unity Gateway support.
2. **Grant tenant consent (M365 admin).** The connector requests Graph scopes such as `Sites.Read.All`, `Files.Read.All`, `User.Read` and `offline_access`, plus Teams/Outlook/Calendar scopes if those surfaces are enabled. Scopes ending in `.All` need an M365 admin to consent once for the organisation. The consent screen is the authoritative list.
3. **Govern it in Unity Catalog.** The connection is a UC object. Metastore admins and connection owners control who may use it. Changing a connection's permissions requires users to re-authenticate.
4. **Users sign in.** In Genie One, click **+** next to the search bar, then **Microsoft 365**, then **Sign in**. Each user authenticates once with their own identity; tokens are never shared between users.
5. **Pilot.** Run the [20-question test set](#8-validate-before-you-roll-out) with 3 to 5 pilot users from different security groups. Include at least one restricted HR or finance document to prove ACL trimming works.
6. **Publish user guidance:** *"Say 'use SharePoint' if Genie does not search; ask for the document name and date; PDFs are not searchable through this path."*

### Using it from your own agents or apps

The same managed service is available as `system.ai.microsoft_365`. Callers need `EXECUTE` on the service plus `USE CATALOG` and `USE SCHEMA` on its parents. A Databricks App declares `user_api_scopes: [ai-gateway]` and calls the service with the per-user client, so requests run on behalf of the signed-in user.

### Keeping Path A accurate

1. **Scope what users search** (the right sites and libraries). This is the biggest precision lever.
2. **Add Genie instructions** that say when to search SharePoint and how to phrase queries.
3. **Clean up SharePoint search:** good titles, managed metadata, promoted/best-bet results for canonical documents.
4. **Remove superseded versions and duplicates.** Stale copies are the number one cause of wrong answers.
5. **Keep content in supported formats under 25 MB.** Keep a `.docx` or `.md` companion for key PDFs if Path A must answer from them.
6. **Make verification a habit:** always ask Genie to name the document and date it used.

**Done when:** pilot users can sign in, the 20-question run is scored, and a restricted document never surfaces for an unauthorised user.

---

## 5. Path B: Lakeflow Connect SharePoint ingestion (managed RAG pipeline)

Time: about half a day, most of it waiting on consent. Steps 1 to 3 are shared with Path C.

### Step 1: Choose the authentication method

| Method | Use when | Watch out for |
|---|---|---|
| **OAuth M2M (service principal)** | Unattended production pipelines. **Recommended** | Client-secret expiry; rotate at least every 180 days |
| OAuth U2M, Databricks-managed | Proof of concept | Ties the pipeline to one person's identity and permissions; refresh tokens expire after 90 days by default |
| OAuth U2M, custom-managed | You need your own app ownership or control over API rate limiting | More setup |
| Manual token refresh | Avoid for new work | Legacy; prone to `invalid_grant` |

This repo implements **M2M with `Sites.Selected`** (least privilege).

### Step 2: Create the Entra app, secrets and Unity Catalog objects (Terraform)

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars      # fill in databricks_host and tenant_id
az login                                          # identity able to create Entra apps + consent
terraform init
terraform plan
terraform apply
```

This creates:

- An Entra app with the `Sites.Selected` **application** permission and tenant admin consent.
- A client secret (180-day lifetime by default) stored **only** in the Databricks secret scope `m365-sharepoint` (`tenant_id`, `client_id`, `client_secret`).
- The `m365` catalog, `sharepoint_bronze` and `sharepoint_silver` schemas, a managed `raw_files` Volume, and grants (including `USE_CATALOG`).
- A Vector Search endpoint. The index is created in a later apply (see [Step 9](#step-9-create-the-vector-search-index-custom-rag-only)).

Secret values pass through Terraform state. Use an encrypted remote backend with tight access, or create the secret out of band.

Manual alternative: Entra ID, App registrations, New registration; Certificates & secrets, New client secret; API permissions, Microsoft Graph, Application permissions, `Sites.Selected`, then **Grant admin consent**. (Broader option: `Sites.Read.All` + `Files.Read.All`. Not recommended.)

### Step 3: Grant the app read access to each in-scope site

`Sites.Selected` grants nothing by itself. For each site:

```bash
# Find the site's Graph ID
./scripts/get_site_id.sh <tenant>.sharepoint.com /sites/<SiteName>

# Grant read (run as an ADMIN identity allowed to grant site permissions)
./scripts/grant_site_access.sh <site-id> "$(terraform -chdir=terraform output -raw entra_client_id)"
```

The caller must be an admin identity (for example an app holding `Sites.FullControl.All`, used once and then removed). The ingestion app cannot grant itself access. Keep the list of granted sites in version control and review it quarterly.

### Step 4: Create the Unity Catalog connection (Catalog Explorer)

1. Catalog Explorer, **Create connection**, type **Microsoft SharePoint**.
2. Auth: **OAuth Machine to Machine**.
3. Enter Client ID and Client secret (from the Terraform output / secret scope), **Domain** `https://<tenant>.sharepoint.com`, and **Tenant ID**.
4. Name it `sharepoint_m2m` (or change `sharepoint_connection_name` in the bundle).
5. Grant `USE CONNECTION` to the pipeline-owner group only.

### Step 5: Configure and deploy the ingestion pipeline (Asset Bundle)

Edit `bundle/databricks.yml` defaults or pass variables:

```bash
cd bundle
databricks bundle validate -t dev --profile <profile> \
  --var sharepoint_url="https://<tenant>.sharepoint.com/sites/<site>" \
  --var ops_email="oncall@example.com"

databricks bundle deploy -t dev --profile <profile> \
  --var sharepoint_url="https://<tenant>.sharepoint.com/sites/<site>" \
  --var ops_email="oncall@example.com"
```

What the pipeline does (`bundle/resources/option_b.yml`):

```yaml
ingestion_definition:
  connection_name: sharepoint_m2m
  objects:
    - table:
        destination_catalog: m365
        destination_schema: sharepoint_bronze
        destination_table: sharepoint_files
        connector_options:
          sharepoint_options:
            entity_type: FILE
            url: https://<tenant>.sharepoint.com/sites/<site>
            file_ingestion_options:
              format: BINARYFILE
              schema_evolution_mode: NONE
```

Behaviour to know:

- **Entity types:** `FILE` (content + metadata), `FILE_METADATA` (metadata only), `LIST` (SharePoint lists, Beta). Formats: `BINARYFILE`, `CSV`, `JSON`, `XML`, `EXCEL`, `PARQUET`, `AVRO`, `ORC`. Structured formats are Beta; use `BINARYFILE` for PDFs and Office files.
- **Incremental:** later runs re-ingest only files updated since the last run. Changes *inside* a file are not applied incrementally.
- **Deletes:** `BINARYFILE` defaults to SCD Type 1, so changed or deleted files are updated or removed. SCD Type 2 is not supported.
- **Scope:** the connector ingests **everything** under the URL (folder, drive, subsite or site). Scope the URL and use `file_filters` deliberately. Each filter object takes **one** key: `path_filter`, `modified_after` or `modified_before`.
- **Metadata column:** rows carry `_sharepoint_metadata` (site/drive IDs, creator, timestamps, MIME type). **Validate** the field names with `DESCRIBE TABLE`. A `permissions` field may appear, but the published limitation is that file-level SharePoint ACLs are **not** ingested, so do not use it for access decisions.
- **Files over 100 MB** are loaded into memory as a single record and can fail the whole update. Exclude them with a filter on the `length` column (see the SharePoint connector limitations page) and report them to content owners.
- **Long backfills:** access tokens expire after a few hours. On a very long first run the pipeline may stop; it resumes where it left off without losing data but may need a manual restart. Schedule the first run off-peak.

Run the first ingest manually from the pipeline page before enabling the schedule.

### Step 6: Parse and chunk

Notebook `02_parse_and_chunk.sql` (task `parse_chunk` in the job) parses binaries with `ai_parse_document` and writes `sharepoint_silver.parsed_docs` and `sharepoint_silver.chunks` (Change Data Feed enabled, required by Delta Sync indexes).

It is **incremental**: only new or modified documents are parsed (and billed); chunks of modified or deleted documents are removed first so the index never serves stale text; and a document the parser rejects keeps a row in `parsed_docs` with `error_status` set and produces no chunks (the monitoring notebook reports them).

- Validate the `_sharepoint_metadata` field names first (`site_id` is used as the security scope).
- `ai_parse_document` accepts PDF, JPG/PNG, TIFF, DOC/DOCX and PPT/PPTX. Other files (for example plain text) need a different path.

Continue at [Step 7: serve the data](#7-serve-the-ingested-data).

---

## 6. Path C: Custom Microsoft Graph pipeline

Use this only when Path B cannot meet a requirement: per-document ACL capture and trimming, custom metadata enrichment, cross-tenant sources, or very fine scheduling control. **Do Path B steps 1 to 3 first** (Entra app, secrets, site grants). Capturing full permission hierarchies requires higher privilege than `Sites.Selected`; review that with Security.

### How the pipeline works

1. Enumerate each document library (drive) with `GET /drives/{drive-id}/root/delta`, following `@odata.nextLink` until you receive `@odata.deltaLink`, then persist the `deltaLink`. A site can hold several libraries, so this runs **per drive**.
2. On each scheduled run, call the saved `deltaLink` to receive only creates, modifies and deletes (the `deleted` facet). On HTTP `410 Gone`, start a fresh enumeration.
3. **Track items by `id`, not path.** A folder rename does not return its descendants.
4. Capture ACLs with `GET /drives/{drive-id}/items/{item-id}/permissions` (the notebook sends `Prefer: hierarchicalsharing` on delta so you can re-fetch only the roots of permission hierarchies).
5. Handle throttling: respect `Retry-After` on HTTP 429, use JSON batching, avoid polling loops. For one-off bulk extraction of very large tenants, evaluate Microsoft Graph Data Connect, which is not subject to REST throttling.
6. Land files in a UC Volume and metadata + ACLs in Delta tables.

### Steps

1. Find the site ID: `./scripts/get_site_id.sh <tenant>.sharepoint.com /sites/<SiteName>`.
2. Deploy the bundle with the site ID:
   ```bash
   cd bundle
   databricks bundle deploy -t dev --profile <profile> \
     --var graph_site_id="<site-id>" --var ops_email="oncall@example.com" \
     --var sharepoint_url="unused-for-option-c"
   ```
3. Run the job `sharepoint-rag-option-c-dev` once. It runs `01_graph_delta_ingest.py` (ingest + ACLs), `02b_parse_and_chunk_from_volume.sql` (parse + chunk, keeping `item_id` and `site_id`), then `03_sync_vector_index.py`.
4. Tables created in `m365.sharepoint_bronze`: `files` (one row per item), `file_acls` (principals per item), `delta_state` (saved `deltaLink` per drive).

Option B and Option C both write `sharepoint_silver.parsed_docs` and `chunks`. Run one corpus pipeline per silver schema, or point them at different catalogs/schemas.

Continue at [Step 7](#7-serve-the-ingested-data).

---

## 7. Serve the ingested data

Pick one (or both, per security domain).

### Option 1: Agent Bricks Knowledge Assistant (fastest route to production RAG)

Agent Bricks, **Knowledge Assistant**, add the SharePoint-ingested UC table (supported natively) or a UC Volume.

| Topic | Detail |
|---|---|
| Knowledge sources | 1 to 10 per assistant |
| File limits | Files over 100 MB or 500 pages are skipped; file names starting with `_` or `.` are skipped |
| Table source rules | Needs Change Data Feed or a streaming table, a `BINARY`/`STRING` content column, and a metadata struct with path, name, size and modification time. Tables ingested by the SharePoint connector produce this format automatically |
| First sync | Can take a few hours; answers include citations |
| Permissions | `Can Query` / `Can Manage`. **Everyone who can query sees the whole corpus**, so build **one Knowledge Assistant per security domain** (for example Clinical, Finance, HR) |
| Editing sources | Only the creator can add or remove sources; `Can Manage` can sync and rename them |

### Option 2: Vector Search with your own agent

#### Step 9: Create the Vector Search index (custom RAG only)

After the first job run has created `sharepoint_silver.chunks`:

```bash
cd terraform
terraform apply -var create_vector_index=true
```

Then `03_sync_vector_index.py` syncs it on every job run. The index is `TRIGGERED` (sync on demand) and uses `databricks-gte-large-en` embeddings by default.

#### Security-trimmed retrieval

`03_sync_vector_index.py` triggers the sync; `lib_secure_search.py` provides `secure_search()` (use it from your agent/app notebook with `%run ./lib_secure_search`, after `%pip install databricks-vectorsearch` and a Python restart; `databricks-vectorsearch` is **not** preinstalled on serverless, and the bundle jobs declare it as an environment dependency):

- **Site/library-level trimming:** map the caller's Entra groups to the sites they may read in a table you own (`m365.sharepoint_silver.site_access`: `group_name`, `site_id`), and pass those sites as a filter on `source_scope`. **Fail closed:** no mapping means no results.
- **Document-level trimming (Path C only):** over-fetch, then drop any chunk whose `item_id` has no `file_acls` row for the caller's principals.

You own and must build the `site_access` mapping (for example refreshed nightly from Entra group membership and your `Sites.Selected` grant list).

---

## 8. Validate before you roll out

Run the **same question set** through Path A and Path B (or C) and let the numbers decide. For the engineering half, `bundle/notebooks/99_e2e_smoke_test.py` asserts retrieval and security behaviour automatically (see [`tests/README.md`](tests/README.md)).

| Step | Activity | Success criteria |
|---|---|---|
| 1 | Pick one representative library and profile it: count by file type, size bands, duplicates | Know the % of PDF and over-25 MB files (content Path A cannot see) |
| 2 | Write 20 gold questions: 8 targeted lookups, 4 multi-document synthesis, 4 exhaustive ("list all …"), 4 restricted-access | Each has a known answer and source document |
| 3 | Run them through Genie One + the M365 connector with 3 pilot users from different groups | Record hit/miss, correct source, latency |
| 4 | Run the same questions through Knowledge Assistant over the ingested library | Same metrics plus citation accuracy |
| 5 | Compare | Path A at 85% or better on targeted lookups: adopt for ad-hoc. Path B wins on PDF and exhaustive: ingest that library |
| 6 | Security test | Restricted documents never surface for unauthorised users in **either** path |

| Metric | Path A | Path B | Target |
|---|---|---|---|
| Correct answer (targeted) | [  ] / 8 | [  ] / 8 | 85% or better |
| Correct source cited | [  ] / 20 | [  ] / 20 | 90% or better |
| Complete on exhaustive questions | [  ] / 4 | [  ] / 4 | Note the gap; expected for A |
| Restricted-access leaks | [  ] | [  ] | **Zero** |
| Median latency | [  ] s | [  ] s | Agree with the business |

### Suggested phased rollout

| Phase | Timing | Deliverables | Exit criteria |
|---|---|---|---|
| 0. Prepare | Week 1 | Entra consent, CA review, region confirmed, Terraform applied, pilot users named | Security sign-off on scopes |
| 1. Quick win: Path A | Weeks 1-2 | M365 connector enabled for pilot group; user guidance published | 20-question run; ACL test passed |
| 2. Ingest pilot library | Weeks 2-4 | Path B for one library; parse + Knowledge Assistant | Side-by-side results |
| 3. Productionise | Weeks 4-6 | Terraform + bundle in CI, schedule, alerts, runbook, cost dashboard | Pen-test findings closed; on-call agreed |
| 4. Scale out | Week 6+ | More libraries, one corpus per security domain; Path C only where ACL trimming is required | Quarterly access review in place |

### Go / no-go for production

- Zero restricted-access leaks in either path.
- Path B accuracy on exhaustive and PDF questions meets the target agreed with the business.
- Alerts firing and on-call rota agreed.
- Secrets in scope, rotation date set, no PATs.
- Processing-location decision recorded.
- Cost per user within your cap over a two-week pilot.
- No open high or critical pen-test findings.

---

## 9. Operate it in production

### Environments and promotion

- Dev, Test, Prod workspaces (or catalogs), with **separate Entra apps and secrets per environment**.
- All changes through Git and pull request, deployed by CI running `terraform apply` and `databricks bundle deploy -t prod`. No manual UI changes in prod.
- The prod pipeline and job run as a **service principal**, never a named user (see the commented `run_as` in `bundle/databricks.yml`).
- Run `terraform plan` and `databricks bundle validate -t dev` on every change before promotion.
- Rollback: redeploy the previous tagged release. Bronze and silver tables are Delta, so `RESTORE TABLE … TO VERSION AS OF` recovers from a bad parse run; re-sync the index afterwards.

### Monitoring and alerting

`bundle/notebooks/04_monitoring_queries.sql` contains the queries. Turn each into a SQL alert.

| Signal | Source | Threshold / action |
|---|---|---|
| Ingestion pipeline failure | Job email/webhook + `system.lakeflow` | Page on-call after 2 consecutive failures |
| Freshness lag | Freshness query (SQL alert) | Alert if newest file is more than 26 h old |
| Graph throttling (Path C) | Job logs (429 counts) | Reduce concurrency, spread the schedule, consider Graph Data Connect |
| Parse failures / skipped files | Skipped-file and parse-failure queries | Weekly report to content owners |
| Vector index sync status | `idx.describe()` in the job | Fail the job if the index is not ONLINE |
| Answer quality | MLflow evaluation on the 20-question gold set | Re-run weekly; alert on more than 10% regression |
| Cost per user | `system.billing.usage` + serving usage | Alert at 80% of your per-user cap |
| **Client secret expiry** | Rotation calendar + Entra app-credential expiry report | Warn at 30 days; rotate at least every 180 days |
| **Site-grant drift** | Quarterly diff of the `Sites.Selected` grant list against Git | Any difference is a finding |
| **Sign-in failures (Path A)** | Entra sign-in logs for the Databricks enterprise apps | Investigate Conditional Access blocks (error codes 53003, 50076) |

Rotate the client secret deliberately with `terraform apply -replace=azuread_application_password.sp_ingest`, then confirm the next pipeline run succeeds.

### Severity and escalation

| Severity | Example | Response |
|---|---|---|
| **Sev 1** | Restricted content surfaced to an unauthorised user | Disable the affected Knowledge Assistant or connection immediately; open a security incident |
| **Sev 2** | Ingestion down more than 26 h; index not ONLINE | Investigate within 4 business hours |
| **Sev 3** | One file failed to parse; one user cannot sign in | Next business day |
| **Sev 4** | Answer-quality complaint | Triage in the weekly review |

When raising a Databricks support case, include the pipeline/update ID, the workspace ID and the error from **Update details, Logs**.

### Monthly checklist

- Review failed and skipped files; send the report to content owners.
- Re-run the gold-set evaluation and compare with last month.
- Review cost against the per-user cap.
- Review `Sites.Selected` grants and UC group membership.
- Check secret expiry dates (next 60 days).
- Re-read the release notes for the services used and update the status table.

---

## 10. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Genie ignores SharePoint | Tool did not trigger | Prompt "use SharePoint"; add Genie instructions |
| "Can't find" a PDF | PDFs are not supported by the M365 connector | Ingest that library (Path B) |
| Wrong or old document cited | Duplicates or old versions outrank the current one | Clean up versions; promoted results; ask for document name and date |
| Restricted doc missing for a user | Correct ACL trimming | Expected; grant access in SharePoint if appropriate |
| M365 sign-in loops or fails | Conditional Access, MFA or IP policy | Exempt or configure the Databricks enterprise app in Entra |
| Connector not visible in Genie One | Region not supported by Unity Gateway, or feature not enabled | Check regional availability and the Previews page |
| Pipeline 401 / 403 | Secret expired, or site not granted | Rotate the secret; re-run `grant_site_access.sh` |
| "Site could not be found" | The authenticating identity cannot read the site | Grant the app or user access to that site |
| `invalid_grant` (manual token refresh) | Expired or revoked refresh token | Move to OAuth M2M or U2M |
| Update fails with out-of-memory on a large file | A binary file over 100 MB | Filter files over 100 MB out of the ingestion |
| Pipeline stops mid-backfill | Access token expired on a very long run | Restart the update; it resumes where it left off |
| HTTP 429 from Graph | Tenant throttling | Honour `Retry-After`, batch, schedule off-peak |
| Delta `410 Gone` (Path C) | Expired delta token | Full re-enumeration (handled in notebook 01) |
| Knowledge Assistant missing some files | Over 100 MB / 500 pages, or names starting with `_` or `.` | Rename or split; see the skipped-file report |
| `terraform apply` fails creating the index | Chunks table does not exist yet | Keep `create_vector_index=false` until the parse job has run once |
| `ModuleNotFoundError: databricks.vector_search` | `databricks-vectorsearch` is not preinstalled on serverless | Keep the job `environments` dependency from `bundle/resources/*.yml`, or `%pip install databricks-vectorsearch` and restart Python. An in-process `pip install` without a restart does not work |
| A document is in `parsed_docs` but has no chunks | The parser rejected it (`error_status`) or every element was under 50 characters | Run the parse-failure queries in `04_monitoring_queries.sql`; fix or replace the file |
| Join returns no rows in `02b` | `read_files` paths carry a `dbfs:` prefix or differ from `files.path` | Compare a few values and adjust the `regexp_replace` |

For failed Lakeflow pipelines, open the failed step and then **Update details, Logs**. For OAuth errors, the Databricks SharePoint troubleshooting page provides a script that exchanges the refresh token and lists the site's drives via Microsoft Graph.

---

## 11. Security checklist

- **Least privilege:** `Sites.Selected` for ingestion apps, a per-site grant list under version control, quarterly review.
- **Secrets:** Entra client secrets live only in Databricks secret scopes, rotated every 180 days or less (or use certificates / federated credentials).
- **Identity:** OAuth only, no long-lived PATs for desktop or agent clients, prod runs under service principals.
- **ACL boundary:** state it explicitly in your design. Path A enforces M365 permissions live. Paths B and C rely on UC grants plus your own site/group filters. **Never put an ingested corpus behind a single broadly queryable Knowledge Assistant if it contains restricted content.**
- **Data protection:** tag catalogs and tables holding personal information, restrict inference tables, set retention, and keep processing in the region your data-protection officer approved.
- **Audit:** Unity Catalog audit logs (`system.access.audit`) cover connection use, table access and serving calls. Feed them to your SIEM.
- **Conditional Access:** document the policies applied to the Databricks enterprise apps and test sign-in from every user persona.
- **Offboarding:** Path A access ends with the user's M365 account. For ingested corpora, remove the user from UC groups the same day.

---

## References

**Genie One and Microsoft 365**
- [Genie One: connect to external tools and sources](https://docs.databricks.com/aws/en/genie-one/external-sources)
- [Genie One overview](https://docs.databricks.com/aws/en/genie-one/)
- [Managed OAuth for MCP tools](https://docs.databricks.com/aws/en/agents/mcp-tools/managed-oauth)
- [External MCP services and on-behalf-of-user access](https://docs.databricks.com/aws/en/generative-ai/mcp/external-mcp)
- [September 2026 release notes](https://docs.databricks.com/aws/en/release-notes/product/2026/september)

**Lakeflow Connect SharePoint**
- [Connector overview](https://docs.databricks.com/aws/en/ingestion/lakeflow-connect/sharepoint)
- [Source setup overview (authentication options)](https://docs.databricks.com/aws/en/ingestion/lakeflow-connect/sharepoint-source-setup-overview)
- [OAuth M2M setup](https://docs.databricks.com/aws/en/ingestion/lakeflow-connect/sharepoint-source-setup-m2m)
- [Create a connection](https://docs.databricks.com/aws/en/ingestion/lakeflow-connect/sharepoint-connection)
- [Create an ingestion pipeline](https://docs.databricks.com/aws/en/ingestion/lakeflow-connect/sharepoint-pipeline)
- [Connector reference](https://docs.databricks.com/aws/en/ingestion/lakeflow-connect/sharepoint-reference)
- [Limitations](https://docs.databricks.com/aws/en/ingestion/lakeflow-connect/sharepoint-limits)
- [FAQs](https://docs.databricks.com/aws/en/ingestion/lakeflow-connect/sharepoint-faq)
- [Troubleshooting](https://docs.databricks.com/aws/en/ingestion/lakeflow-connect/sharepoint-troubleshoot)
- [Ingest files from SharePoint (standard connector)](https://docs.databricks.com/aws/en/ingestion/sharepoint)
- [August 2026 release notes](https://docs.databricks.com/aws/en/release-notes/product/2026/august)

**Parsing, RAG, regions**
- [`ai_parse_document`](https://docs.databricks.com/aws/en/sql/language-manual/functions/ai_parse_document)
- [Knowledge Assistant](https://docs.databricks.com/aws/en/generative-ai/agent-bricks/knowledge-assistant)
- [Vector Search](https://docs.databricks.com/aws/en/generative-ai/vector-search)
- [Lakeflow system tables](https://docs.databricks.com/aws/en/admin/system-tables/jobs)
- [Features with limited regional availability](https://docs.databricks.com/aws/en/resources/feature-region-support)
- [Supported clouds and regions](https://docs.databricks.com/aws/en/resources/supported-regions)

**Microsoft Graph**
- [driveItem: delta](https://learn.microsoft.com/en-us/graph/api/driveitem-delta)
- [Discovering files and detecting changes at scale](https://learn.microsoft.com/en-us/onedrive/developer/rest-api/concepts/scan-guidance)
- [Throttling guidance](https://learn.microsoft.com/en-us/graph/throttling) and [service-specific limits](https://learn.microsoft.com/en-us/graph/throttling-limits)
- [Selected permissions (`Sites.Selected`)](https://learn.microsoft.com/en-us/graph/permissions-selected-overview)
- [Create site permission](https://learn.microsoft.com/en-us/graph/api/site-post-permissions)
- [Microsoft Search API](https://learn.microsoft.com/en-us/graph/search-concept-overview)
- [Microsoft Graph Data Connect](https://learn.microsoft.com/en-us/graph/data-connect-concept-overview)

**Terraform**
- [`databricks_vector_search_index`](https://registry.terraform.io/providers/databricks/databricks/latest/docs/resources/vector_search_index), [`databricks_grants`](https://registry.terraform.io/providers/databricks/databricks/latest/docs/resources/grants), [`databricks_secret_scope`](https://registry.terraform.io/providers/databricks/databricks/latest/docs/resources/secret_scope), [`databricks_volume`](https://registry.terraform.io/providers/databricks/databricks/latest/docs/resources/volume)
- [`azuread_application`](https://registry.terraform.io/providers/hashicorp/azuread/latest/docs/resources/application), [`azuread_app_role_assignment`](https://registry.terraform.io/providers/hashicorp/azuread/latest/docs/resources/app_role_assignment), [`azuread_application_password`](https://registry.terraform.io/providers/hashicorp/azuread/latest/docs/resources/application_password)

---

## What has been tested

A run on a live Databricks workspace (AWS, serverless compute, 6 October 2026) against a **synthetic** corpus (PDF and Word files in two simulated sites, one restricted):

| Verified | Result |
|---|---|
| `terraform validate` against the real providers | Pass |
| `databricks bundle validate` against a workspace | Pass |
| `02_parse_and_chunk.sql` as a serverless job with job parameters | Pass: PDF and DOCX parsed, chunks created, Change Data Feed on |
| Incremental behaviour | No-change rerun re-parsed nothing; a modified file was re-parsed and only its new text remained; a deleted file's chunks were removed; a corrupt file was recorded with its error and produced no chunks |
| `02b_parse_and_chunk_from_volume.sql` against a simulated Graph `files` table | Pass, `item_id` populated |
| Delta Sync index build, `secure_search()` | Pass: correct top document; restricted site never returned to an unmapped group; empty/unknown groups return nothing |
| `04_monitoring_queries.sql` | All queries run |

Bugs found and fixed by that run: `databricks-vectorsearch` not preinstalled on serverless; the parse notebook re-parsed (and re-billed) every document on every run, ignored parse errors and never removed stale chunks.

**Not tested** (needs a Microsoft tenant): the SharePoint Unity Catalog connection, the Lakeflow Connect pipeline against real SharePoint, the Entra app and Terraform `apply`, `01_graph_delta_ingest.py` against the real Graph API, Path A, and Knowledge Assistant. Bronze inputs were simulated, so **validate the real connector's output schema** (`DESCRIBE TABLE`) before relying on the column names used in `02`.

---

## Disclaimer

The code is a reference implementation. Run everything in a dev workspace first, validate the items marked **Validate** against your own output, and review the security settings with your security team before production use.
