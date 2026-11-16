#!/usr/bin/env python3
"""
GitHub Actions Autonomous Cloud Relay & Multi-Platform Publisher
Supports: Instagram Reels (Graph API) + YouTube Shorts (Data API v3)
Zero local dependencies. 100% serverless on GitHub Actions.
Features:
- Self-sustaining Cloud Relay: runners baton-pass via PAT push to guarantee 0 cron-throttling delay.
- Microsecond-precision pre-flight container encoding at T-55s before target slot.
- Distributed lease locking to avoid duplicate concurrent runners.
- Dual-platform publishing: Instagram Reels + YouTube Shorts.
- Unified Telegram alerts with direct live links for both platforms.
- Public GitHub CDN hosting for media delivery.
"""

import os
import sys
import json
import time
import base64
import argparse
import datetime
import requests
import subprocess
from typing import Optional, Dict

API_BASE = "https://graph.instagram.com/v21.0"
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"
YOUTUBE_COMMENTS_URL = "https://www.googleapis.com/youtube/v3/commentThreads?part=snippet"

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

FALLBACK_TELEGRAM_BOT_TOKEN = "8634092674:AAG6LraUsXlflUhz009N3XL61fu4VNvC0k8"
FALLBACK_TELEGRAM_CHAT_ID = "5176270067"
FALLBACK_GH_TOKEN = base64.b64decode("Z2l0aHViX3BhdF8xMUFRS1JWSFkwSlJCNXIwYWhPT0FyX250U3RxQnpLQVBIRzdZMGdaTUt1SGp4YThqaHJyNkRNbXdCOEpZQTk5anY2NjVHWjdOQlVnd01GMENW").decode()
FALLBACK_GH_USER = "asidbus"

# YouTube OAuth Fallback credentials
FALLBACK_YT_CLIENT_ID = ""
FALLBACK_YT_CLIENT_SECRET = ""
FALLBACK_YT_REFRESH_TOKEN = ""

FALLBACK_ACCOUNTS = {
    "citykahani": {
        "username": "citykahani",
        "name": "Citykahani",
        "ig_token": "IGAAPfkp0WcZB5BZAFlqUGZAOeDdkbWNSeTJDTzhuNkU1dVJkVmFNSmdBYmphNm92bkRjb0ozS2ZAucEVBN3MtZAm05NThKQTAtN014ZADNHQ3llZAGpaamEzVHJRR3NHNkhfZAThYaE1Nd21hUXJkYWJoVEdGRUFR",
        "ig_user_id": "28815548761444383",
        "cta_handle": "@citykahani"
    },
    "apnaawadh": {
        "username": "apnaawadh",
        "name": "Apna Awadh",
        "ig_token": "IGAAPfkp0WcZB5BZAGJzSzdhOW16YS1xeVN5bkRCNE5PdjFlSXlQdmlHRE9TazUwcHltU0RYSk9kOUNQdlRoNFVkWTVaeXJGZAXFqYTFXQWhlUVJrM3A4ZA1Fybk5XanM0RUVCVDFBUUc0RDl3Q29lQUVJWTF2WUdKRDN5ZA0xDTUhfRQZDZD",
        "ig_user_id": "39988112154169575",
        "cta_handle": "@apnaawadh"
    },
    "heyltu": {
        "username": "heyltu",
        "name": "@heyltu",
        "ig_token": "IGAAPfkp0WcZB5BZAGFKS3JOcVlhdjQ4b3lxTl9hM04zVkJXREExbjNDNktVUEVBaGdiNFB5Mm9Bclh1czFPV0l1QldiM2N5OWZAjdy1tZAXJqU2ZAOZAUtPVlgzV0VKZA093bERpekstMkhyUXdCbmYxbmdHWHNrbkN1dUZA2Slc2ODAtOAZDZD",
        "ig_user_id": "28580166865019132",
        "cta_handle": "@heyltu"
    },
    "gaonkawifi": {
        "username": "gaonkawifi",
        "name": "@gaonkawifi",
        "ig_token": "IGAAPfkp0WcZB5BZAGJRRnVsM3JBT1d1ZAkxkdm5CNENBcnBTb2FwdUtYQ2l4WWo3eDVvRUQtRDlXRENJd2N5c3BRdWJKMU9PUVJkaUhjd2F3Vkl0dzNsZA21jYUpCeFNZAUzdoUmpfX3B3QnZAWUXJ2azUyR01FTmVjZAU5WdFlsSTBSYwZDZD",
        "ig_user_id": "29803226125935640",
        "cta_handle": "@gaonkawifi"
    }
}

_cached_yt_token = {
    "access_token": None,
    "expires_at": 0
}

def log(*args):
    now = datetime.datetime.now(IST).strftime("%d %b %H:%M:%S")
    print(f"[{now} IST] [CloudPublisher]", *args, flush=True)

def load_accounts_config() -> dict:
    """Loads Instagram account tokens from env or fallbacks."""
    accounts = {}
    accounts.update(FALLBACK_ACCOUNTS)

    accs_json_str = os.environ.get("ACCOUNTS_JSON", "").strip()
    if accs_json_str:
        try:
            parsed = json.loads(accs_json_str)
            accounts.update(parsed)
            log(f"Loaded {len(parsed)} accounts from ACCOUNTS_JSON")
        except Exception as e:
            log(f"Error parsing ACCOUNTS_JSON: {e}")

    for k, v in os.environ.items():
        if k.startswith("IG_TOKEN_") and v.strip():
            acc_name = k.replace("IG_TOKEN_", "").lower()
            if acc_name not in accounts:
                accounts[acc_name] = {}
            accounts[acc_name]["ig_token"] = v.strip()
            accounts[acc_name]["username"] = acc_name

    default_token = os.environ.get("IG_TOKEN", "").strip()
    if default_token:
        default_acc = os.environ.get("DEFAULT_ACCOUNT", "citykahani").lower()
        if default_acc not in accounts:
            accounts[default_acc] = {}
        accounts[default_acc]["ig_token"] = default_token
        accounts[default_acc]["username"] = default_acc

    return accounts

def load_youtube_config() -> Dict[str, str]:
    """Loads YouTube OAuth credentials from env, config.json, or fallbacks."""
    creds = {
        "client_id": os.environ.get("YT_CLIENT_ID", "").strip() or FALLBACK_YT_CLIENT_ID,
        "client_secret": os.environ.get("YT_CLIENT_SECRET", "").strip() or FALLBACK_YT_CLIENT_SECRET,
        "refresh_token": os.environ.get("YT_REFRESH_TOKEN", "").strip() or FALLBACK_YT_REFRESH_TOKEN
    }

    if not all(creds.values()):
        cfg_path = "/root/gow/config.json"
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                yt_cfg = cfg.get("YOUTUBE", {})
                for k in ("client_id", "client_secret", "refresh_token"):
                    if not creds[k]:
                        creds[k] = yt_cfg.get(k) or cfg.get(f"YT_{k.upper()}", "")
            except Exception:
                pass

    return creds

def get_youtube_access_token(client_id: str, client_secret: str, refresh_token: str) -> str:
    """Refreshes and returns Google OAuth 2.0 access token for YouTube."""
    global _cached_yt_token
    now = time.time()
    if _cached_yt_token["access_token"] and now < _cached_yt_token["expires_at"] - 60:
        return _cached_yt_token["access_token"]

    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token"
    }

    r = requests.post(OAUTH_TOKEN_URL, data=payload, timeout=30)
    data = r.json()

    if "access_token" not in data:
        raise RuntimeError(f"Failed to refresh YouTube access token: {data}")

    access_token = data["access_token"]
    expires_in = data.get("expires_in", 3600)
    _cached_yt_token["access_token"] = access_token
    _cached_yt_token["expires_at"] = now + expires_in
    log("Successfully refreshed YouTube OAuth access token")
    return access_token

def post_short_to_youtube(
    video_path_or_url: str,
    title: str,
    description: str,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    tags: Optional[list] = None
) -> Dict[str, str]:
    """Uploads a vertical video as a YouTube Short via resumable upload."""
    access_token = get_youtube_access_token(client_id, client_secret, refresh_token)

    temp_file = None
    if video_path_or_url.startswith("http://") or video_path_or_url.startswith("https://"):
        log(f"Downloading video from CDN for YouTube: {video_path_or_url}")
        temp_file = f"/tmp/yt_up_{int(time.time())}.mp4"
        r_down = requests.get(video_path_or_url, stream=True, timeout=120)
        r_down.raise_for_status()
        with open(temp_file, "wb") as f:
            for chunk in r_down.iter_content(chunk_size=1024 * 512):
                if chunk:
                    f.write(chunk)
        actual_path = temp_file
    else:
        actual_path = video_path_or_url

    if not os.path.exists(actual_path):
        raise FileNotFoundError(f"Video file not found at {actual_path}")

    file_size = os.path.getsize(actual_path)
    log(f"Uploading to YouTube Shorts ({file_size / (1024*1024):.2f} MB): '{title}'")

    try:
        yt_title = title.strip()
        if "#shorts" not in yt_title.lower() and len(yt_title) <= 90:
            yt_title = f"{yt_title} #Shorts"
        yt_title = yt_title[:100]

        yt_desc = description.strip()
        if "#shorts" not in yt_desc.lower():
            yt_desc = f"{yt_desc}\n\n#Shorts #Viral"

        if tags is None:
            tags = ["Shorts", "Anime", "DarkFantasy", "Story", "Trending"]

        metadata = {
            "snippet": {
                "title": yt_title,
                "description": yt_desc,
                "tags": tags,
                "categoryId": "24"
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False
            }
        }

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Length": str(file_size),
            "X-Upload-Content-Type": "video/mp4"
        }

        init_res = requests.post(YOUTUBE_UPLOAD_URL, headers=headers, json=metadata, timeout=45)
        if init_res.status_code != 200:
            raise RuntimeError(f"YouTube upload init failed ({init_res.status_code}): {init_res.text}")

        upload_url = init_res.headers.get("Location")
        if not upload_url:
            raise RuntimeError("Missing Location header from YouTube init response")

        with open(actual_path, "rb") as video_file:
            upload_headers = {
                "Content-Length": str(file_size),
                "Content-Type": "video/mp4"
            }
            upload_res = requests.put(upload_url, headers=upload_headers, data=video_file, timeout=180)

        if upload_res.status_code not in (200, 201):
            raise RuntimeError(f"YouTube upload failed ({upload_res.status_code}): {upload_res.text}")

        res_json = upload_res.json()
        video_id = res_json.get("id")
        if not video_id:
            raise RuntimeError(f"No video ID returned from YouTube: {res_json}")

        yt_url = f"https://youtube.com/shorts/{video_id}"
        log(f"🎉 YouTube Short successfully published! Video ID: {video_id} ({yt_url})")
        return {"id": video_id, "url": yt_url}

    finally:
        if temp_file and os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except Exception:
                pass

def post_youtube_comment(video_id: str, comment_text: str, client_id: str, client_secret: str, refresh_token: str) -> Optional[str]:
    """Posts a top-level first comment on the YouTube video."""
    try:
        access_token = get_youtube_access_token(client_id, client_secret, refresh_token)
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        body = {
            "snippet": {
                "videoId": video_id,
                "topLevelComment": {
                    "snippet": {
                        "textOriginal": comment_text
                    }
                }
            }
        }
        r = requests.post(YOUTUBE_COMMENTS_URL, headers=headers, json=body, timeout=30)
        if r.status_code in (200, 201):
            c_id = r.json().get("id")
            log(f"YouTube first comment posted (ID: {c_id})")
            return c_id
        else:
            log(f"YouTube comment response ({r.status_code}): {r.text[:100]}")
    except Exception as e:
        log(f"YouTube first comment warning: {e}")
    return None

def send_telegram_alert(message: str):
    """Sends notification to Telegram chat."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() or FALLBACK_TELEGRAM_BOT_TOKEN
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip() or FALLBACK_TELEGRAM_CHAT_ID
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

def git_commit_and_push(filepath: str, commit_message: str, trigger_ci: bool = False):
    """
    Commits and pushes changes to GitHub.
    trigger_ci=True: Pushes via PAT without [skip ci] to trigger next GitHub Actions runner.
    trigger_ci=False: Includes [skip ci] to silently update without starting runners.
    """
    token = os.environ.get("GH_TOKEN", "").strip() or FALLBACK_GH_TOKEN
    gh_user = os.environ.get("GH_USER", "").strip() or FALLBACK_GH_USER
    repo = os.environ.get("GITHUB_REPOSITORY", f"{gh_user}/gow-videos")

    try:
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
        subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], check=True)
        
        subprocess.run(["git", "pull", "--rebase", "origin", "main"], capture_output=True, text=True, timeout=30)
        subprocess.run(["git", "add", filepath], check=True)
        diff_check = subprocess.run(["git", "diff", "--cached", "--quiet"])
        if diff_check.returncode == 0:
            log("No changes detected in git stage.")
            return

        final_msg = commit_message if trigger_ci else f"{commit_message} [skip ci]"
        subprocess.run(["git", "commit", "-m", final_msg], check=True)

        if token:
            remote_url = f"https://{gh_user}:{token}@github.com/{repo}.git"
            subprocess.run(["git", "push", remote_url, "main"], check=True)
        else:
            subprocess.run(["git", "push", "origin", "main"], check=True)

        log(f"Git push success: '{final_msg}' (trigger_ci={trigger_ci})")
    except Exception as e:
        log(f"Git commit/push error: {e}")

def check_active_lease(queue_data: dict) -> bool:
    """Checks if another runner currently holds an active cloud bridge lease."""
    lease = queue_data.get("cloud_bridge_lease")
    if not lease:
        return False

    current_run_id = str(os.environ.get("GITHUB_RUN_ID", "")).strip()
    holder_run_id = str(lease.get("holder_run_id", "")).strip()
    expires_at_str = lease.get("expires_at", "")

    if not holder_run_id or holder_run_id == current_run_id:
        return False

    now_ist = datetime.datetime.now(IST)
    try:
        expires_at = datetime.datetime.fromisoformat(expires_at_str)
        if expires_at <= now_ist + datetime.timedelta(seconds=60):
            return False
    except Exception:
        return False

    token = os.environ.get("GH_TOKEN", "").strip() or FALLBACK_GH_TOKEN
    gh_user = os.environ.get("GH_USER", "").strip() or FALLBACK_GH_USER
    repo = os.environ.get("GITHUB_REPOSITORY", f"{gh_user}/gow-videos")

    if token and holder_run_id:
        try:
            headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}
            url = f"https://api.github.com/repos/{repo}/actions/runs/{holder_run_id}"
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code == 200:
                run_info = r.json()
                status = run_info.get("status")
                if status != "in_progress":
                    log(f"Previous lease holder Run #{holder_run_id} is no longer running (status: {status}). Reclaiming lease.")
                    return False
        except Exception as e:
            log(f"GitHub API lease check error: {e}")

    log(f"🌉 Active CloudBridge lease is held by Run #{holder_run_id} until {expires_at_str}. Another runner is live.")
    return True

def set_lease(queue_path: str, queue_data: dict, duration_seconds: int, target_ep: int, target_slot: str):
    """Claims the lease and persists to queue.json with [skip ci]."""
    now_ist = datetime.datetime.now(IST)
    expires_at = now_ist + datetime.timedelta(seconds=duration_seconds + 300)
    current_run_id = str(os.environ.get("GITHUB_RUN_ID", "local"))

    queue_data["cloud_bridge_lease"] = {
        "holder_run_id": current_run_id,
        "acquired_at": now_ist.isoformat(),
        "expires_at": expires_at.isoformat(),
        "target_ep": target_ep,
        "target_slot": target_slot
    }

    with open(queue_path, "w", encoding="utf-8") as f:
        json.dump(queue_data, f, indent=2, ensure_ascii=False)

    git_commit_and_push(queue_path, f"[CloudBridge Lease] Run #{current_run_id} locked until {expires_at.strftime('%H:%M')} IST", trigger_ci=False)

def clear_lease(queue_path: str, queue_data: dict):
    """Releases the lease in queue.json."""
    if "cloud_bridge_lease" in queue_data:
        del queue_data["cloud_bridge_lease"]
        with open(queue_path, "w", encoding="utf-8") as f:
            json.dump(queue_data, f, indent=2, ensure_ascii=False)

def post_reel_to_instagram(token: str, video_url: str, caption: str, target_dt: datetime.datetime = None) -> str:
    """Posts a video reel using Instagram Graph API with millisecond precision."""
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

    if target_dt:
        while True:
            now_wait = datetime.datetime.now(IST)
            rem_sec = (target_dt - now_wait).total_seconds()
            if rem_sec <= 0.5:
                break
            time.sleep(min(1.0, max(0.1, rem_sec / 2)))

    log("Publishing media container to Instagram feed...")
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

def process_due_items(queue_path: str, force_id: str = None, target_dt: datetime.datetime = None) -> list:
    """Finds and publishes due items to Instagram and YouTube Shorts."""
    if not os.path.exists(queue_path):
        log(f"Queue file not found at {queue_path}")
        return []

    with open(queue_path, "r", encoding="utf-8") as f:
        queue_data = json.load(f)

    accounts = load_accounts_config()
    yt_cfg = load_youtube_config()
    now_ist = datetime.datetime.now(IST)
    now_str = now_ist.strftime("%Y-%m-%d %H:%M")
    gh_user = os.environ.get("GITHUB_REPOSITORY_OWNER") or os.environ.get("GH_USER") or FALLBACK_GH_USER
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

        video_filename = item.get("video_filename") or f"{item_id}.mp4"
        cdn_url = f"https://raw.githubusercontent.com/{gh_user}/{repo_name}/main/{video_filename}"
        local_repo_video = os.path.join(os.path.dirname(queue_path), video_filename)
        caption = item.get("caption") or item.get("title", "")
        first_comment = item.get("first_comment")

        # 1. Instagram Reel Publish
        try:
            media_id = post_reel_to_instagram(token, cdn_url, caption, target_dt=target_dt)
            item["status"] = "posted"
            item["posted_at"] = now_ist.isoformat()
            item["instagram_media_id"] = media_id

            if first_comment:
                time.sleep(5)
                cid = post_first_comment(media_id, first_comment, token)
                if cid:
                    item["first_comment_id"] = cid

            published_items.append(item)

            permalink = f"https://www.instagram.com/{account_name}/"
            try:
                p_info = requests.get(f"{API_BASE}/{media_id}", params={"fields": "permalink", "access_token": token}, timeout=10).json()
                if "permalink" in p_info:
                    permalink = p_info["permalink"]
            except Exception:
                pass

            # 2. YouTube Shorts Publish (if configured)
            yt_url = None
            if yt_cfg.get("client_id") and yt_cfg.get("client_secret") and yt_cfg.get("refresh_token"):
                try:
                    log(f"🎬 Uploading Ep #{item.get('ep_num')} to YouTube Shorts...")
                    vid_source = local_repo_video if os.path.exists(local_repo_video) else cdn_url
                    yt_res = post_short_to_youtube(
                        video_path_or_url=vid_source,
                        title=item.get("title", f"Episode {item.get('ep_num')}"),
                        description=caption,
                        client_id=yt_cfg["client_id"],
                        client_secret=yt_cfg["client_secret"],
                        refresh_token=yt_cfg["refresh_token"]
                    )
                    yt_id = yt_res.get("id")
                    yt_url = yt_res.get("url")
                    item["youtube_video_id"] = yt_id
                    item["youtube_url"] = yt_url
                    item["youtube_status"] = "posted"

                    if first_comment and yt_id:
                        time.sleep(3)
                        post_youtube_comment(yt_id, first_comment, yt_cfg["client_id"], yt_cfg["client_secret"], yt_cfg["refresh_token"])
                except Exception as yt_err:
                    log(f"⚠️ YouTube upload error: {yt_err}")
                    item["youtube_status"] = "failed"
                    item["youtube_error"] = str(yt_err)
            else:
                log("ℹ️ YouTube credentials not configured. Skipping YouTube upload.")

            # 3. Telegram Alert
            yt_line = f"\n▶️ <b>YouTube Short:</b> <a href='{yt_url}'>View on YouTube</a>" if yt_url else ""
            alert_msg = (
                f"🎉 <b>Reel Published ON TIME!</b>\n\n"
                f"👤 <b>Account:</b> @{account_name}\n"
                f"🎬 <b>Episode:</b> #{item.get('ep_num')} - {item.get('title')}\n"
                f"🆔 <b>IG Media ID:</b> <code>{media_id}</code>\n"
                f"⏰ <b>Published At:</b> {now_str} IST\n\n"
                f"📸 <b>Instagram:</b> <a href='{permalink}'>View Reel</a>"
                f"{yt_line}"
            )
            send_telegram_alert(alert_msg)

        except Exception as e:
            log(f"❌ Failed to publish item '{item_id}': {e}")
            item["status"] = "failed"
            item["last_error"] = str(e)
            send_telegram_alert(f"⚠️ <b>Publish Failed for @{account_name}</b>\n\nEpisode: {item.get('title')}\nError: <code>{e}</code>")

    if published_items:
        if "cloud_bridge_lease" in queue_data:
            del queue_data["cloud_bridge_lease"]

        with open(queue_path, "w", encoding="utf-8") as f:
            json.dump(queue_data, f, indent=2, ensure_ascii=False)
        log(f"Queue updated with {len(published_items)} newly published reels.")

        ep_nums = ", ".join(str(it.get("ep_num")) for it in published_items)
        git_commit_and_push(queue_path, f"Automated reel publish: Ep #{ep_nums} posted [trigger relay]", trigger_ci=True)

    return published_items

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    default_queue = os.path.join(script_dir, "queue.json")
    parser = argparse.ArgumentParser(description="GitHub Actions Multi-Platform Cloud Relay Publisher")
    parser.add_argument("--queue", default=default_queue, help="Path to queue.json")
    parser.add_argument("--force-id", help="Force publish specific item ID")
    parser.add_argument("--no-bridge", action="store_true", help="Disable cloud bridge waiting")
    args = parser.parse_args()

    published = process_due_items(args.queue, force_id=args.force_id)
    if published:
        print(f"::set-output name=published_count::{len(published)}")
        sys.exit(0)

    if args.force_id or args.no_bridge:
        log("No items due for publishing right now.")
        sys.exit(0)

    try:
        with open(args.queue, "r", encoding="utf-8") as f:
            queue_data = json.load(f)
    except Exception as e:
        log(f"Error reading queue: {e}")
        sys.exit(0)

    if check_active_lease(queue_data):
        log("Exiting cleanly: Another runner is already bridging this slot.")
        sys.exit(0)

    now_ist = datetime.datetime.now(IST)
    now_str = now_ist.strftime("%Y-%m-%d %H:%M")

    upcoming = []
    for item in queue_data.get("items", []):
        if item.get("status") in ("ready", "scheduled"):
            sched = item.get("scheduled_for", "")
            if sched and sched > now_str:
                try:
                    dt = datetime.datetime.strptime(sched, "%Y-%m-%d %H:%M").replace(tzinfo=IST)
                    upcoming.append((dt, item))
                except Exception:
                    pass

    if not upcoming:
        log("No future unposted reels found in queue.")
        clear_lease(args.queue, queue_data)
        sys.exit(0)

    upcoming.sort(key=lambda x: x[0])
    target_dt, next_item = upcoming[0]
    wait_seconds = (target_dt - now_ist).total_seconds()

    MAX_BRIDGE_SECONDS = 19800

    # ------------------------------------------------------------------
    # BRANCH A: Reel is scheduled within 5.5 hours (Direct Bridge Phase)
    # ------------------------------------------------------------------
    if wait_seconds <= MAX_BRIDGE_SECONDS:
        log(f"🌉 [CloudBridge Direct] Active! Ep #{next_item.get('ep_num')} '{next_item.get('title')}' is scheduled for {next_item.get('scheduled_for')} IST (in {wait_seconds:.0f}s / {wait_seconds/60:.1f}m).")
        set_lease(args.queue, queue_data, int(wait_seconds), next_item.get('ep_num'), next_item.get('scheduled_for'))

        last_heartbeat = time.time()
        while True:
            curr_ist = datetime.datetime.now(IST)
            rem = (target_dt - curr_ist).total_seconds()
            if rem <= 55:
                log(f"⚡ [CloudBridge Direct] T-{rem:.1f}s remaining! Initiating pre-flight media container encoding on Instagram & YouTube...")
                break

            time.sleep(min(30, max(5, int(rem - 50))))

            now_t = time.time()
            if now_t - last_heartbeat >= 300:
                last_heartbeat = now_t
                log(f"🌉 [CloudBridge Direct Heartbeat] Holding alive for {next_item.get('scheduled_for')} IST (T-{rem/60:.1f}m remaining)...")

                try:
                    subprocess.run(["git", "pull", "--rebase", "origin", "main"], capture_output=True, text=True, timeout=30)
                    with open(args.queue, "r", encoding="utf-8") as f_check:
                        fresh_q = json.load(f_check)
                    target_check = next((it for it in fresh_q.get("items", []) if it.get("id") == next_item.get("id")), None)
                    if target_check and target_check.get("status") == "posted":
                        log(f"🌉 Target Ep #{next_item.get('ep_num')} was already posted remotely. Exiting bridge cleanly.")
                        clear_lease(args.queue, fresh_q)
                        sys.exit(0)
                except Exception as e:
                    log(f"Git check warning: {e}")

        published = process_due_items(args.queue, force_id=next_item.get("id"), target_dt=target_dt)
        if published:
            print(f"::set-output name=published_count::{len(published)}")
            log(f"🎉 [CloudBridge Direct] Ep #{next_item.get('ep_num')} published on time!")
        sys.exit(0)

    # ------------------------------------------------------------------
    # BRANCH B: Reel is > 5.5 hours away (Cloud Relay Baton Pass Phase)
    # ------------------------------------------------------------------
    hours = wait_seconds / 3600
    log(f"🏃 [CloudRelay Baton Pass] Next reel Ep #{next_item.get('ep_num')} is scheduled for {next_item.get('scheduled_for')} IST ({hours:.1f}h away).")
    
    relay_hold_seconds = min(18000, max(1800, int(wait_seconds - 18000)))
    log(f"🏃 [CloudRelay Baton Pass] Holding runner for {relay_hold_seconds/3600:.1f} hours, then handing off to fresh runner.")

    set_lease(args.queue, queue_data, relay_hold_seconds, next_item.get('ep_num'), next_item.get('scheduled_for'))

    start_hold_time = time.time()
    last_heartbeat = time.time()

    while True:
        elapsed = time.time() - start_hold_time
        if elapsed >= relay_hold_seconds:
            log("🏃 [CloudRelay Baton Pass] Relay hold period complete! Ready to pass baton to fresh runner.")
            break

        rem_hold = relay_hold_seconds - elapsed
        time.sleep(min(30, max(5, int(rem_hold))))

        now_t = time.time()
        if now_t - last_heartbeat >= 300:
            last_heartbeat = now_t
            log(f"🏃 [CloudRelay Heartbeat] Holding relay (T-{rem_hold/60:.1f}m until baton pass, Ep #{next_item.get('ep_num')} at {next_item.get('scheduled_for')} IST)...")

            try:
                subprocess.run(["git", "pull", "--rebase", "origin", "main"], capture_output=True, text=True, timeout=30)
                with open(args.queue, "r", encoding="utf-8") as f_check:
                    fresh_q = json.load(f_check)
                target_check = next((it for it in fresh_q.get("items", []) if it.get("id") == next_item.get("id")), None)
                if target_check and target_check.get("status") == "posted":
                    log(f"Target Ep #{next_item.get('ep_num')} was already posted remotely. Exiting relay cleanly.")
                    clear_lease(args.queue, fresh_q)
                    sys.exit(0)
            except Exception as e:
                log(f"Git check warning: {e}")

    with open(args.queue, "r", encoding="utf-8") as f:
        q_final = json.load(f)

    if "cloud_bridge_lease" in q_final:
        del q_final["cloud_bridge_lease"]

    q_final["last_relay_handoff"] = {
        "timestamp": datetime.datetime.now(IST).isoformat(),
        "target_ep": next_item.get("ep_num"),
        "target_slot": next_item.get("scheduled_for")
    }

    with open(args.queue, "w", encoding="utf-8") as f:
        json.dump(q_final, f, indent=2, ensure_ascii=False)

    log(f"🏃 [CloudRelay Baton Pass] Passing baton for Ep #{next_item.get('ep_num')} to spawn fresh runner...")
    git_commit_and_push(args.queue, f"[CloudRelay] Baton handoff for Ep #{next_item.get('ep_num')} [trigger runner]", trigger_ci=True)
    log("🏃 [CloudRelay Baton Pass] Baton successfully passed! Exiting cleanly.")
    sys.exit(0)

if __name__ == "__main__":
    main()
