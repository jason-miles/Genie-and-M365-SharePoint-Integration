# Databricks notebook source
# MAGIC %md
# MAGIC # Option C: Microsoft Graph delta ingestion with ACL capture
# MAGIC Full enumeration on the first run, then only creates / changes / deletes via the saved `deltaLink`,
# MAGIC per document library (drive). Files land in a UC Volume; metadata and ACLs land in Delta tables.
# MAGIC Prerequisite: the Entra app has `Sites.Selected` and has been granted read on the site
# MAGIC (`scripts/grant_site_access.sh`). Capturing permissions may need a higher-privileged grant: review with Security.

# COMMAND ----------

import requests, time
from datetime import datetime, timezone

dbutils.widgets.text("catalog", "m365")
dbutils.widgets.text("site_id", "")
CATALOG = dbutils.widgets.get("catalog")
SITE_ID = dbutils.widgets.get("site_id")
assert SITE_ID, "Set the site_id parameter (see scripts/get_site_id.sh)"

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
    """GET with Retry-After handling for 429/503 and token refresh on 401."""
    for attempt in range(8):
        r = session.get(url, timeout=120, **kw)
        if r.status_code in (429, 503):
            time.sleep(int(r.headers.get("Retry-After", 2 ** attempt)))
            continue
        if r.status_code == 401:
            session.headers["Authorization"] = f"Bearer {token()}"
            continue
        return r
    raise RuntimeError(f"Graph call kept failing after retries: {url}")


def drive_delta(drive_id):
    """Return (changed items, new deltaLink) for one document library."""
    start = f"{GRAPH}/drives/{drive_id}/root/delta"
    saved = spark.sql(f"SELECT delta_link FROM {STATE} WHERE drive_id = '{drive_id}'").collect()
    url, items, link = (saved[0].delta_link if saved else start), [], None
    while url:
        r = graph_get(url, headers={"Prefer": "hierarchicalsharing"})
        if r.status_code == 410:            # delta token expired -> full resync
            url, items = start, []
            continue
        r.raise_for_status()
        body = r.json()
        items += body["value"]
        url, link = body.get("@odata.nextLink"), body.get("@odata.deltaLink", link)
    return items, link


def download(drive_id, item_id, dest):
    r = graph_get(f"{GRAPH}/drives/{drive_id}/items/{item_id}/content", stream=True)
    r.raise_for_status()
    with open(dest, "wb") as f:             # stream: never hold a whole file in memory
        for chunk in r.iter_content(8 * 1024 * 1024):
            f.write(chunk)

# COMMAND ----------

total_changes = total_acls = 0
drives = graph_get(f"{GRAPH}/sites/{SITE_ID}/drives").json()["value"]   # every library on the site

for d in drives:
    drive_id = d["id"]
    items, link = drive_delta(drive_id)
    meta_rows, acl_rows = [], []

    for it in items:
        if "file" not in it and "deleted" not in it:
            continue                        # skip folders
        if "deleted" in it:
            meta_rows.append({"item_id": it["id"], "drive_id": drive_id, "site_id": SITE_ID, "name": None,
                              "path": None, "web_url": None, "size": None, "modified": None,
                              "is_deleted": True})
            continue

        dest = f"{VOLUME}/{SITE_ID}/{drive_id}/{it['id']}_{it['name']}"
        dbutils.fs.mkdirs(dest.rsplit("/", 1)[0])
        download(drive_id, it["id"], dest)
        meta_rows.append({"item_id": it["id"], "drive_id": drive_id, "site_id": SITE_ID, "name": it["name"],
                          "path": dest, "web_url": it.get("webUrl"), "size": it.get("size"),
                          "modified": it.get("lastModifiedDateTime"), "is_deleted": False})

        perms = graph_get(f"{GRAPH}/drives/{drive_id}/items/{it['id']}/permissions").json()
        for p in perms.get("value", []):
            for ident in p.get("grantedToIdentitiesV2") or [p.get("grantedToV2") or {}]:
                who = ident.get("group") or ident.get("siteGroup") or ident.get("user") or {}
                acl_rows.append({"item_id": it["id"], "principal_id": who.get("id"),
                                 "principal_name": who.get("displayName"),
                                 "roles": ",".join(p.get("roles", [])),
                                 "captured_at": datetime.now(timezone.utc)})

    if meta_rows:                           # upsert metadata, then replace ACLs for every touched item
        spark.createDataFrame(meta_rows, schema=spark.table(FILES).schema).createOrReplaceTempView("chg")
        spark.sql(f"""MERGE INTO {FILES} t USING chg s ON t.item_id = s.item_id
          WHEN MATCHED AND s.is_deleted THEN DELETE
          WHEN MATCHED THEN UPDATE SET *
          WHEN NOT MATCHED AND NOT s.is_deleted THEN INSERT *""")
        spark.sql(f"DELETE FROM {ACLS} WHERE item_id IN (SELECT item_id FROM chg)")
    if acl_rows:
        (spark.createDataFrame(acl_rows, schema=spark.table(ACLS).schema)
              .write.mode("append").saveAsTable(ACLS))

    if link:                                # persist the new deltaLink only after this drive succeeded
        spark.sql(f"""MERGE INTO {STATE} t
          USING (SELECT '{drive_id}' drive_id, '{link}' delta_link, current_timestamp() updated_at) s
          ON t.drive_id = s.drive_id
          WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *""")
    total_changes += len(items)
    total_acls += len(acl_rows)

print(f"Processed {total_changes} changes across {len(drives)} drive(s); {total_acls} ACL entries")
