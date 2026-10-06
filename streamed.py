#!/usr/bin/env python3
"""
Bulletproof Hard-to-Hard Scraper for embed.st
- Captures direct mono.m3u8 streams via Playwright Chromium.
- Automatically resolves inner mono.m3u8 variants from Master playlist.m3u8 if needed.
"""

import asyncio
from datetime import datetime, timezone
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


async def resolve_mono_from_master(session, master_url, referer, user_agent):
    """
    যদি মাস্টার playlist.m3u8 ক্যাপচার হয়, তবে এই ফাংশনটি ফাইল ডাউনলোড করে
    ভেতরের আসল mono.m3u8 বের করে আনে।
    """
    headers = {
        "User-Agent": user_agent,
        "Referer": referer,
        "Origin": "https://embed.st",
    }
    try:
        async with session.get(master_url, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            if resp.status == 200:
                text = await resp.text()
                lines = [
                    line.strip()
                    for line in text.splitlines()
                    if line.strip() and not line.startswith("#")
                ]

                # mono.m3u8 বা low/mono.m3u8 অগ্রাধিকার দেওয়া
                low_mono = next((l for l in lines if "low/mono.m3u8" in l.lower()), None)
                any_mono = next((l for l in lines if "mono.m3u8" in l.lower()), None)
                target_path = low_mono or any_mono or (lines[0] if lines else None)

                if target_path:
                    resolved_url = urljoin(master_url, target_path)
                    print(f"   [RESOLVED MASTER -> MONO] {resolved_url}")
                    return resolved_url
    except Exception as e:
        print(f"   [MASTER RESOLVE ERROR] {e}")

    return master_url


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

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ]
            )

            async with aiohttp.ClientSession() as http_session:
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
                        print(f"\n-> চেকিং এমবেড: {embed_url}")

                        captured = await self._scrape_embed_url(
                            browser, http_session, embed_url, stream_title, logo
                        )
                        if captured:
                            self.captured_streams.extend(captured)

            await browser.close()

        print(f"\n[*] M3U ফাইল তৈরি করা হচ্ছে ({len(self.captured_streams)} টি লিঙ্ক পাওয়া গেছে)...")
        self._generate_m3u(self.captured_streams)

    async def _scrape_embed_url(self, browser, http_session, embed_url, title, logo):
        context = await browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        )
        page = await context.new_page()

        # Stealth Script (Bot Detection Bypass)
        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        # শুধুমাত্র হেভি মিডিয়া/ইমেজ ব্লক করা হচ্ছে (CSS অন থাকবে যাতে প্লেয়ার কাজ করে)
        await page.route(
            "**/*.{png,jpg,jpeg,svg,webp,woff,woff2,ttf,mp4,ts}",
            lambda route: route.abort()
        )

        captured_requests = []

        def handle_request(request):
            url = request.url
            if ".m3u8" in url.lower():
                captured_requests.append({
                    "url": url,
                    "headers": request.headers,
                })
                print(f"   [CAPTURED REQUEST] {url}")

        page.on("request", handle_request)

        try:
            await page.goto(embed_url, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(3)

            # প্লেয়ার চালু করতে স্ক্রিনের মাঝে অটো-ক্লিক
            try:
                await page.mouse.click(640, 360)
            except Exception:
                pass

            # Iframes থাকলে সেগুলোর ভেতরের প্লে বাটন খোঁজা
            for frame in page.frames:
                try:
                    play_btn = await frame.query_selector("button, .vjs-big-play-button, #player, .play-btn")
                    if play_btn:
                        await play_btn.click(force=True)
                except Exception:
                    pass

            # নেটওয়ার্ক কলের জন্য ১০ সেকেন্ড অপেক্ষা
            for _ in range(10):
                await asyncio.sleep(1)
                if any("mono.m3u8" in r["url"].lower() for r in captured_requests):
                    print("   [SUCCESS] mono.m3u8 সরাসরি ইন্টারসেপ্ট হয়েছে!")
                    break

        except Exception as e:
            print(f"   [PAGE ERROR] {e}")
        finally:
            await context.close()

        if not captured_requests:
            return []

        # ১. যদি সরাসরি mono.m3u8 পেয়ে যায়
        mono_reqs = [r for r in captured_requests if "mono.m3u8" in r["url"].lower()]
        if mono_reqs:
            low_mono = next((r for r in mono_reqs if "low/" in r["url"].lower()), None)
            target = low_mono or mono_reqs[-1]
            return [{
                "title": title,
                "url": target["url"],
                "logo": logo,
                "referer": target["headers"].get("referer", embed_url),
                "user_agent": target["headers"].get("user-agent", "Mozilla/5.0"),
                "origin": target["headers"].get("origin", "https://embed.st"),
            }]

        # ২. যদি শুধু playlist.m3u8 (Master Manifest) ক্যাপচার হয়, তবে অটোমেটিক mono.m3u8 বের করা
        master_reqs = [r for r in captured_requests if ".m3u8" in r["url"].lower()]
        if master_reqs:
            master_target = master_reqs[0]
            master_url = master_target["url"]
            headers = master_target["headers"]
            ref = headers.get("referer", embed_url)
            ua = headers.get("user-agent", "Mozilla/5.0")

            final_mono_url = await resolve_mono_from_master(http_session, master_url, ref, ua)

            return [{
                "title": title,
                "url": final_mono_url,
                "logo": logo,
                "referer": ref,
                "user_agent": ua,
                "origin": headers.get("origin", "https://embed.st"),
            }]

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

        print(f"\n[+] 'live_playlist.m3u' ফাইল সফলভাবে আপডেট হয়েছে!")


if __name__ == "__main__":
    scraper = FootballJsonScraper()
    asyncio.run(scraper.run())
            
