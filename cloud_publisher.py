#!/usr/bin/env python3
import os, sys, json, time, datetime, subprocess, re
import requests

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
API_BASE = "https://graph.instagram.com/v21.0"
GH_USER = os.environ.get("GH_USER", "asidbus")
GH_REPO = os.environ.get("GH_REPO", "gow-videos")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
QUEUE_PATH = os.path.join(SCRIPT_DIR, "queue.json")

def log(*args):
    print(time.strftime("%d %b %H:%M:%S"), "[CloudPublisher]", *args, flush=True)

def get_ig_token(account: str = None) -> str:
    acc = (account or "").lower().strip().lstrip("@")
    clean_acc = re.sub(r"[^A-Za-z0-9]", "_", acc).upper()

    # 1. Check account-specific env var (e.g. IG_TOKEN_APNAAWADH, IG_TOKEN_LUCKNOWLOSTBOY)
    if clean_acc:
        tok = os.environ.get(f"IG_TOKEN_{clean_acc}", "").strip()
        if tok:
            return tok

    # 2. Check general IG_TOKEN env var (matches default or single account setup)
    default_tok = os.environ.get("IG_TOKEN", "").strip()
    if acc in ("", "lucknowlostboy") and default_tok:
        return default_tok

    # 3. Check config.json fallback if present
    cfg_path = os.path.join(SCRIPT_DIR, "config.json")
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                c = json.load(f)
                accs = c.get("ACCOUNTS", {})
                if acc and acc in accs:
                    return accs[acc].get("ig_token", "").strip()
                if not acc:
                    return c.get("IG_TOKEN", "").strip()
        except Exception:
            pass

    # 4. If nothing else matched, use default_tok as last resort
    if default_tok:
        return default_tok

    # 5. Local token file fallback
    tok_file = os.path.join(SCRIPT_DIR, "ig_token.txt")
    if os.path.exists(tok_file):
        try:
            with open(tok_file, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            pass
    return ""

def load_queue() -> dict:
    if os.path.exists(QUEUE_PATH):
        with open(QUEUE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"schedule": ["09:00", "19:30"], "items": []}

def save_queue(q: dict):
    with open(QUEUE_PATH, "w", encoding="utf-8") as f:
        json.dump(q, f, indent=2, ensure_ascii=False)

def send_telegram_alert(text: str):
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not bot_token or not chat_id:
        return
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        requests.post(url, json=payload, timeout=20)
    except Exception as e:
        log(f"Telegram alert warning: {e}")

def post_reel_to_instagram(video_url: str, caption: str, token: str) -> str:
    log("Creating Instagram Reels media container...")
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
    log(f"Container created (ID: {creation_id}). Polling processing status...")

    for attempt in range(40):
        time.sleep(10)
        status_url = f"{API_BASE}/{creation_id}"
        s = requests.get(status_url, params={"fields": "status_code,status", "access_token": token}, timeout=45).json()
        code = s.get("status_code")
        if code == "FINISHED":
            log("Video processing FINISHED on Instagram servers")
            break
        if code in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram video encoding error: {s}")
    else:
        raise RuntimeError("Instagram video processing timed out")

    log("Publishing media container...")
    publish_url = f"{API_BASE}/me/media_publish"
    p = requests.post(publish_url, data={"creation_id": creation_id, "access_token": token}, timeout=90).json()
    if "id" not in p:
        raise RuntimeError(f"Publish failed: {p}")

    media_id = p["id"]
    log(f"Reel successfully published! Media ID: {media_id}")
    return media_id

def post_first_comment(media_id: str, comment_text: str, token: str) -> str:
    if not comment_text or not media_id:
        return None
    log(f"Posting algorithm hashtags as first comment on Reel {media_id}...")
    try:
        url = f"{API_BASE}/{media_id}/comments"
        payload = {"message": comment_text, "access_token": token}
        r = requests.post(url, data=payload, timeout=45)
        res = r.json()
        if r.status_code in (200, 201) and "id" in res:
            comment_id = res["id"]
            log(f"First comment posted! Comment ID: {comment_id}")
            return comment_id
        else:
            log(f"First comment response warning: {r.status_code} {res}")
            return None
    except Exception as e:
        log(f"Failed to post first comment: {e}")
        return None

def commit_and_push():
    log("Committing updated queue.json to repository...")
    try:
        subprocess.run(["git", "config", "user.name", "Cloud Publisher Bot"], check=False)
        subprocess.run(["git", "config", "user.email", "cloud-bot@github-actions.local"], check=False)
        subprocess.run(["git", "add", "queue.json"], check=False)
        r = subprocess.run(["git", "commit", "-m", "Auto-published scheduled reel [skip ci]"], capture_output=True, text=True)
        if r.returncode == 0:
            subprocess.run(["git", "push"], check=True)
            log("Updated queue.json successfully pushed to GitHub repository")
        else:
            log("No changes to commit or commit skipped")
    except Exception as e:
        log(f"Git commit/push warning: {e}")

def main():
    now_ist = datetime.datetime.now(IST)
    now_str = now_ist.strftime("%Y-%m-%d %H:%M")
    log(f"Running Cloud Reel Publisher Check at {now_str} IST...")

    q = load_queue()
    items = q.get("items", [])
    due_items = [it for it in items if it.get("status") == "ready" and it.get("scheduled_for", "") <= now_str]

    if not due_items:
        log(f"No reels currently due for publishing. (Upcoming: {len([it for it in items if it.get('status') == 'ready'])})")
        return

    published_count = 0
    for item in due_items:
        acc_name = item.get("account") or "lucknowlostboy"
        token = get_ig_token(acc_name)
        if not token:
            log(f"ERROR: No Instagram Access Token found for account @{acc_name}! Please configure IG_TOKEN or IG_TOKEN_{acc_name.upper()}.")
            item["status"] = "failed"
            item["error"] = f"Missing token for @{acc_name}"
            continue

        log(f"Processing due reel: Ep #{item.get('ep_num')} '{item.get('title')}' for @{acc_name} (Scheduled: {item.get('scheduled_for')})")
        video_filename = item.get("video_filename") or f"{item.get('id')}.mp4"
        cdn_url = f"https://raw.githubusercontent.com/{GH_USER}/{GH_REPO}/main/{video_filename}"

        # Verify video availability
        try:
            head = requests.head(cdn_url, timeout=20, allow_redirects=True)
            if head.status_code != 200:
                log(f"Warning: CDN URL returned status {head.status_code}: {cdn_url}")
        except Exception as e:
            log(f"CDN check error: {e}")

        try:
            caption = item.get("caption") or item.get("title", "")
            media_id = post_reel_to_instagram(cdn_url, caption, token)

            # Post first comment with algorithm hashtags
            first_comment = item.get("first_comment", "")
            comment_id = None
            if first_comment:
                time.sleep(5)
                comment_id = post_first_comment(media_id, first_comment, token)

            # Update item in queue
            item["status"] = "posted"
            item["posted_at"] = datetime.datetime.now(IST).isoformat()
            item["instagram_media_id"] = media_id
            item["first_comment_id"] = comment_id
            published_count += 1

            send_telegram_alert(
                f"🎉 <b>Cloud Publisher: Reel Published to Instagram!</b>\n\n"
                f"• <b>Title:</b> {item.get('title')}\n"
                f"• <b>Account:</b> @{acc_name}\n"
                f"• <b>Media ID:</b> <code>{media_id}</code>\n"
                f"• <b>First Comment ID:</b> <code>{comment_id}</code>\n"
                f"• <b>Published At:</b> {now_str} IST"
            )

        except Exception as e:
            log(f"Publishing failed for item '{item.get('id')}': {e}")
            item["status"] = "failed"
            item["error"] = str(e)

    if published_count > 0:
        save_queue(q)
        commit_and_push()
        log(f"Published {published_count} reels in this cloud run.")

if __name__ == "__main__":
    main()
