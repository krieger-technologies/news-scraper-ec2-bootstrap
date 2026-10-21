# scraper for news articles to aggregate banned items and highlights
import os
import sys
import time
# Fail fast if run outside the 3.11 project venv (e.g. bare `python3`)
if sys.version_info < (3, 11):
    raise SystemExit(
        f"news_scraper.py requires Python >= 3.11 (running {sys.version.split()[0]}).\n"
        f"Run it via the project venv:\n"
        f"  /home/ec2-user/home/ETL-project/venv/bin/python etl/news_scraper.py"
    )

from dotenv import load_dotenv
import boto3
import json
import socket # package for email retrieval timeout
socket.setdefaulttimeout(60)
import signal # package for python operation timeouts

# data manipulation
import pandas as pd
import re
from datetime import datetime, timedelta, timezone
import argparse

parser = argparse.ArgumentParser()
parser.add_argument('--full-refresh', action='store_true', help='Pull full Gmail history instead of last 3 days')
args = parser.parse_args()

# email packages
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ENV_DIR = os.path.join(BASE_DIR, "env")
TOKEN_PATH = os.path.join(ENV_DIR, "token.json")
creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
service = build("gmail", "v1", credentials=creds)

# web scraping
from urllib.parse import unquote, urlparse, parse_qs
from bs4 import BeautifulSoup
import requests

# graphs
# import plotly.express as px
import us

# organize later
import re
import base64
from urllib.parse import unquote, urlparse, parse_qs

# -----------------------
# Project setup
# -----------------------
print(f"[START] ETL script started at {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
sys.stdout.flush()
# Import ban_config properly
sys.path.append(os.path.join(BASE_DIR))
from config.ban_config import state_patterns, keywords
from config.news_scraper_functions import extract_state, extract_datasource, clean_headline, COMPILED_PATTERNS, extract_target_generic, find_keyword_targets, choose_target, safe_get_plain, state_to_abbrev, is_noise_headline
from config.news_publish import build_latest, build_summary
from botocore.exceptions import ClientError
# imports for Gmail cutoff
from datetime import datetime, timedelta, timezone
# connect to email server and initial pull of messages and emails

def fetch_gmail_messages(full_refresh=False):
    all_messages = []
    page_token = None
    
    if full_refresh:
        query = 'subject:"Google Alert - ban"'
        print("Mode: FULL REFRESH - pulling complete Gmail history")
    else:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y/%m/%d")
        query = f'subject:"Google Alert - ban" after:{cutoff}'
        print(f"Mode: INCREMENTAL - pulling from {cutoff}")

    while True:
        response = service.users().messages().list(
            userId="me",
            q=query,
            maxResults=500,
            pageToken=page_token
        ).execute()
        all_messages.extend(response.get("messages", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return all_messages

all_messages = fetch_gmail_messages(full_refresh=args.full_refresh)

print(f"Total messages found: {len(all_messages)}")

# unpack messages
# -----------------------
# Pre-compile patterns
# -----------------------
results_pattern = re.compile(
    r"(\d+\s+new results for \[Bans\])",
    re.IGNORECASE
)

split_pattern = re.compile(
    r"(.*?)(?:\r\n\r\n|$)",
    re.DOTALL
)

line_pattern = re.compile(
    r"(.*?)(?:\r\n|$)",
    re.DOTALL
)

# -----------------------
# Helper: fetch + decode Gmail message
# -----------------------
def get_plain_text_and_date(service, msg_id):
    msg = service.users().messages().get(
        userId="me",
        id=msg_id,
        format="full"
    ).execute()

    payload = msg.get("payload", {})
    headers = payload.get("headers", [])
    parts = payload.get("parts", [])

    # Extract Date header
    alert_date = None
    for h in headers:
        if h["name"].lower() == "date":
            alert_date = h["value"]
            break

    # Extract plain text body
    for part in parts:
        if part.get("mimeType") == "text/plain":
            data = part["body"].get("data")
            if data:
                return (
                    alert_date,
                    base64.urlsafe_b64decode(data).decode(
                        "utf-8", errors="ignore"
                    )
                )

    return alert_date, None


# -----------------------
# Iterate through Gmail messages
# -----------------------
container = {
    "alert_date": [],
    "headlines": [],
    "article_links": [],
}

for m in all_messages:
    alert_date, plain_text = get_plain_text_and_date(service, m["id"])
    if not plain_text:
        continue

    # Optional debug: how many results Google reports
    match = results_pattern.search(plain_text)
    if match:
        print("Found:", match.group(1))

    # Split alert into blocks
    matches = [
        g.group(1).strip()
        for g in split_pattern.finditer(plain_text)
        if g.group(1).strip()
    ]

    # Guard against malformed alerts
    if len(matches) < 5:
        continue

    matches = matches[1:-3]

    for item in matches:
        sub_groups = [
            g.group(1).strip()
            for g in line_pattern.finditer(item)
            if g.group(1).strip()
        ]

        if not sub_groups:
            continue

        container["alert_date"].append(alert_date)
        container["headlines"].append(sub_groups[0])

        url_match = re.search(r"<(https.*?)>", sub_groups[-1])
        if url_match:
            google_url = unquote(url_match.group(1))
            parsed = urlparse(google_url)
            qs = parse_qs(parsed.query)
            container["article_links"].append(
                qs.get("url", [""])[0] or "no url found (1)."
            )
        else:
            container["article_links"].append("no url found (2).")

print('finished unpacking emails into dictionary.')
print('converting into df and organizing..')
# call cleaning and organizing functions
df = pd.DataFrame(data=container)
df["state"] = df["headlines"].apply(extract_state)
df["datasource"] = df["article_links"].apply(extract_datasource)
df["target"] = df["headlines"].apply(choose_target)
# free-text subject guess for untagged rows only (review list for new categories; not charted)
df["target_raw"] = df["headlines"].where(df["target"].isna()).apply(lambda h: extract_target_generic(h) if isinstance(h, str) else None)
df = df[['alert_date', 'state', 'target', 'target_raw', 'headlines', 'datasource', 'article_links']].sort_values(by=['state', 'target']).drop_duplicates(subset=['article_links']).reset_index(drop=True)
df['alert_date'] = pd.to_datetime(df['alert_date'], utc=True)


# --- S3 JSON export ---
print("Uploading JSON to S3...")
s3 = boto3.client(
    "s3",
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    region_name=os.getenv("AWS_REGION")
)
BUCKET = "krieger-technologies.com"
# Full history; the ETL merges into this every run (authoritative copy)
ARCHIVE_KEY = "news_data/archive/news_detail_archive.json"
# Site-facing payloads
LATEST_KEY = "news_data/news_latest.json"
SUMMARY_KEY = "news_data/news_summary.json"
META_KEY = "news_data/last_run_timestamp.json"
DATA_CACHE_CONTROL = "public, max-age=86400, stale-while-revalidate=604800"


def load_s3_json(key):
    # None only when the object doesn't exist; any other failure raises so the
    # run aborts instead of overwriting history with just the new rows
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=key)
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "NoSuchKey":
            return None
        raise
    return json.loads(obj["Body"].read().decode("utf-8"))


def put_s3_json(key, payload, label, indent=None, cache_control=DATA_CACHE_CONTROL):
    body = json.dumps(payload, indent=indent) if indent else json.dumps(payload, separators=(",", ":"))
    resp = s3.put_object(
        Bucket=BUCKET,
        Key=key,
        Body=body,
        ContentType="application/json",
        CacheControl=cache_control,
    )
    print(
        f"{label} PUT:",
        resp.get("ResponseMetadata", {}).get("HTTPStatusCode"),
        "ETag:",
        resp.get("ETag"),
        f"({len(body)} bytes)",
    )


# -- MERGE WITH EXISTING S3 DATA --
# Full refresh rebuilds from the complete Gmail history, so skip the merge
# (nothing is deleted; the uploads below overwrite in place).
if args.full_refresh:
    print("Full refresh: ignoring existing S3 data")
    existing_records = []
else:
    print("downloading existing detail archive from S3 for merge...")
    try:
        existing_records = load_s3_json(ARCHIVE_KEY)
    except Exception as e:
        print(f"ABORT: could not load existing data from S3, nothing uploaded: {e}")
        sys.exit(1)
    if existing_records is None:
        print(f"ABORT: no archive at {ARCHIVE_KEY}; restore it from an S3 object version, or run with --full-refresh to start fresh")
        sys.exit(1)
df_existing = pd.DataFrame(existing_records)
print(f"Existing records loaded: {len(df_existing)}")
# Convert DataFrame dates to ISO format
# 1 drop "National / Unknown"
df_filtered = df[df["state"] != "National / Unknown"]
# 2 drop headlines that are parsed wrong and begin with an https tag
df_filtered = df_filtered[~df_filtered["headlines"].str.startswith("<https", na=False)]
# 3 drop headlines that aren't ban stories (e.g. "Ray-Ban" products)
df_filtered = df_filtered[~df_filtered["headlines"].apply(is_noise_headline)]
df_filtered["state_abbrev"] = df_filtered["state"].apply(state_to_abbrev)
df_filtered['alert_date'] = df_filtered['alert_date'].dt.strftime('%Y-%m-%dT%H:%M:%SZ')

# --- APPEND TO EXISTING DATA ---
if not df_existing.empty:
    df_filtered = pd.concat([df_existing, df_filtered], ignore_index=True)
    df_filtered = df_filtered.drop_duplicates(subset=["article_links"]).reset_index(drop=True)
    print(f"Merged record count after dedup: {len(df_filtered)}")
else:
    print("No existing data to merge - using new records only")
# --- DETAIL DATASET (row-level, authoritative) ---
df_filtered = df_filtered.where(pd.notnull(df_filtered), None)
detail_payload = (
    df_filtered[
        [
            "alert_date",
            "state",
            "state_abbrev",
            "target",
            "target_raw",
            "headlines",
            "datasource",
            "article_links",
        ]
    ]
    .sort_values(["alert_date", "state", "target"])
    .to_dict(orient="records")
)

# merge keeps existing rows, so the archive can only grow
if len(detail_payload) < len(existing_records):
    print(f"ABORT: merged {len(detail_payload)} rows < {len(existing_records)} existing; nothing uploaded")
    sys.exit(1)

# archive first: it's the copy the next run merges into
put_s3_json(ARCHIVE_KEY, detail_payload, "ARCHIVE")
put_s3_json(LATEST_KEY, build_latest(detail_payload), "LATEST")
put_s3_json(SUMMARY_KEY, build_summary(detail_payload), "SUMMARY")

# export last time run timestamp last: a new timestamp is what makes browsers fetch the new files
metadata = {
    "last_updated_utc": datetime.now(timezone.utc).isoformat(),
    "record_count": len(df),
    "source": "email_scraper_v4"
}
put_s3_json(META_KEY, metadata, "META", indent=2, cache_control="no-cache, no-store, must-revalidate")
print("ETL reached end of script")
sys.exit(0)
print("public_news_data.json uploaded")
