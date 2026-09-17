"""
Runs inside GitHub Actions. Checks queue.json for due carousel posts,
publishes them to Instagram, and commits the updated queue.json back to
the repo so state persists between runs.

Secrets (INSTAGRAM_ACCOUNT_ID, INSTAGRAM_LONG_LIVED_TOKEN) come from
GitHub Actions repository secrets, injected as environment variables —
never stored in this file or in the repo.
"""
import json
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

TZ = ZoneInfo("Asia/Kolkata")
QUEUE_PATH = "queue.json"
LOG_PATH = "run_log.txt"
API_VERSION = "v21.0"
BASE = f"https://graph.instagram.com/{API_VERSION}"


def log(msg):
    line = f"[{datetime.now(TZ).isoformat()}] {msg}"
    print(line)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def post_carousel(image_urls, caption):
    account_id = os.environ["INSTAGRAM_ACCOUNT_ID"]
    token = os.environ["INSTAGRAM_LONG_LIVED_TOKEN"]

    child_ids = []
    for url in image_urls:
        create = requests.post(
            f"{BASE}/{account_id}/media",
            data={"image_url": url, "is_carousel_item": "true", "access_token": token},
        ).json()
        if "error" in create:
            raise RuntimeError(f"carousel item creation failed: {create['error']}")
        child_ids.append(create["id"])

    carousel = requests.post(
        f"{BASE}/{account_id}/media",
        data={
            "media_type": "CAROUSEL",
            "children": ",".join(child_ids),
            "caption": caption,
            "access_token": token,
        },
    ).json()
    if "error" in carousel:
        raise RuntimeError(f"carousel container creation failed: {carousel['error']}")

    publish = requests.post(
        f"{BASE}/{account_id}/media_publish",
        data={"creation_id": carousel["id"], "access_token": token},
    ).json()
    if "error" in publish:
        raise RuntimeError(f"publish failed: {publish['error']}")

    return publish.get("id")


def main():
    with open(QUEUE_PATH) as f:
        queue = json.load(f)

    now = datetime.now(TZ)
    changed = False

    for entry in queue:
        if entry.get("status") != "pending":
            continue
        scheduled_for = datetime.fromisoformat(entry["scheduled_for"])
        if scheduled_for > now:
            continue

        log(f"Posting {entry['id']} ({entry['type']}), scheduled for {entry['scheduled_for']}...")
        changed = True
        try:
            if entry["type"] == "carousel":
                media_id = post_carousel(entry["image_urls"], entry["caption"])
            else:
                raise RuntimeError(f"unsupported type in cloud runner: {entry['type']}")

            entry["status"] = "posted"
            entry["posted_at"] = now.isoformat()
            entry["media_id"] = media_id
            log(f"  -> posted, media id {media_id}")
        except Exception as e:
            entry["status"] = "failed"
            entry["error"] = str(e)
            log(f"  -> FAILED: {e}")
            log(traceback.format_exc())

    if changed:
        with open(QUEUE_PATH, "w") as f:
            json.dump(queue, f, indent=2, default=str)
        print("QUEUE_CHANGED=true")
    else:
        log("Nothing due.")
        print("QUEUE_CHANGED=false")

    # Low-queue check: warn once a day (not every 30 min) if the buffer
    # of future pending posts is running thin.
    if now.hour == 9 and now.minute < 30:
        pending_future = [
            e for e in queue
            if e.get("status") == "pending" and datetime.fromisoformat(e["scheduled_for"]) >= now
        ]
        days_left = len(pending_future)
        if days_left <= 7:
            last_date = max((e["scheduled_for"][:10] for e in pending_future), default="none")
            log(f"LOW QUEUE WARNING: only {days_left} day(s) of posts remain (through {last_date}). Ask Claude to top up the queue.")


if __name__ == "__main__":
    main()
