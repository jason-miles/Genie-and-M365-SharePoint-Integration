# Databricks notebook source
# MAGIC %md
# MAGIC # Path C: Microsoft Graph delta ingestion with ACL capture
# MAGIC Full enumeration on the first run, then only creates / changes / deletes via the saved `deltaLink`, per document library (drive).
# MAGIC Files land in a UC Volume; metadata and ACLs land in Delta tables.
# MAGIC
# MAGIC **Parameters:** `catalog`, `site_id`, `capture_acls` (default `true`), `acl_backfill` (default `false`).
# MAGIC * **Fast first load:** run with `capture_acls=false`, then once with `acl_backfill=true` to fetch ACLs for every file already ingested.
# MAGIC   (A normal delta run after a no-ACL load would not revisit unchanged files, so the backfill is required.)
# MAGIC * The run **fails** (rather than silently succeeding) if any download or Graph call fails; the drive's `deltaLink` is only saved when its run was clean.
# MAGIC
# MAGIC Prerequisite: the Entra app has `Sites.Selected`, has been granted read on the site (`scripts/grant_site_access.sh`), and can read item
# MAGIC permissions. Capturing permissions may need a higher-privileged grant: review with Security.
# MAGIC Known limit: ACL changes on a *folder* are not propagated to its children until the next `acl_backfill=true` run; schedule one periodically.

# COMMAND ----------

import re, time, email.utils
import requests
from datetime import datetime, timezone

dbutils.widgets.text("catalog", "m365")
dbutils.widgets.text("site_id", "")
dbutils.widgets.text("capture_acls", "true")
dbutils.widgets.text("acl_backfill", "false")

CATALOG = dbutils.widgets.get("catalog")
SITE_ID = dbutils.widgets.get("site_id").strip()
CAPTURE_ACLS = dbutils.widgets.get("capture_acls").strip().lower() in ("true", "1", "yes")
ACL_BACKFILL = dbutils.widgets.get("acl_backfill").strip().lower() in ("true", "1", "yes")

# COMMAND ----------

# --- helpers (pure; unit-tested in tests/test_helpers.py) ---------------------------------------------

def sql_str(value):
    """Escape a value for a single-quoted Spark SQL literal (Spark treats backslash as an escape too)."""
    return str(value).replace("\\", "\\\\").replace("'", "''")


def valid_graph_id(value, what):
    if not re.fullmatch(r"[A-Za-z0-9!_.,\-]+", value or ""):
        raise ValueError(f"Unexpected characters in {what}: {value!r}")
    return value


def retry_after_seconds(header_value, attempt, cap=300):
    """Retry-After may be seconds or an HTTP-date. Fall back to exponential backoff; never wait more than `cap`."""
    try:
        wait = float(header_value)
    except (TypeError, ValueError):
        try:
            wait = email.utils.parsedate_to_datetime(header_value).timestamp() - time.time()
        except (TypeError, ValueError):
            wait = 2 ** attempt
    return max(0.0, min(wait, cap))


def safe_name(name, max_bytes=120):
    """Truncate to max_bytes (UTF-8), keeping the extension, so '<item_id>_<name>' stays under filename limits."""
    if len(name.encode("utf-8")) <= max_bytes:
        return name
    stem, dot, ext = name.rpartition(".")
    ext = (dot + ext) if dot and len(ext) <= 10 else ""
    stem = name[: len(name) - len(ext)] if ext else name
    budget = max_bytes - len(ext.encode("utf-8"))
    return stem.encode("utf-8")[:budget].decode("utf-8", "ignore") + ext


def dedupe_last(items):
    """Delta can return the same item more than once across pages; keep the last occurrence of each id."""
    seen = {}
    for it in items:
        seen[it["id"]] = it
    return list(seen.values())

# --- end helpers ----------------------------------------------------------------------------------------

# COMMAND ----------

assert SITE_ID, "Set the site_id parameter (see scripts/get_site_id.sh)"
valid_graph_id(SITE_ID, "site_id")

GRAPH = "https://graph.microsoft.com/v1.0"
TENANT = dbutils.secrets.get("m365-sharepoint", "tenant_id")
CLIENT = dbutils.secrets.get("m365-sharepoint", "client_id")
SECRET = dbutils.secrets.get("m365-sharepoint", "client_secret")

VOLUME = f"/Volumes/{CATALOG}/sharepoint_bronze/raw_files"
FILES = f"{CATALOG}.sharepoint_bronze.files"
ACLS = f"{CATALOG}.sharepoint_bronze.file_acls"
STATE = f"{CATALOG}.sharepoint_bronze.delta_state"

spark.sql(f"""CREATE TABLE IF NOT EXISTS {FILES} (
  item_id STRING, drive_id STRING, site_id STRING, name STRING, path STRING,
  web_url STRING, size BIGINT, modified STRING, is_deleted BOOLEAN)""")
spark.sql(f"""CREATE TABLE IF NOT EXISTS {ACLS} (
  item_id STRING, principal_id STRING, principal_name STRING, roles STRING, captured_at TIMESTAMP)""")
spark.sql(f"CREATE TABLE IF NOT EXISTS {STATE} (drive_id STRING, delta_link STRING, updated_at TIMESTAMP)")

# COMMAND ----------

def token():
    r = requests.post(
        f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token",
        data={"client_id": CLIENT, "client_secret": SECRET,
              "scope": "https://graph.microsoft.com/.default",
              "grant_type": "client_credentials"},
        timeout=60)
    r.raise_for_status()
    return r.json()["access_token"]


session = requests.Session()
session.headers["Authorization"] = f"Bearer {token()}"


def graph_get(url, **kw):
    """GET with Retry-After handling for 429/503 and token refresh on 401. Returns the response (caller checks status)."""
    for attempt in range(8):
        r = session.get(url, timeout=120, **kw)
        if r.status_code in (429, 503):
            time.sleep(retry_after_seconds(r.headers.get("Retry-After"), attempt))
            continue
        if r.status_code == 401:
            session.headers["Authorization"] = f"Bearer {token()}"
            continue
        return r
    raise RuntimeError(f"Graph call kept failing after retries: {url}")


def graph_pages(url):
    """Yield every item of a paged Graph collection; raise on any non-2xx response."""
    while url:
        r = graph_get(url)
        r.raise_for_status()
        body = r.json()
        yield from body.get("value", [])
        url = body.get("@odata.nextLink")


def drive_delta(drive_id):
    """Return (changed items, new deltaLink, is_full_enumeration) for one document library."""
    start = f"{GRAPH}/drives/{drive_id}/root/delta"
    saved = spark.sql(f"SELECT delta_link FROM {STATE} WHERE drive_id = '{sql_str(drive_id)}'").collect()
    url, items, link, full, reset_done = (saved[0].delta_link if saved else start), [], None, not saved, False
    while url:
        r = graph_get(url, headers={"Prefer": "hierarchicalsharing"})
        if r.status_code == 410:            # delta token expired -> one full resync, then give up
            if reset_done:
                r.raise_for_status()
            url, items, full, reset_done = start, [], True, True
            continue
        r.raise_for_status()
        body = r.json()
        items += body["value"]
        url, link = body.get("@odata.nextLink"), body.get("@odata.deltaLink", link)
    return dedupe_last(items), link, full


def download(drive_id, item_id, dest):
    r = graph_get(f"{GRAPH}/drives/{drive_id}/items/{item_id}/content", stream=True)
    r.raise_for_status()
    part = dest + ".part"
    with open(part, "wb") as f:             # stream; never hold a whole file in memory
        for chunk in r.iter_content(8 * 1024 * 1024):
            f.write(chunk)
    dbutils.fs.mv(part, dest)


def item_acl_rows(drive_id, item_id):
    rows = []
    for p in graph_pages(f"{GRAPH}/drives/{drive_id}/items/{item_id}/permissions"):
        for ident in p.get("grantedToIdentitiesV2") or [p.get("grantedToV2") or {}]:
            who = ident.get("group") or ident.get("siteGroup") or ident.get("user") or {}
            rows.append({"item_id": item_id, "principal_id": who.get("id"), "principal_name": who.get("displayName"),
                         "roles": ",".join(p.get("roles", [])), "captured_at": datetime.now(timezone.utc)})
    return rows


def remove_items(drive_id, item_ids):
    """Delete table rows and Volume files for items that no longer exist in SharePoint."""
    ids = list(item_ids)
    if not ids:
        return
    spark.createDataFrame([(i,) for i in ids], "item_id string").createOrReplaceTempView("gone")
    for row in spark.sql(f"SELECT path FROM {FILES} WHERE drive_id = '{sql_str(drive_id)}' "
                         f"AND item_id IN (SELECT item_id FROM gone) AND path IS NOT NULL").collect():
        try:
            dbutils.fs.rm(row.path)
        except Exception as e:              # already gone is fine
            print(f"WARN could not remove {row.path}: {e}")
    spark.sql(f"DELETE FROM {FILES} WHERE item_id IN (SELECT item_id FROM gone)")
    spark.sql(f"DELETE FROM {ACLS} WHERE item_id IN (SELECT item_id FROM gone)")


def replace_acls(rows, item_ids):
    spark.createDataFrame([(i,) for i in item_ids], "item_id string").createOrReplaceTempView("acl_ids")
    spark.sql(f"DELETE FROM {ACLS} WHERE item_id IN (SELECT item_id FROM acl_ids)")
    if rows:
        spark.createDataFrame(rows, schema=spark.table(ACLS).schema).write.mode("append").saveAsTable(ACLS)

# COMMAND ----------

resp = graph_get(f"{GRAPH}/sites/{SITE_ID}/drives")
resp.raise_for_status()
drives = resp.json().get("value", [])
if not drives:
    raise RuntimeError(f"Site {SITE_ID} returned no document libraries: check the site ID and the Sites.Selected grant")

# COMMAND ----------

# --- ACL backfill mode: fetch ACLs for files already ingested, then stop -------------------------------
if ACL_BACKFILL:
    total = 0
    for d in drives:
        drive_id = valid_graph_id(d["id"], "drive id")
        ids = [r.item_id for r in spark.sql(
            f"SELECT item_id FROM {FILES} WHERE drive_id = '{sql_str(drive_id)}' AND NOT is_deleted").collect()]
        batch_rows, batch_ids = [], []
        for item_id in ids:
            batch_rows += item_acl_rows(drive_id, item_id)
            batch_ids.append(item_id)
            if len(batch_ids) >= 500:       # write in batches so a long run makes progress
                replace_acls(batch_rows, batch_ids); total += len(batch_rows); batch_rows, batch_ids = [], []
        if batch_ids:
            replace_acls(batch_rows, batch_ids); total += len(batch_rows)
    dbutils.notebook.exit(f"ACL backfill complete: {total} ACL entries")

# COMMAND ----------

total_changes = total_acls = 0
failures = []

for d in drives:
    drive_id = valid_graph_id(d["id"], "drive id")
    items, link, full = drive_delta(drive_id)
    meta_rows, acl_rows, deleted_ids, seen_ids, touched_ids = [], [], [], set(), []
    drive_failures = 0

    for it in items:
        if "deleted" in it:
            deleted_ids.append(it["id"])
            continue
        if "file" not in it:
            continue                        # skip folders
        seen_ids.add(it["id"])
        dest = f"{VOLUME}/{SITE_ID}/{drive_id}/{it['id']}_{safe_name(it['name'])}"
        try:
            dbutils.fs.mkdirs(dest.rsplit("/", 1)[0])
            download(drive_id, it["id"], dest)
            if CAPTURE_ACLS:
                acl_rows += item_acl_rows(drive_id, it["id"])
        except Exception as e:              # one bad item must not abort a long load; but the run will fail at the end
            drive_failures += 1
            failures.append(f"{drive_id}/{it['id']}: {type(e).__name__}: {e}")
            continue
        touched_ids.append(it["id"])
        meta_rows.append({"item_id": it["id"], "drive_id": drive_id, "site_id": SITE_ID, "name": it["name"],
                          "path": dest, "web_url": it.get("webUrl"), "size": it.get("size"),
                          "modified": it.get("lastModifiedDateTime"), "is_deleted": False})

    # deletions announced by delta (or, after a full enumeration, anything we hold that is no longer there)
    remove_ids = set(deleted_ids)
    if full:
        held = {r.item_id for r in spark.sql(
            f"SELECT item_id FROM {FILES} WHERE drive_id = '{sql_str(drive_id)}' AND NOT is_deleted").collect()}
        remove_ids |= (held - seen_ids)
    remove_items(drive_id, remove_ids)

    if meta_rows:
        new_paths = {m["item_id"]: m["path"] for m in meta_rows}   # a rename changes the path: drop the old copy
        spark.createDataFrame([(i,) for i in new_paths], "item_id string").createOrReplaceTempView("touched")
        for r in spark.sql(f"SELECT item_id, path FROM {FILES} WHERE item_id IN (SELECT item_id FROM touched) "
                           f"AND path IS NOT NULL").collect():
            if r.path != new_paths[r.item_id]:
                try:
                    dbutils.fs.rm(r.path)
                except Exception as e:
                    print(f"WARN could not remove {r.path}: {e}")
        spark.createDataFrame(meta_rows, schema=spark.table(FILES).schema).createOrReplaceTempView("chg")
        spark.sql(f"""MERGE INTO {FILES} t USING chg s ON t.item_id = s.item_id
          WHEN MATCHED THEN UPDATE SET *
          WHEN NOT MATCHED THEN INSERT *""")
        if CAPTURE_ACLS:                    # replace ACLs only for items whose ACLs we actually fetched
            replace_acls(acl_rows, touched_ids)

    # persist the new deltaLink only when this drive ran cleanly, so failed items are re-delivered next run
    if link and drive_failures == 0:
        spark.sql(f"""MERGE INTO {STATE} t
          USING (SELECT '{sql_str(drive_id)}' drive_id, '{sql_str(link)}' delta_link, current_timestamp() updated_at) s
          ON t.drive_id = s.drive_id
          WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *""")
    total_changes += len(items)
    total_acls += len(acl_rows)

print(f"Processed {total_changes} changes across {len(drives)} drive(s); {total_acls} ACL entries; {len(failures)} failure(s)")
if failures:
    raise RuntimeError(f"{len(failures)} item(s) failed (deltaLink not advanced for the affected drives). First errors: {failures[:5]}")
