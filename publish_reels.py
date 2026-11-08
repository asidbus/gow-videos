#!/usr/bin/env python3
"""
GitHub Actions Cloud Publisher for Instagram Reels.
Autonomously checks queue.json, uploads due reels to Instagram via Graph API,
posts hashtag first comment, notifies Telegram, and persists status to GitHub.
"""

import os
import sys
import json
import time
import argparse
import datetime
import requests
import subprocess

API_BASE = "https://graph.instagram.com/v21.0"
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

def log(*args):
    now = datetime.datetime.now(IST).strftime("%d %b %H:%M:%S")
    print(f"[{now} IST] [CloudPublisher]", *args, flush=True)

def load_accounts_config() -> dict:
    """Loads account tokens from environment variables."""
    accounts = {}
    
    # 1. Check ACCOUNTS_JSON (JSON string containing accounts dict)
    accs_json_str = os.environ.get("ACCOUNTS_JSON", "").strip()
    if accs_json_str:
        try:
            accounts = json.loads(accs_json_str)
            log(f"Loaded {len(accounts)} accounts from ACCOUNTS_JSON")
        except Exception as e:
            log(f"Error parsing ACCOUNTS_JSON: {e}")

    # 2. Check individual account tokens like IG_TOKEN_CITYKAHANI, IG_TOKEN_HEYLTU, etc.
    for k, v in os.environ.items():
        if k.startswith("IG_TOKEN_") and v.strip():
            acc_name = k.replace("IG_TOKEN_", "").lower()
            if acc_name not in accounts:
                accounts[acc_name] = {}
            accounts[acc_name]["ig_token"] = v.strip()
            accounts[acc_name]["username"] = acc_name
            log(f"Loaded token for @{acc_name} from env {k}")

    # 3. Fallback generic IG_TOKEN
    default_token = os.environ.get("IG_TOKEN", "").strip()
    if default_token:
        default_acc = os.environ.get("DEFAULT_ACCOUNT", "citykahani").lower()
        if default_acc not in accounts:
            accounts[default_acc] = {}
        if not accounts[default_acc].get("ig_token"):
            accounts[default_acc]["ig_token"] = default_token
            accounts[default_acc]["username"] = default_acc
            log(f"Assigned default IG_TOKEN to @{default_acc}")

    return accounts

def send_telegram_alert(message: str):
    """Sends notification to Telegram chat if credentials are present."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not bot_token or not chat_id:
        return

    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": False
        }
        r = requests.post(url, json=payload, timeout=15)
        if r.status_code == 200:
            log("Telegram alert sent successfully")
        else:
            log(f"Telegram alert response {r.status_code}: {r.text[:100]}")
    except Exception as e:
        log(f"Failed to send Telegram alert: {e}")

def post_reel_to_instagram(token: str, video_url: str, caption: str) -> str:
    """Posts a video reel using Instagram Graph API."""
    log(f"Creating media container from CDN URL: {video_url}")
    create_url = f"{API_BASE}/me/media"
    payload = {
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption,
        "share_to_feed": "true",
        "access_token": token
    }
    r = requests.post(create_url, data=payload, timeout=90)
    res = r.json()
    if "id" not in res:
        raise RuntimeError(f"Media container creation failed: {res}")

    creation_id = res["id"]
    log(f"Container created (ID: {creation_id}). Polling for encoding completion...")

    # Poll status
    for attempt in range(45):
        time.sleep(10)
        status_url = f"{API_BASE}/{creation_id}"
        s = requests.get(status_url, params={"fields": "status_code,status", "access_token": token}, timeout=45).json()
        code = s.get("status_code")
        if code == "FINISHED":
            log("Instagram video processing FINISHED")
            break
        if code in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram encoding error: {s}")
        if attempt % 3 == 0:
            log(f"Still encoding... (attempt {attempt+1}/45, status: {code})")
    else:
        raise RuntimeError("Instagram video processing timed out after 7.5 minutes")

    log("Publishing media container to profile feed...")
    publish_url = f"{API_BASE}/me/media_publish"
    p = requests.post(publish_url, data={"creation_id": creation_id, "access_token": token}, timeout=90).json()
    if "id" not in p:
        raise RuntimeError(f"Publish failed: {p}")

    media_id = p["id"]
    log(f"Reel successfully published! Media ID: {media_id}")
    return media_id

def post_first_comment(media_id: str, comment_text: str, token: str) -> str:
    """Posts hashtag block as the first comment on the reel."""
    try:
        url = f"{API_BASE}/{media_id}/comments"
        r = requests.post(url, data={"message": comment_text, "access_token": token}, timeout=45)
        res = r.json()
        if "id" in res:
            log(f"First comment posted (Comment ID: {res['id']})")
            return res["id"]
        else:
            log(f"First comment failed: {res}")
    except Exception as e:
        log(f"First comment exception: {e}")
    return None

def process_due_items(queue_path: str, force_id: str = None) -> list:
    """Finds and publishes due items in queue.json."""
    if not os.path.exists(queue_path):
        log(f"Queue file not found at {queue_path}")
        return []

    with open(queue_path, "r", encoding="utf-8") as f:
        queue_data = json.load(f)

    accounts = load_accounts_config()
    now_ist = datetime.datetime.now(IST)
    now_str = now_ist.strftime("%Y-%m-%d %H:%M")
    gh_user = os.environ.get("GITHUB_REPOSITORY_OWNER") or os.environ.get("GH_USER", "asidbus")
    repo_name = os.environ.get("GITHUB_REPOSITORY", f"{gh_user}/gow-videos").split("/")[-1]

    log(f"Checking queue at current IST: {now_str} (Repository: {gh_user}/{repo_name})")

    published_items = []

    for item in queue_data.get("items", []):
        item_id = item.get("id")
        status = item.get("status")

        if force_id:
            if item_id != force_id:
                continue
        else:
            if status not in ("ready", "scheduled"):
                continue
            sched = item.get("scheduled_for", "")
            if not sched or sched > now_str:
                continue

        account_name = (item.get("account") or "citykahani").lower()
        acc_info = accounts.get(account_name, {})
        token = acc_info.get("ig_token") or os.environ.get("IG_TOKEN", "")

        if not token:
            log(f"ERROR: No Instagram token found for account '@{account_name}'. Skipping item '{item_id}'")
            continue

        log(f"🚀 Ready to publish Ep #{item.get('ep_num')} '{item.get('title')}' for @{account_name}")

        # Construct public GitHub CDN URL
        video_filename = item.get("video_filename") or f"{item_id}.mp4"
        cdn_url = f"https://raw.githubusercontent.com/{gh_user}/{repo_name}/main/{video_filename}"
        caption = item.get("caption") or item.get("title", "")

        try:
            media_id = post_reel_to_instagram(token, cdn_url, caption)
            item["status"] = "posted"
            item["posted_at"] = now_ist.isoformat()
            item["instagram_media_id"] = media_id

            # Post first comment if present
            first_comment = item.get("first_comment")
            if first_comment:
                time.sleep(5)
                cid = post_first_comment(media_id, first_comment, token)
                if cid:
                    item["first_comment_id"] = cid

            published_items.append(item)

            # Send Telegram Alert
            alert_msg = (
                f"🎉 <b>Reel Published on Instagram!</b>\n\n"
                f"👤 <b>Account:</b> @{account_name}\n"
                f"🎬 <b>Episode:</b> #{item.get('ep_num')} - {item.get('title')}\n"
                f"🆔 <b>Media ID:</b> <code>{media_id}</code>\n"
                f"⏰ <b>Published At:</b> {now_str} IST\n\n"
                f"🔗 <a href='https://www.instagram.com/{account_name}/'>View on Instagram</a>"
            )
            send_telegram_alert(alert_msg)

        except Exception as e:
            log(f"❌ Failed to publish item '{item_id}': {e}")
            item["status"] = "failed"
            item["last_error"] = str(e)
            send_telegram_alert(f"⚠️ <b>Publish Failed for @{account_name}</b>\n\nEpisode: {item.get('title')}\nError: <code>{e}</code>")

    if published_items:
        with open(queue_path, "w", encoding="utf-8") as f:
            json.dump(queue_data, f, indent=2, ensure_ascii=False)
        log(f"Queue updated with {len(published_items)} newly published reels.")

    return published_items

def main():
    parser = argparse.ArgumentParser(description="GitHub Actions Reels Publisher")
    parser.add_argument("--queue", default="queue.json", help="Path to queue.json")
    parser.add_argument("--force-id", help="Force publish specific item ID")
    args = parser.parse_args()

    published = process_due_items(args.queue, force_id=args.force_id)
    if published:
        print(f"::set-output name=published_count::{len(published)}")
        sys.exit(0)
    else:
        log("No items due for publishing right now.")
        sys.exit(0)

if __name__ == "__main__":
    main()
