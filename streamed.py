#!/usr/bin/env python3
"""
Fast & Reliable Playwright Scraper for embed.st
- Intercepts live m3u8 streams executed via embed.st JavaScript player.
- Optimized for speed to save GitHub Actions runner time.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import os
from urllib.parse import urljoin
import aiohttp
from playwright.async_api import async_playwright

JSON_URL = "https://raw.githubusercontent.com/srhady/data/refs/heads/main/live_sports_playlist.json"
OUTPUT_FILE = "live_playlist.m3u"


async def fetch_json_data(url: str) -> list:
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status == 200:
                return await response.json(content_type=None)
            return []


class FootballJsonScraper:

    def __init__(self, json_url=JSON_URL):
        self.json_url = json_url
        self.captured_streams = []

    async def run(self):
        print("[*] JSON ডাটা ডাউনলোড করা হচ্ছে...")
        all_matches = await fetch_json_data(self.json_url)

        football_matches = [
            m for m in all_matches
            if str(m.get("Category", "")).strip().lower() == "football" and m.get("Streams")
        ]

        print(f"[+] মোট {len(football_matches)} টি ফুটবল ম্যাচ পাওয়া গেছে।")

        if not football_matches:
            return

        print("\n[*] Fast Playwright Chromium জেনারেট করা হচ্ছে...")
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-accelerated-2d-canvas",
                    "--no-first-run",
                    "--no-zygote",
                    "--disable-gpu",
                ]
            )

            for match in football_matches:
                t1 = match.get("Team 1 Name", "").strip()
                t2 = match.get("Team 2 Name", "").strip()
                display_title = f"{t1} VS {t2}" if (t1 and t2) else match.get("Match Title", "Football Match")
                logo = match.get("Match Poster", "")
                streams = match.get("Streams", [])

                hd_streams = [s for s in streams if str(s.get("Quality", "")).strip().upper() == "HD"]

                for stream in hd_streams:
                    embed_url = stream.get("Embed_URL", "")
                    source_name = stream.get("Source", "Server")
                    lang = stream.get("Language", "")
                    if not embed_url:
                        continue

                    stream_title = f"{display_title} ({source_name} - {lang})"
                    print(f"-> চেকিং এমবেড: {embed_url}")

                    captured = await self._scrape_embed_url(browser, embed_url, stream_title, logo)
                    if captured:
                        self.captured_streams.extend(captured)

            await browser.close()

        print(f"\n[*] M3U তৈরি করা হচ্ছে ({len(self.captured_streams)} টি লিঙ্ক পাওয়া গেছে)...")
        self._generate_m3u(self.captured_streams)

    async def _scrape_embed_url(self, browser, embed_url, title, logo):
        context = await browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        )
        page = await context.new_page()

        # স্পিড বাড়াতে অহেতুক ফাইল ব্লক করা
        await page.route(
            "**/*.{png,jpg,jpeg,svg,webp,woff,woff2,ttf,css}",
            lambda route: route.abort()
        )

        captured_urls = set()
        found_streams = []

        def handle_request(request):
            url = request.url
            lower = url.lower()

            if ".m3u8" in lower and url not in captured_urls:
                captured_urls.add(url)
                headers = request.headers
                found_streams.append({
                    "title": title,
                    "url": url,
                    "logo": logo,
                    "referer": headers.get("referer", embed_url),
                    "user_agent": headers.get("user-agent", "Mozilla/5.0"),
                    "origin": headers.get("origin", "https://embed.st"),
                })
                print(f"   [INTERCEPTED] {url[:80]}...")

        page.on("request", handle_request)

        try:
            # পেজ লোড হওয়া মাত্রই ইন্টারসেপ্ট চালু
            await page.goto(embed_url, wait_until="commit", timeout=15000)

            # প্লেয়ার চালু করার জন্য সেন্টারে একটা ক্লিক দেওয়া
            await asyncio.sleep(2)
            try:
                await page.mouse.click(640, 360)
            except Exception:
                pass

            # লিঙ্ক পাওয়া মাত্রই আর দেরি না করে লুপ ব্রেক করা
            for _ in range(8):
                await asyncio.sleep(1)
                if any("mono.m3u8" in s["url"].lower() for s in found_streams):
                    break

        except Exception as e:
            pass
        finally:
            await context.close()

        # prioritize low/mono or mono stream
        mono_list = [s for s in found_streams if "mono.m3u8" in s["url"].lower()]
        if mono_list:
            low_variant = next((s for s in mono_list if "low/" in s["url"].lower()), None)
            return [low_variant or mono_list[-1]]

        if found_streams:
            return [found_streams[-1]]

        return []

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

        print(f"[+] 'live_playlist.m3u' আপডেট সফল হয়েছে!")


if __name__ == "__main__":
    scraper = FootballJsonScraper()
    asyncio.run(scraper.run())
    
