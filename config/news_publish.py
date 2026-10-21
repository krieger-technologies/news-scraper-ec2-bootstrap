# Builds the small site-facing payloads from the full detail archive.
# Pure functions over lists of record dicts (stdlib only) so they can be
# checked locally against a snapshot without running the scraper.
from datetime import datetime, timedelta, timezone

LATEST_ROW_COUNT = 100
TOP_TARGET_COUNT = 10
# Date-range windows offered by the site's dropdown; None = all time
WINDOWS = {"7d": 7, "30d": 30, "60d": 60, "90d": 90, "all": None}


def _parse_date(value):
    # alert_date is stored as "YYYY-MM-DDTHH:MM:SSZ"
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _clean_topic(value):
    return value.strip() if isinstance(value, str) else ""


def build_latest(records, n=LATEST_ROW_COUNT):
    # newest first; missing/unparseable dates sink to the bottom.
    # sorted() is stable, so same-timestamp rows keep archive order (matches the site's old table sort)
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    ranked = sorted(records, key=lambda r: _parse_date(r.get("alert_date")) or epoch, reverse=True)
    return ranked[:n]


def _top_targets(rows):
    # mirrors the site's old chartRows: skip untagged rows, top 10 by count
    counts = {}
    for row in rows:
        topic = _clean_topic(row.get("target"))
        if not topic or topic.lower() == "null" or topic == "—":
            continue
        entry = counts.setdefault(topic, {"count": 0, "states": set()})
        entry["count"] += 1
        state = row.get("state") or "—"
        if state != "—":
            entry["states"].add(state)
    ranked = sorted(counts.items(), key=lambda kv: kv[1]["count"], reverse=True)
    return [
        {
            "target": topic,
            "count_of_news_articles": v["count"],
            "states": ", ".join(sorted(v["states"])),
        }
        for topic, v in ranked[:TOP_TARGET_COUNT]
    ]


def _state_counts(rows):
    # mirrors the site's old mapRows: untagged rows still count toward a state
    states = {}
    for row in rows:
        state = row.get("state") or ""
        abbrev = row.get("state_abbrev")
        if not state or not abbrev:
            continue
        entry = states.setdefault(abbrev, {"state": state, "count": 0, "topics": set()})
        entry["count"] += 1
        topic = _clean_topic(row.get("target"))
        if topic and topic.lower() != "null":
            entry["topics"].add(topic)
    return [
        {"abbrev": abbrev, "state": v["state"], "count": v["count"], "topics": ", ".join(sorted(v["topics"]))}
        for abbrev, v in states.items()
    ]


def build_summary(records, now=None):
    # Windows are anchored to the run: start = midnight (UTC) N days before the run date
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    windows = {}
    for name, days in WINDOWS.items():
        if days is None:
            rows = records
        else:
            start = today - timedelta(days=days)
            rows = [r for r in records if (d := _parse_date(r.get("alert_date"))) and d >= start]
        windows[name] = {
            "article_count": len(rows),
            "top_targets": _top_targets(rows),
            "states": _state_counts(rows),
        }
    return {
        "generated_utc": now.isoformat(),
        "window_anchor_utc": today.isoformat(),
        "total_articles": len(records),
        "windows": windows,
    }
