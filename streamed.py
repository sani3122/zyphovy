#!/usr/bin/env python3
"""
Ultra Fast Direct Scraper (No Browser Needed)
- Bypasses heavy browsers completely.
- Parses embed source HTML directly via HTTP requests.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import re
from urllib.parse import urljoin
import aiohttp

JSON_URL = "https://raw.githubusercontent.com/srhady/data/refs/heads/main/live_sports_playlist.json"
OUTPUT_FILE = "live_playlist.m3u"


async def fetch_url_text(session, url, headers=None):
    try:
        async with session.get(
            url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            if resp.status == 200:
                return await resp.text()
    except Exception:
        pass
    return None


async def extract_m3u8_from_embed(session, embed_url):
    """Embed URL-এর ভেতরে থাকা M3U8 লিংক সরাসরি HTML এক্সট্র্যাক্ট করে।"""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            " (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "Referer": embed_url,
    }

    html = await fetch_url_text(session, embed_url, headers)
    if not html:
        return None

    # HTML থেকে m3u8 লিংক খোঁজা
    m3u8_matches = re.findall(
        r'(https?://[^\s\'"]+?\.m3u8[^\s\'"]*)', html, re.IGNORECASE
    )

    if not m3u8_matches:
        # আপেক্ষিক পাথ (relative path) খোঁজা
        rel_matches = re.findall(
            r'[\'"]([^\'"\s]+?\.m3u8[^\'"\s]*)[\'"]', html, re.IGNORECASE
        )
        m3u8_matches = [
            urljoin(embed_url, m) for m in rel_matches if not m.startswith("#")
        ]

    if not m3u8_matches:
        return None

    # low/mono বা mono.m3u8 অগ্রাধিকার দেওয়া
    target_url = m3u8_matches[0]
    for m in m3u8_matches:
        if "mono.m3u8" in m.lower():
            target_url = m
            break

    # যদি মাস্টার প্লেলিস্ট হয়, তবে চাইল্ড লিংক এক্সট্র্যাক্ট করা
    if "mono.m3u8" not in target_url.lower():
        master_text = await fetch_url_text(session, target_url, headers)
        if master_text:
            child_paths = [
                line.strip()
                for line in master_text.splitlines()
                if line.strip()
                and not line.startswith("#")
                and ".m3u8" in line.lower()
            ]
            if child_paths:
                best_path = next(
                    (p for p in child_paths if "low/" in p.lower()),
                    child_paths[0],
                )
                target_url = urljoin(target_url, best_path)

    return target_url


class FootballJsonScraper:

    def __init__(self, json_url=JSON_URL):
        self.json_url = json_url
        self.captured_streams = []

    async def run(self):
        print("[*] JSON ডাটা ডাউনলোড করা হচ্ছে...")
        async with aiohttp.ClientSession() as session:
            async with session.get(self.json_url) as resp:
                if resp.status != 200:
                    print("[!] JSON ডাউনলোড ব্যর্থ হয়েছে।")
                    return
                all_matches = await resp.json(content_type=None)

            # সময় ফিল্টার উঠিয়ে দিয়ে সরাসরি ফুটবল ক্যাটাগরি ও স্ট্রিম আছে এমন সব ম্যাচ প্রসেস করা
            football_matches = [
                m
                for m in all_matches
                if str(m.get("Category", "")).strip().lower() == "football"
                and m.get("Streams")
            ]

            print(
                f"[+] মোট {len(football_matches)} টি ফুটবল ম্যাচ পাওয়া গেছে।"
            )

            for match in football_matches:
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
                    print(f"-> চেকিং এমবেড: {embed_url}")

                    m3u8_link = await extract_m3u8_from_embed(session, embed_url)
                    if m3u8_link:
                        print(f"   [SUCCESS] লিঙ্ক পাওয়া গেছে: {m3u8_link}")
                        self.captured_streams.append({
                            "title": stream_title,
                            "url": m3u8_link,
                            "logo": logo,
                            "referer": embed_url,
                            "user_agent": (
                                "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
                            ),
                            "origin": "https://embed.st",
                        })

        print(
            f"\n[*] M3U ফাইল তৈরি হচ্ছে ({len(self.captured_streams)} টি"
            " লিঙ্ক সহ)..."
        )
        self._generate_m3u(self.captured_streams)

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

            lines.append(
                f'#EXTINF:-1 tvg-logo="{logo}" group-title="Football",{title}'
            )
            lines.append(f"#EXTVLCOPT:http-referrer={ref}")
            lines.append(f"#EXTVLCOPT:http-user-agent={ua}")
            if origin:
                lines.append(f"#EXTVLCOPT:http-origin={origin}")

            kodi_props = [f"Referer={ref}", f"User-Agent={ua}"]
            if origin:
                kodi_props.append(f"Origin={origin}")
            lines.append(
                "#KODIPROP:inputstream.adaptive.stream_headers="
                + "&".join(kodi_props)
            )

            lines.append(url)
            lines.append("")

        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"[+] 'live_playlist.m3u' সফলভাবে আপডেট হয়েছে!")


if __name__ == "__main__":
    scraper = FootballJsonScraper()
    asyncio.run(scraper.run())
    
