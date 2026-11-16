#!/usr/bin/env python3
"""
Zero-dependency YouTube Shorts Publisher via YouTube Data API v3.
Uses standard HTTP requests to authenticate via Google OAuth 2.0 refresh token
and uploads vertical videos as YouTube Shorts.
"""

import os
import sys
import json
import time
import requests
from typing import Optional, Dict, Tuple

OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"
YOUTUBE_COMMENTS_URL = "https://www.googleapis.com/youtube/v3/commentThreads?part=snippet"

_cached_token_info = {
    "access_token": None,
    "expires_at": 0
}

def log(*args):
    now_str = time.strftime("%d %b %H:%M:%S")
    print(f"[{now_str}] [YouTubePublisher]", *args, flush=True)

def load_youtube_config(config_path: str = "/root/gow/config.json") -> Dict[str, str]:
    """Loads YouTube credentials from config.json or environment."""
    creds = {
        "client_id": os.environ.get("YT_CLIENT_ID", "").strip(),
        "client_secret": os.environ.get("YT_CLIENT_SECRET", "").strip(),
        "refresh_token": os.environ.get("YT_REFRESH_TOKEN", "").strip()
    }

    if not all(creds.values()) and os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            yt_cfg = cfg.get("YOUTUBE", {})
            for k in ("client_id", "client_secret", "refresh_token"):
                if not creds[k]:
                    creds[k] = yt_cfg.get(k) or cfg.get(f"YT_{k.upper()}", "")
        except Exception as e:
            log(f"Warning reading {config_path}: {e}")

    return creds

def get_access_token(client_id: str, client_secret: str, refresh_token: str) -> str:
    """Refreshes and returns a valid Google OAuth 2.0 access token."""
    global _cached_token_info
    now = time.time()
    if _cached_token_info["access_token"] and now < _cached_token_info["expires_at"] - 60:
        return _cached_token_info["access_token"]

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
    _cached_token_info["access_token"] = access_token
    _cached_token_info["expires_at"] = now + expires_in
    log("Successfully refreshed YouTube OAuth access token")
    return access_token

def upload_short(
    video_path_or_url: str,
    title: str,
    description: str,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    tags: Optional[list] = None,
    privacy_status: str = "public"
) -> Dict[str, str]:
    """
    Uploads a vertical video as a YouTube Short via resumable upload.
    Returns dict with 'id' and 'url'.
    """
    access_token = get_access_token(client_id, client_secret, refresh_token)

    # 1. Prepare video source
    temp_file = None
    if video_path_or_url.startswith("http://") or video_path_or_url.startswith("https://"):
        log(f"Downloading video from CDN URL: {video_path_or_url}")
        temp_file = f"/tmp/yt_upload_{int(time.time())}.mp4"
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
    log(f"Starting YouTube upload ({file_size / (1024*1024):.2f} MB): '{title}'")

    try:
        # Ensure #Shorts is present in title or description
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
                "categoryId": "24" # Entertainment
            },
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": False
            }
        }

        # Step A: Initiate resumable session
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Length": str(file_size),
            "X-Upload-Content-Type": "video/mp4"
        }

        init_res = requests.post(YOUTUBE_UPLOAD_URL, headers=headers, json=metadata, timeout=45)
        if init_res.status_code != 200:
            raise RuntimeError(f"YouTube upload initialization failed ({init_res.status_code}): {init_res.text}")

        upload_url = init_res.headers.get("Location")
        if not upload_url:
            raise RuntimeError("Missing 'Location' header in YouTube upload initialization response")

        # Step B: Stream video binary to upload URL
        with open(actual_path, "rb") as video_file:
            upload_headers = {
                "Content-Length": str(file_size),
                "Content-Type": "video/mp4"
            }
            log("Uploading video payload to YouTube...")
            upload_res = requests.put(upload_url, headers=upload_headers, data=video_file, timeout=180)

        if upload_res.status_code not in (200, 201):
            raise RuntimeError(f"YouTube video upload failed ({upload_res.status_code}): {upload_res.text}")

        res_json = upload_res.json()
        video_id = res_json.get("id")
        if not video_id:
            raise RuntimeError(f"No video ID returned by YouTube: {res_json}")

        yt_url = f"https://youtube.com/shorts/{video_id}"
        log(f"🎉 YouTube Short successfully published! Video ID: {video_id} ({yt_url})")
        return {"id": video_id, "url": yt_url}

    finally:
        if temp_file and os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except Exception:
                pass

def post_first_comment(video_id: str, comment_text: str, client_id: str, client_secret: str, refresh_token: str) -> Optional[str]:
    """Posts a top-level first comment on the YouTube video."""
    try:
        access_token = get_access_token(client_id, client_secret, refresh_token)
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
            log(f"YouTube comment warning ({r.status_code}): {r.text[:100]}")
    except Exception as e:
        log(f"YouTube first comment exception: {e}")
    return None

if __name__ == "__main__":
    cfg = load_youtube_config()
    print("YouTube Configuration Loaded:")
    print("  Client ID:", cfg["client_id"][:15] + "..." if cfg["client_id"] else "NOT SET")
    print("  Client Secret:", "SET" if cfg["client_secret"] else "NOT SET")
    print("  Refresh Token:", "SET" if cfg["refresh_token"] else "NOT SET")
