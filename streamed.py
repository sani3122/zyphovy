#!/usr/bin/env python3
"""
JSON Live Football M3U Scraper
- Fetches matches directly from raw GitHub JSON.
- Filters strictly for Category == "Football" and Quality == "HD".
- Intercepts network calls or parses master manifest files directly to extract 
  the exact server-published mono.m3u8 stream URLs.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from urllib.parse import urljoin
import aiohttp
from camoufox.async_api import AsyncCamoufox

JSON_URL = "https://raw.githubusercontent.com/srhady/data/refs/heads/main/live_sports_playlist.json"
OUTPUT_FILE = "live_playlist.m3u"
PAGE_TIMEOUT = 10  # Seconds to allow for network interception


def is_match_live_or_due(match_item: dict) -> bool:
    """Determines if a match is live or active based on BD Time (UTC+6)."""
    status = match_item.get("Match Status", "")

    if "live" in str(status).lower() or "🔴" in str(status):
        return True

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

        print("\n[*] Initializing Camoufox browser for stream extraction...")
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
        """Intercepts stream requests or reads playlist.m3u8 to extract the exact mono stream path."""
        context = await browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        )
        page = await context.new_page()

        captured_requests = []

        # Synchronous request listener to prevent async task drops
        def on_request(request):
            url = request.url
            if ".m3u8" in url.lower():
                captured_requests.append({
                    "url": url,
                    "headers": request.headers,
                })
                print(f"        [NETWORK] Intercepted: {url[:85]}...")

        context.on("request", on_request)

        try:
            await page.goto(embed_url, wait_until="domcontentloaded", timeout=25000)

            # Allow time for initial network requests to trigger
            for _ in range(PAGE_TIMEOUT):
                await asyncio.sleep(1)
                # If a direct mono.m3u8 URL was caught, break early
                if any(
                    "mono.m3u8" in req["url"].lower() for req in captured_requests
                ):
                    break

        except Exception as e:
            print(f"        [!] Page load warning: {e}")

        final_streams = []

        # 1. Direct match: Check if the player directly requested mono.m3u8
        mono_reqs = [r for r in captured_requests if "mono.m3u8" in r["url"].lower()]
        low_mono = next((r for r in mono_reqs if "low/" in r["url"].lower()), None)
        target_mono = low_mono or (mono_reqs[-1] if mono_reqs else None)

        if target_mono:
            url = target_mono["url"]
            headers = target_mono["headers"]
            print(f"        [SUCCESS - Direct Native Stream] {url}")
            final_streams.append({
                "title": title,
                "url": url,
                "logo": logo,
                "referer": headers.get("referer", embed_url),
                "user_agent": headers.get("user-agent", "Mozilla/5.0"),
                "origin": headers.get("origin", "https://embed.st"),
            })
        else:
            # 2. Fallback: If only playlist.m3u8 was requested, fetch it and parse the exact variant link
            master_reqs = [
                r for r in captured_requests if ".m3u8" in r["url"].lower()
            ]
            if master_reqs:
                master = master_reqs[0]
                master_url = master["url"]
                headers = master["headers"]
                print(f"        [*] Parsing master manifest: {master_url[:80]}...")

                try:
                    async with aiohttp.ClientSession() as session:
                        req_headers = {
                            "User-Agent": headers.get("user-agent", "Mozilla/5.0"),
                            "Referer": headers.get("referer", embed_url),
                        }
                        async with session.get(
                            master_url, headers=req_headers, timeout=10
                        ) as resp:
                            if resp.status == 200:
                                manifest_text = await resp.text()
                                child_paths = []

                                for line in manifest_text.splitlines():
                                    line = line.strip()
                                    if (
                                        line
                                        and not line.startswith("#")
                                        and ".m3u8" in line.lower()
                                    ):
                                        child_paths.append(line)

                                if child_paths:
                                    # Pick low/mono if published in manifest, else first available
                                    best_path = next(
                                        (
                                            p
                                            for p in child_paths
                                            if "low/" in p.lower()
                                        ),
                                        child_paths[0],
                                    )
                                    exact_child_url = urljoin(master_url, best_path)
                                    print(
                                        "        [SUCCESS - Manifest Resolved"
                                        f" Stream] {exact_child_url}"
                                    )
                                    final_streams.append({
                                        "title": title,
                                        "url": exact_child_url,
                                        "logo": logo,
                                        "referer": headers.get(
                                            "referer", embed_url
                                        ),
                                        "user_agent": headers.get(
                                            "user-agent", "Mozilla/5.0"
                                        ),
                                        "origin": headers.get(
                                            "origin", "https://embed.st"
                                        ),
                                    })
                except Exception as ex:
                    print(f"        [!] Error reading manifest: {ex}")

        await context.close()
        return final_streams

    def _generate_m3u(self, streams):
        """Saves playlist into clean M3U format with IPTV headers."""
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
            
