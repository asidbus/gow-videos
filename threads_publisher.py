#!/usr/bin/env python3
"""
Autonomous Meta Threads Video & Reel Publisher
Uses official Meta Threads API (graph.threads.net/v1.0).
Zero extra dependencies (uses requests).
Supports:
- Video publishing via public CDN URL
- Text / caption publishing
- Container status polling with exponential backoff
- Permalinks retrieval
"""

import os
import sys
import time
import requests
from typing import Optional, Dict

THREADS_API_BASE = "https://graph.threads.net/v1.0"

def log(*args):
    now_str = time.strftime("%d %b %H:%M:%S")
    print(f"[{now_str}] [ThreadsPublisher]", *args, flush=True)

def post_to_threads(
    threads_token: str,
    video_url: str,
    text: str,
    threads_user_id: str = "me"
) -> Dict[str, str]:
    """
    Publishes a video to Threads via Meta Threads API.
    Returns dict with 'id' and 'permalink'.
    """
    log(f"Initiating Threads media container for video: {video_url[:60]}...")
    create_url = f"{THREADS_API_BASE}/{threads_user_id}/threads"
    
    # Threads max caption length is 500 characters
    clean_text = text.strip()
    if len(clean_text) > 500:
        clean_text = clean_text[:497] + "..."

    payload = {
        "media_type": "VIDEO",
        "video_url": video_url,
        "text": clean_text,
        "access_token": threads_token
    }

    # Step 1: Create media container with retry
    res = {}
    for attempt in range(3):
        try:
            r = requests.post(create_url, data=payload, timeout=60)
            res = r.json()
            if "id" in res:
                break
            log(f"Container creation retry ({attempt+1}/3): {res}")
        except Exception as net_err:
            log(f"Network glitch on container create ({attempt+1}/3): {net_err}")
        time.sleep(3)
    else:
        raise RuntimeError(f"Threads container creation failed: {res}")

    container_id = res["id"]
    log(f"Container created with ID: {container_id}. Waiting for video processing...")

    # Step 2: Poll container status
    status_url = f"{THREADS_API_BASE}/{container_id}"
    params = {
        "fields": "status,error_message",
        "access_token": threads_token
    }

    max_wait = 180
    start_time = time.time()
    while time.time() - start_time < max_wait:
        time.sleep(5)
        st_res = requests.get(status_url, params=params, timeout=20).json()
        status = st_res.get("status")
        if status == "FINISHED":
            log(f"Container {container_id} is FINISHED and ready to publish.")
            break
        elif status == "ERROR":
            raise RuntimeError(f"Threads video encoding error: {st_res.get('error_message', 'Unknown error')}")
        else:
            log(f"Container status: {status} (elapsed: {int(time.time() - start_time)}s)...")
    else:
        raise TimeoutError(f"Threads container {container_id} timed out waiting for encoding")

    # Step 3: Publish container with retry
    log(f"Publishing Threads container {container_id}...")
    pub_url = f"{THREADS_API_BASE}/{threads_user_id}/threads_publish"
    pub_payload = {
        "creation_id": container_id,
        "access_token": threads_token
    }
    pres = {}
    for attempt in range(3):
        try:
            pr = requests.post(pub_url, data=pub_payload, timeout=45)
            pres = pr.json()
            if "id" in pres:
                break
            log(f"Publish retry ({attempt+1}/3): {pres}")
        except Exception as pub_net_err:
            log(f"Network glitch on publish ({attempt+1}/3): {pub_net_err}")
        time.sleep(3)
    else:
        raise RuntimeError(f"Threads publish failed: {pres}")

    threads_post_id = pres["id"]
    log(f"🎉 Successfully published to Threads! Post ID: {threads_post_id}")

    # Step 4: Get permalink
    permalink = f"https://www.threads.net/t/{threads_post_id}"
    try:
        p_res = requests.get(
            f"{THREADS_API_BASE}/{threads_post_id}",
            params={"fields": "permalink", "access_token": threads_token},
            timeout=10
        ).json()
        if "permalink" in p_res:
            permalink = p_res["permalink"]
    except Exception:
        pass

    return {
        "id": threads_post_id,
        "permalink": permalink
    }

def get_threads_profile(threads_token: str) -> dict:
    """Verifies Threads access token and retrieves profile info."""
    r = requests.get(
        f"{THREADS_API_BASE}/me",
        params={"fields": "id,username,name,threads_profile_picture_url", "access_token": threads_token},
        timeout=15
    )
    return r.json()
