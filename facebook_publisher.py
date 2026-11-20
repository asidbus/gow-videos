#!/usr/bin/env python3
"""
Autonomous Facebook Page Reel Publisher
Uses official Meta Graph API (v21.0) and rupload protocol.
Zero external dependencies (uses requests).

Workflow:
1. Initialize Reel upload session: POST /{page_id}/video_reels (upload_phase=start)
2. Resumable binary upload: POST {upload_url} on rupload.facebook.com
3. Finish & Publish Reel: POST /{page_id}/video_reels (upload_phase=finish, video_state=PUBLISHED)
4. Verify processing state: GET /{video_id}?fields=status
5. Post optional engagement first-comment: POST /{video_id}/comments
"""

import os
import sys
import json
import time
import requests
from typing import Dict, Optional

GRAPH_API_BASE = "https://graph.facebook.com/v21.0"
CONFIG_FILE = "/root/gow/config.json"

def log(*args):
    now_str = time.strftime("%d %b %H:%M:%S")
    print(f"[{now_str}] [FBPublisher]", *args, flush=True)

def load_facebook_config(config_path: str = CONFIG_FILE) -> Dict[str, str]:
    """Loads Facebook Page ID and Page Access Token from environment or config.json."""
    page_id = os.environ.get("FB_PAGE_ID") or os.environ.get("FACEBOOK_PAGE_ID") or ""
    page_access_token = os.environ.get("FB_PAGE_ACCESS_TOKEN") or os.environ.get("FACEBOOK_PAGE_ACCESS_TOKEN") or ""

    if (not page_id or not page_access_token) and os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            fb_section = cfg.get("FACEBOOK", {})
            if not page_id:
                page_id = fb_section.get("page_id", "")
            if not page_access_token:
                page_access_token = fb_section.get("page_access_token", "")
        except Exception as e:
            log(f"Warning reading config file: {e}")

    return {
        "page_id": page_id.strip(),
        "page_access_token": page_access_token.strip()
    }

def verify_facebook_credentials(page_id: str, page_access_token: str) -> Dict[str, any]:
    """Verifies that the Page ID and Page Access Token are valid and returns Page info."""
    url = f"{GRAPH_API_BASE}/{page_id}"
    params = {
        "fields": "id,name,link,fan_count,verification_status",
        "access_token": page_access_token
    }
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, timeout=15)
            data = r.json()
            if "error" in data:
                raise RuntimeError(f"Facebook API Error: {data['error'].get('message')}")
            return data
        except requests.exceptions.RequestException as e:
            if attempt == 2:
                raise
            time.sleep(2)

def post_reel_to_facebook(
    video_path_or_url: str,
    title: str,
    description: str,
    page_id: str,
    page_access_token: str
) -> Dict[str, str]:
    """
    Publishes a vertical video as a Facebook Page Reel using the official 3-phase Reels API.
    Returns: {"id": video_id, "url": reel_url}
    """
    if not page_id or not page_access_token:
        raise ValueError("Missing Facebook Page ID or Page Access Token.")

    # 1. Prepare video binary
    temp_file = None
    if video_path_or_url.startswith("http://") or video_path_or_url.startswith("https://"):
        log(f"Downloading video from CDN URL: {video_path_or_url[:60]}...")
        temp_file = f"/tmp/fb_reel_{int(time.time())}.mp4"
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
    log(f"Starting Facebook Reel publish ({file_size / (1024*1024):.2f} MB): '{title[:45]}...'")

    try:
        # Phase 1: Initialize Upload Session
        init_url = f"{GRAPH_API_BASE}/{page_id}/video_reels"
        init_payload = {
            "upload_phase": "start",
            "access_token": page_access_token
        }

        init_res = None
        for attempt in range(3):
            try:
                r = requests.post(init_url, data=init_payload, timeout=30)
                init_res = r.json()
                if "video_id" in init_res and "upload_url" in init_res:
                    break
                log(f"Phase 1 retry ({attempt+1}/3): {init_res}")
            except Exception as e:
                log(f"Phase 1 network error ({attempt+1}/3): {e}")
            time.sleep(3)
        else:
            raise RuntimeError(f"Facebook Reels Phase 1 (init) failed: {init_res}")

        video_id = init_res["video_id"]
        upload_url = init_res["upload_url"]
        log(f"Upload session initialized: Video ID = {video_id}")

        # Phase 2: Binary Video Upload (rupload.facebook.com)
        upload_headers = {
            "Authorization": f"OAuth {page_access_token}",
            "offset": "0",
            "file_size": str(file_size),
            "Content-Type": "application/octet-stream"
        }

        with open(actual_path, "rb") as video_file:
            log(f"Streaming video bytes to rupload endpoint...")
            for attempt in range(3):
                try:
                    video_file.seek(0)
                    up_r = requests.post(upload_url, headers=upload_headers, data=video_file, timeout=180)
                    up_json = up_r.json()
                    if up_json.get("success") is True or up_r.status_code in (200, 201):
                        log("Video binary transfer complete!")
                        break
                    log(f"Phase 2 upload attempt ({attempt+1}/3) returned: {up_r.text[:150]}")
                except Exception as e:
                    log(f"Phase 2 upload error ({attempt+1}/3): {e}")
                time.sleep(3)
            else:
                raise RuntimeError(f"Facebook Reels Phase 2 (upload) failed after 3 attempts")

        # Phase 3: Finalize & Publish Reel
        finish_payload = {
            "upload_phase": "finish",
            "access_token": page_access_token,
            "video_id": video_id,
            "video_state": "PUBLISHED",
            "description": description.strip(),
            "title": title[:100].strip()
        }

        finish_res = None
        for attempt in range(3):
            try:
                r = requests.post(init_url, data=finish_payload, timeout=30)
                finish_res = r.json()
                if finish_res.get("success") is True:
                    break
                log(f"Phase 3 finish attempt ({attempt+1}/3): {finish_res}")
            except Exception as e:
                log(f"Phase 3 network error ({attempt+1}/3): {e}")
            time.sleep(3)
        else:
            raise RuntimeError(f"Facebook Reels Phase 3 (publish) failed: {finish_res}")

        reel_url = f"https://www.facebook.com/reel/{video_id}"
        log(f"🎉 Facebook Reel successfully published! Video ID: {video_id} ({reel_url})")

        return {
            "id": video_id,
            "url": reel_url
        }

    finally:
        if temp_file and os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except Exception:
                pass

def post_facebook_comment(video_id: str, comment_text: str, page_access_token: str) -> Optional[str]:
    """Posts a top-level first comment on the Facebook Reel / Video."""
    try:
        url = f"{GRAPH_API_BASE}/{video_id}/comments"
        payload = {
            "message": comment_text,
            "access_token": page_access_token
        }
        r = requests.post(url, data=payload, timeout=20)
        res = r.json()
        if "id" in res:
            log(f"Facebook first comment posted (ID: {res['id']})")
            return res["id"]
        else:
            log(f"Facebook first comment notice ({r.status_code}): {res}")
    except Exception as e:
        log(f"Facebook comment exception: {e}")
    return None

if __name__ == "__main__":
    cfg = load_facebook_config()
    print("Facebook Configuration:")
    print("  Page ID:", cfg["page_id"] or "NOT SET")
    print("  Page Access Token:", "SET (" + cfg["page_access_token"][:10] + "...)" if cfg["page_access_token"] else "NOT SET")

    if cfg["page_id"] and cfg["page_access_token"]:
        try:
            info = verify_facebook_credentials(cfg["page_id"], cfg["page_access_token"])
            print(f"✅ Connected to Page: '{info.get('name')}' (ID: {info.get('id')})")
        except Exception as e:
            print(f"❌ Verification failed: {e}")
    else:
        print("\nℹ️ To enable Facebook publishing, add 'FACEBOOK' credentials to config.json:")
        print('   "FACEBOOK": {')
        print('     "page_id": "<YOUR_PAGE_ID>",')
        print('     "page_access_token": "<YOUR_PAGE_ACCESS_TOKEN>"')
        print('   }')
