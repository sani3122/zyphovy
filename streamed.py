#!/usr/bin/env python3
"""
Lightweight WebKit (Safari Engine) Live Stream Scraper
- Ultra fast execution to save GitHub Action minutes.
- Uses Playwright WebKit to bypass embed anti-bot blocks without overhead.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from urllib.parse import urljoin
import aiohttp
from playwright.async_api import async_playwright

JSON_URL = "https://raw.githubusercontent.com/srhady/data/refs/heads/main/live_sports_playlist.json"
OUTPUT_FILE = "live_playlist.m3u"
PAGE_TIMEOUT = 6  # মাত্র ৬ সেকেন্ড সময় দেবে লিঙ্ক ক্যাপচার করার জন্য


def is_match_live_or_due(match_item: dict) -> bool:
    """BD Time (UTC+6) অনুযায়ী ম্যাচ লাইভ আছে কি না তা চেক করে।"""
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
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status == 200:
                return await response.json(content_type=None)
            else:
                return []


class FootballJsonScraper:

    def __init__(self, json_url=JSON_URL):
        self.json_url = json_url
        self.captured_streams = []

    async def run(self):
        print("[*] JSON ডাটা ডাউনলোড করা হচ্ছে...")
        all_matches = await fetch_json_data(self.json_url)

        football_matches = [
            m
            for m in all_matches
            if str(m.get("Category", "")).strip().lower() == "football"
        ]
        active_matches = [m for m in football_matches if is_match_live_or_due(m)]

        if not active_matches:
            print("[!] এই মুহূর্তে কোনো লাইভ ফুটবল ম্যাচ নেই।")
            return

        print("\n[*] Fast WebKit Browser দিয়ে লিঙ্ক এক্সট্র্যাক্ট করা হচ্ছে...")
        async with async_playwright() as p:
            # WebKit (Safari Engine) ব্যবহার করা হচ্ছে যা হালকা ও দ্রুতগতির
            browser = await p.webkit.launch(headless=True)

            for match in active_matches:
                t1 = match.get("Team 1 Name", "").strip()
                t2 = match.get("Team 2 Name", "").strip()
                display_title = (
                    f"{t1} VS {t2}"
                    if (t1 and t2)
                    else match.get("Match Title", "Football Match")
                )
                logo = match.get("Match Poster", "")
                streams = match.get("Streams", [])

                hd_streams = [
                    s
                    for s in streams
                    if str(s.get("Quality", "")).strip().upper() == "HD"
                ]

                for stream in hd_streams:
                    embed_url = stream.get("Embed_URL", "")
                    source_name = stream.get("Source", "Server")
                    lang = stream.get("Language", "")
                    if not embed_url:
                        continue

                    stream_title = f"{display_title} ({source_name} - {lang})"
                    captured = await self._scrape_embed_url(
                        browser, embed_url, stream_title, logo
                    )
                    if captured:
                        self.captured_streams.extend(captured)

            await browser.close()

        print(
            f"\n[*] M3U তৈরি করা হচ্ছে ({len(self.captured_streams)} লিঙ্ক পাওয়া"
            " গেছে)..."
        )
        self._generate_m3u(self.captured_streams)

    async def _scrape_embed_url(self, browser, embed_url, title, logo):
        context = await browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
        )
        page = await context.new_page()

        # ইমেজ ও ফন্ট ব্লক করা হচ্ছে যাতে রান টাইম আরও দ্রুত হয়
        await page.route(
            "**/*.{png,jpg,jpeg,svg,webp,woff,woff2,ttf}",
            lambda route: route.abort(),
        )

        captured_requests = []

        def on_request(request):
            url = request.url
            if ".m3u8" in url.lower():
                captured_requests.append({
                    "url": url,
                    "headers": request.headers,
                })

        page.on("request", on_request)

        try:
            await page.goto(embed_url, wait_until="domcontentloaded", timeout=15000)

            for _ in range(PAGE_TIMEOUT):
                await asyncio.sleep(1)
                if any(
                    "mono.m3u8" in r["url"].lower() for r in captured_requests
                ):
                    break

        except Exception:
            pass

        final_streams = []
        mono_reqs = [r for r in captured_requests if "mono.m3u8" in r["url"].lower()]
        low_mono = next((r for r in mono_reqs if "low/" in r["url"].lower()), None)
        target_mono = low_mono or (mono_reqs[-1] if mono_reqs else None)

        if target_mono:
            url = target_mono["url"]
            headers = target_mono["headers"]
            final_streams.append({
                "title": title,
                "url": url,
                "logo": logo,
                "referer": headers.get("referer", embed_url),
                "user_agent": headers.get("user-agent", "Mozilla/5.0"),
                "origin": headers.get("origin", "https://embed.st"),
            })
        else:
            master_reqs = [
                r for r in captured_requests if ".m3u8" in r["url"].lower()
            ]
            if master_reqs:
                master_url = master_reqs[0]["url"]
                headers = master_reqs[0]["headers"]
                try:
                    async with aiohttp.ClientSession() as session:
                        req_headers = {
                            "User-Agent": headers.get("user-agent", "Mozilla/5.0"),
                            "Referer": headers.get("referer", embed_url),
                        }
                        async with session.get(
                            master_url, headers=req_headers, timeout=8
                        ) as resp:
                            if resp.status == 200:
                                text = await resp.text()
                                child_paths = [
                                    line.strip()
                                    for line in text.splitlines()
                                    if line.strip()
                                    and not line.startswith("#")
                                    and ".m3u8" in line.lower()
                                ]

                                if child_paths:
                                    best_path = next(
                                        (p for p in child_paths if "low/" in p.lower()),
                                        child_paths[0],
                                    )
                                    exact_url = urljoin(master_url, best_path)
                                    final_streams.append({
                                        "title": title,
                                        "url": exact_url,
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
                except Exception:
                    pass

        await context.close()
        return final_streams

    def _generate_m3u(self, streams):
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


if __name__ == "__main__":
    scraper = FootballJsonScraper()
    asyncio.run(scraper.run())
                    
