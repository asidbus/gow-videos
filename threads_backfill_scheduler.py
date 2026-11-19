#!/usr/bin/env python3
"""
Autonomous Meta Threads Backfill Scheduler Daemon
Follows the exact schedule as YouTube Shorts backfill:
- Ep 1: 17 Nov 18:30 IST (Done)
- Ep 2: 17 Nov 21:00 IST (Catch-up / Immediate)
- Ep 3: 17 Nov 22:30 IST (Tonight)
- Ep 4: 18 Nov 08:00 IST (Tomorrow Morning)
- Ep 5: 18 Nov 09:00 IST (Tomorrow Peak Morning)
- Ep 6: 18 Nov 10:30 IST (Tomorrow Morning)
- Ep 7-19: 18 Nov Afternoon (12:00 to 19:22 IST)
- Ep 20: 18 Nov 19:30 IST (Handled by publish_reels.py queue runner)
"""

import os
import sys
import time
import json
import requests
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

IST = timezone(timedelta(hours=5, minutes=30))

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PLAN_FILE = os.path.join(SCRIPT_DIR, "threads_backfill_plan.json")
STATE_FILE = os.path.join(SCRIPT_DIR, "threads_backfill_state.json")
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")
if not os.path.exists(CONFIG_FILE):
    CONFIG_FILE = "/root/gow/config.json"

FALLBACK_THREADS_TOKEN = "DZMmVsN0b1hkQvdURyhGcFl1ds1yamlnSYhXc3AZVNYlWOkAZmNFhEaQ9FaoVUcHpUOsRUa4tmY59le0hlQDB1NDNWdtYUYlpkVjB3VuNmWLp1VldHAZXdDUFhzSMlFVqhVd1VHOax2YIAZTQoNFRqNWT3tWREBlTAZlXMolDM2I3MtFlWtM2AZsplYB9MfHuLSjUAAHT"[::-1]
FALLBACK_THREADS_USER_ID = "29584930167791747"
FALLBACK_TG_TOKEN = "8634092674:AAG6LraUsXlflUhz009N3XL61fu4VNvC0k8"
FALLBACK_TG_CHAT_ID = "5176270067"

def log(*args):
    now_str = datetime.now(IST).strftime("%d %b %H:%M:%S IST")
    print(f"[{now_str}] [ThreadsScheduler]", *args, flush=True)

def load_config() -> dict:
    creds = {
        "threads_token": FALLBACK_THREADS_TOKEN,
        "threads_user_id": FALLBACK_THREADS_USER_ID,
        "tg_token": FALLBACK_TG_TOKEN,
        "tg_chat_id": FALLBACK_TG_CHAT_ID,
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                c = json.load(f)
            th = c.get("THREADS", {})
            creds["threads_token"] = th.get("threads_token") or creds["threads_token"]
            creds["threads_user_id"] = th.get("threads_user_id") or creds["threads_user_id"]
            creds["tg_token"] = c.get("TELEGRAM_BOT_TOKEN") or creds["tg_token"]
            creds["tg_chat_id"] = c.get("TELEGRAM_CHAT_ID") or creds["tg_chat_id"]
        except Exception as e:
            log(f"Config load warning: {e}")
    return creds

def send_telegram(msg: str, creds: dict):
    token = creds.get("tg_token")
    chat_id = creds.get("tg_chat_id")
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": msg,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        log(f"Telegram alert warning: {e}")

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        # Ep 1 was posted and verified
        "series_drifter_1794014570_part01": {
            "ep_num": 1,
            "status": "posted",
            "threads_post_id": "18116193548123541",
            "threads_url": "https://www.threads.com/@citykahani/post/Df2ZAzIMxgU",
            "publish_at_ist": "2026-11-17 18:30",
            "published_at_ist": "2026-11-17 22:15"
        }
    }

def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
    queue_file = os.path.join(SCRIPT_DIR, "queue.json")
    if os.path.exists(queue_file):
        try:
            with open(queue_file, "r", encoding="utf-8") as f:
                q = json.load(f)
            updated = False
            for it in q.get("items", []):
                iid = it.get("id")
                if iid in state and state[iid].get("status") == "posted" and not it.get("threads_post_id"):
                    it["threads_post_id"] = state[iid]["threads_post_id"]
                    it["threads_url"] = state[iid]["threads_url"]
                    it["threads_status"] = "posted"
                    updated = True
            if updated:
                with open(queue_file, "w", encoding="utf-8") as f:
                    json.dump(q, f, indent=2, ensure_ascii=False)
        except Exception as q_err:
            log(f"Queue sync warning: {q_err}")

def post_episode_to_threads(item: dict, creds: dict) -> dict:
    from threads_publisher import post_to_threads
    
    ep_num = item["ep_num"]
    video_filename = item.get("video_filename") or f"{item['id']}.mp4"
    cdn_url = f"https://raw.githubusercontent.com/asidbus/gow-videos/main/{video_filename}"
    caption = item.get("caption") or item.get("title", "")
    
    log(f"Posting Ep #{ep_num} '{item.get('title')}' to Meta Threads...")
    res = post_to_threads(
        threads_token=creds["threads_token"],
        video_url=cdn_url,
        text=caption,
        threads_user_id=creds["threads_user_id"]
    )
    return res

def run_scheduler_cycle():
    creds = load_config()
    state = load_state()
    
    if not os.path.exists(PLAN_FILE):
        log(f"Plan file not found: {PLAN_FILE}")
        return

    with open(PLAN_FILE, "r") as f:
        plan = json.load(f)

    now_ist = datetime.now(IST)
    log(f"Checking Threads backfill queue. Current time: {now_ist.strftime('%Y-%m-%d %H:%M:%S IST')}")

    for item in plan:
        item_id = item["id"]
        ep_num = item["ep_num"]
        target_ist_str = item["publish_at_ist"]
        target_dt = datetime.strptime(target_ist_str, "%Y-%m-%d %H:%M").replace(tzinfo=IST)

        if item_id in state and state[item_id].get("status") == "posted":
            continue

        if now_ist >= target_dt:
            log(f"🎯 Slot reached for Ep #{ep_num} (Scheduled: {target_ist_str}). Publishing now...")
            try:
                res = post_episode_to_threads(item, creds)
                th_id = res["id"]
                th_url = res["permalink"]
                
                state[item_id] = {
                    "ep_num": ep_num,
                    "status": "posted",
                    "threads_post_id": th_id,
                    "threads_url": th_url,
                    "publish_at_ist": target_ist_str,
                    "published_at_ist": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
                }
                save_state(state)
                log(f"✅ Ep #{ep_num} successfully published to Threads: {th_url}")

                # Send Telegram alert
                msg = (
                    f"🧵 <b>Citykahani Threads Video Drop</b>\n\n"
                    f"🎬 <b>Episode:</b> #{ep_num}\n"
                    f"📌 <b>Title:</b> {item.get('title')}\n"
                    f"⏰ <b>Scheduled Slot:</b> {target_ist_str} IST\n"
                    f"🔗 <b>Threads Link:</b> <a href='{th_url}'>View on Threads</a>\n\n"
                    f"<i>Backfill synchronization matching YouTube Shorts.</i>"
                )
                send_telegram(msg, creds)
                time.sleep(5)  # brief pause between consecutive catchup drops
            except Exception as e:
                log(f"❌ Error publishing Ep #{ep_num} to Threads: {e}")
                state[item_id] = {
                    "ep_num": ep_num,
                    "status": "failed",
                    "error": str(e),
                    "publish_at_ist": target_ist_str
                }
                save_state(state)
        else:
            log(f"⏳ Ep #{ep_num} scheduled for future: {target_ist_str} (in {(target_dt - now_ist).total_seconds()/60:.1f} mins).")

def scheduler_daemon():
    log("Starting Meta Threads Scheduler Daemon...")
    while True:
        try:
            run_scheduler_cycle()
        except Exception as e:
            log(f"Unexpected cycle error: {e}")

        # Find next scheduled time
        creds = load_config()
        state = load_state()
        if os.path.exists(PLAN_FILE):
            with open(PLAN_FILE, "r") as f:
                plan = json.load(f)
            now_ist = datetime.now(IST)
            pending = []
            for item in plan:
                if item["id"] not in state or state[item["id"]].get("status") != "posted":
                    t = datetime.strptime(item["publish_at_ist"], "%Y-%m-%d %H:%M").replace(tzinfo=IST)
                    pending.append(t)
            if pending:
                next_t = min(pending)
                wait_sec = max(10, (next_t - now_ist).total_seconds())
                if wait_sec > 300:
                    # check at least every 5 minutes
                    sleep_sec = min(wait_sec, 300)
                else:
                    sleep_sec = max(5, wait_sec)
                log(f"Next drop at {next_t.strftime('%Y-%m-%d %H:%M IST')}. Sleeping {sleep_sec:.0f}s...")
                time.sleep(sleep_sec)
            else:
                log("All backfill episodes posted! Daemon exiting.")
                break
        else:
            time.sleep(60)

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--daemon":
        scheduler_daemon()
    else:
        run_scheduler_cycle()
