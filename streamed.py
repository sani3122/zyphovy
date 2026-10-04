#!/usr/bin/env python3
"""
JSON Live Football M3U Scraper
- Fetches matches directly from raw GitHub JSON.
- Filters strictly for Category == "Football".
- Filters streams strictly for Quality == "HD".
- Checks 'Match Status' == "Live 🔴" OR compares 'Start Time' with Bangladesh Time (UTC+6).
- Target streams strictly ending in 'mono.m3u8'.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
import re
import aiohttp
from camoufox.async_api import AsyncCamoufox

JSON_URL = "https://raw.githubusercontent.com/srhady/data/refs/heads/main/live_sports_playlist.json"
OUTPUT_FILE = "live_playlist.m3u"
PAGE_TIMEOUT = 12  # Seconds to wait per embed link to let mono.m3u8 load


def is_mono_stream_url(url: str) -> bool:
    """Target strictly mono.m3u8 or sub-level quality variant streams."""
    lower = url.lower()
    
    # Check for mono.m3u8 explicitly
    if "mono.m3u8" in lower:
        return True
        
    return False


def is_match_live_or_due(match_item: dict) -> bool:
    """Determines if a match is live or should be playing based on Bangladesh Time (UTC+6)."""
    status = match_item.get("Match Status", "")

    # Rule 1: Immediate match if status contains 'Live' or 🔴
    if "live" in str(status).lower() or "🔴" in str(status):
        return True

    # Rule 2: Compare start time with Bangladesh Standard Time (UTC+6)
    start_time_str = match_item.get("Start Time", "")
    if not start_time_str:
        return False

    try:
        bd_tz = timezone(timedelta(hours=6))
        now_bd = datetime.now(bd_tz)

        time_part, date_part = start_time_str.split("|")
        time_part = time_part.strip()
        date_part = date_part.strip()

        parsed_dt = datetime.strptime(
            f"{date_part}-{now_bd.year} {time_part}", "%d-%b-%Y %I:%M %p"
        )
        match_dt = parsed_dt.replace(tzinfo=bd_tz)

        if match_dt <= now_bd <= (match_dt + timedelta(hours=3, minutes=30)):
            return True
    except Exception:
        pass

    return False


async def fetch_json_data(url: str) -> list:
    """Fetches the live sports JSON data from GitHub."""
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status == 200:
                return await response.json(content_type=None)
            else:
                print(f"[!] Failed to fetch JSON. HTTP Status: {response.status}")
                return []


class FootballJsonScraper:

    def __init__(self, json_url=JSON_URL):
        self.json_url = json_url
        self.captured_streams = []

    async def run(self):
        print("[*] Fetching live match list from JSON...")
        all_matches = await fetch_json_data(self.json_url)
        print(f"[+] Downloaded {len(all_matches)} total matches from JSON.")

        football_matches = [
            m
            for m in all_matches
            if str(m.get("Category", "")).strip().lower() == "football"
        ]
        print(f"    -> Found {len(football_matches)} Football match(es).")

        active_matches = [m for m in football_matches if is_match_live_or_due(m)]
        print(
            f"    -> {len(active_matches)} Football match(es) are currently Live"
            " or active in Bangladesh Time."
        )

        if not active_matches:
            print("[!] No active football matches found right now.")
            return

        print("\n[*] Initializing Camoufox browser for mono.m3u8 extraction...")
        async with AsyncCamoufox(headless=True) as browser:
            for match in active_matches:
                t1 = match.get("Team 1 Name", "").strip()
                t2 = match.get("Team 2 Name", "").strip()

                if t1 and t2:
                    display_title = f"{t1} VS {t2}"
                else:
                    display_title = match.get("Match Title", "Football Match")

                logo = match.get("Match Poster", "")
                streams = match.get("Streams", [])

                hd_streams = [
                    s
                    for s in streams
                    if str(s.get("Quality", "")).strip().upper() == "HD"
                ]

                print(
                    f"\n[Match] Processing: {display_title} ({len(hd_streams)} HD"
                    " stream options)"
                )

                for stream in hd_streams:
                    embed_url = stream.get("Embed_URL", "")
                    source_name = stream.get("Source", "Server")
                    lang = stream.get("Language", "")
                    if not embed_url:
                        continue

                    stream_title = f"{display_title} ({source_name} - {lang})"
                    print(f"    -> Inspecting Embed: {embed_url}")

                    captured = await self._scrape_embed_url(
                        browser, embed_url, stream_title, logo
                    )
                    if captured:
                        self.captured_streams.extend(captured)

        print(
            f"\n[*] Generating final M3U playlist ({len(self.captured_streams)}"
            " streams captured)..."
        )
        self._generate_m3u(self.captured_streams)

    async def _scrape_embed_url(self, browser, embed_url, title, logo):
        """Opens embed URL, plays video, and intercepts mono.m3u8."""
        context = await browser.new_context()
        page = await context.new_page()
        found = []
        captured_urls = set()

        def handle_request(request):
            url = request.url
            if is_mono_stream_url(url) and url not in captured_urls:
                captured_urls.add(url)
                headers = request.headers
                found.append({
                    "title": title,
                    "url": url,
                    "logo": logo,
                    "referer": headers.get("referer", embed_url),
                    "user_agent": headers.get("user-agent", "Mozilla/5.0"),
                    "origin": headers.get("origin", "https://embed.st"),
                })
                print(f"        [HIT mono.m3u8] Captured Stream: {url[:95]}")

        # Attach request listener to entire context to capture iframe traffic
        context.on("request", handle_request)

        try:
            await page.goto(embed_url, wait_until="domcontentloaded", timeout=25000)
            await asyncio.sleep(2)

            # Trigger video play command across frames to force mono.m3u8 fetch
            for frame in page.frames:
                try:
                    await frame.evaluate("""() => {
                        const v = document.querySelector('video');
                        if (v) v.play().catch(() => {});
                    }""")
                except Exception:
                    pass

            await asyncio.sleep(PAGE_TIMEOUT)
        except Exception as e:
            print(f"        [!] Embed load warning: {e}")
        finally:
            await context.close()

        return found

    def _generate_m3u(self, streams):
        """Saves playlist into clean M3U format with full IPTV headers."""
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines = [
            "#EXTM3U",
            f"# generated: {ts}",
            f"# streams: {len(streams)}",
            "",
        ]

        for s in streams:
            title = s["title"].replace('"', "'")
            url = s["url"]
            logo = s["logo"]
            ref = s["referer"]
            ua = s["user_agent"]
            origin = s["origin"]

            lines.append(f'#EXTINF:-1 tvg-logo="{logo}" group-title="Football",{title}')
            lines.append(f"#EXTVLCOPT:http-referrer={ref}")
            lines.append(f"#EXTVLCOPT:http-user-agent={ua}")
            if origin:
                lines.append(f"#EXTVLCOPT:http-origin={origin}")

            kodi_props = [f"Referer={ref}", f"User-Agent={ua}"]
            if origin:
                kodi_props.append(f"Origin={origin}")
            lines.append(
                "#KODIPROP:inputstream.adaptive.stream_headers=" + "&".join(kodi_props)
            )

            lines.append(url)
            lines.append("")

        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        print(f"\n[+] Success! Saved live playlist to '{OUTPUT_FILE}'.")


if __name__ == "__main__":
    scraper = FootballJsonScraper()
    asyncio.run(scraper.run())
          
