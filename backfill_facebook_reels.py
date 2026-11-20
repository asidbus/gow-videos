#!/usr/bin/env python3
"""
Autonomous Facebook Page Reels Backfill Engine.
Finds episodes in queue.json and uploads them to your Facebook Page as Reels.
Features:
- Resumable upload via official Meta video_reels endpoint
- Rate limiting delay between uploads
- First comment posting
- State persistence directly into queue.json
"""

import os
import sys
import json
import time
import argparse
from facebook_publisher import load_facebook_config, post_reel_to_facebook, post_facebook_comment, verify_facebook_credentials

def main():
    parser = argparse.ArgumentParser(description="Backfill episodes to Facebook Page Reels")
    parser.add_argument("--queue", default="/root/gow/gow-videos/queue.json", help="Path to queue.json")
    parser.add_argument("--series", default="drifter", help="Filter by series keyword, e.g. 'drifter'")
    parser.add_argument("--from-ep", type=int, default=1, help="Starting episode number")
    parser.add_argument("--to-ep", type=int, default=26, help="Ending episode number (defaults to 26 for today evening)")
    parser.add_argument("--delay", type=int, default=20, help="Delay in seconds between uploads")
    parser.add_argument("--force", action="store_true", help="Force upload even if already posted to Facebook")
    parser.add_argument("--dry-run", action="store_true", help="Print items without uploading")
    parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")
    args = parser.parse_args()

    fb_cfg = load_facebook_config()
    page_id = fb_cfg.get("page_id")
    page_token = fb_cfg.get("page_access_token")

    if not page_id or not page_token:
        print("❌ Error: Facebook Page credentials not found.")
        print("Please configure 'FACEBOOK' (page_id and page_access_token) in config.json or environment variables.")
        sys.exit(1)

    try:
        page_info = verify_facebook_credentials(page_id, page_token)
        print(f"✅ Connected to Facebook Page: '{page_info.get('name')}' (ID: {page_info.get('id')})")
    except Exception as e:
        print(f"⚠️ Warning: Facebook Page verification failed: {e}")
        if not args.dry_run:
            sys.exit(1)

    if not os.path.exists(args.queue):
        print(f"❌ Error: Queue file not found at {args.queue}")
        sys.exit(1)

    with open(args.queue, "r", encoding="utf-8") as f:
        queue_data = json.load(f)

    target_items = []
    for it in queue_data.get("items", []):
        ep = it.get("ep_num")
        if not ep or not (args.from_ep <= ep <= args.to_ep):
            continue
        if args.series and args.series.lower() not in (it.get("series_id", "") + it.get("series_title", "")).lower():
            continue
        if not args.force and it.get("facebook_status") == "posted":
            continue
        target_items.append(it)

    target_items.sort(key=lambda x: x.get("ep_num", 0))
    print(f"\nFound {len(target_items)} episodes to publish to Facebook Reels (Range Part {args.from_ep} to {args.to_ep}):")

    for it in target_items:
        fb_status = it.get("facebook_status", "not_uploaded")
        print(f"  - Ep #{it.get('ep_num')}: '{it.get('title')[:45]}' [FB: {fb_status}]")

    if args.dry_run:
        print("\nDry run complete. No uploads performed.")
        return

    if not target_items:
        print("\nAll episodes in range already posted to Facebook Reels!")
        return

    if not args.yes and sys.stdin.isatty():
        confirm = input(f"\nUpload {len(target_items)} videos to Facebook Page Reels? (y/N): ").strip().lower()
        if confirm != "y":
            print("Aborted.")
            return

    success_count = 0
    for it in target_items:
        ep = it.get("ep_num")
        title = it.get("title", f"Part {ep}")
        caption = it.get("caption", title)
        first_comment = it.get("first_comment")

        video_filename = it.get("video_filename") or f"{it.get('id')}.mp4"
        local_repo_path = os.path.join(os.path.dirname(args.queue), video_filename)
        out_path = f"/root/gow/out/drifter_part{ep:02d}.mp4"

        if os.path.exists(local_repo_path):
            video_path = local_repo_path
        elif os.path.exists(out_path):
            video_path = out_path
        else:
            video_path = f"https://raw.githubusercontent.com/asidbus/gow-videos/main/{video_filename}"

        print(f"\n🚀 Uploading Part {ep} to Facebook Page Reels ({video_path})...")
        try:
            res = post_reel_to_facebook(
                video_path_or_url=video_path,
                title=title,
                description=caption,
                page_id=page_id,
                page_access_token=page_token
            )
            it["facebook_post_id"] = res["id"]
            it["facebook_url"] = res["url"]
            it["facebook_status"] = "posted"
            it.pop("facebook_error", None)
            success_count += 1

            if first_comment:
                time.sleep(3)
                post_facebook_comment(res["id"], first_comment, page_token)

            with open(args.queue, "w", encoding="utf-8") as f:
                json.dump(queue_data, f, indent=2, ensure_ascii=False)

            print(f"✅ Part {ep} successfully published to Facebook: {res['url']}")
            if ep < args.to_ep:
                print(f"Waiting {args.delay}s before next upload...")
                time.sleep(args.delay)

        except Exception as e:
            print(f"❌ Failed to publish Part {ep} to Facebook: {e}")
            it["facebook_status"] = "failed"
            it["facebook_error"] = str(e)
            with open(args.queue, "w", encoding="utf-8") as f:
                json.dump(queue_data, f, indent=2, ensure_ascii=False)

    print(f"\n🎉 Facebook backfill complete! Successfully uploaded {success_count}/{len(target_items)} Reels.")

if __name__ == "__main__":
    main()
