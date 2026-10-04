#!/usr/bin/env python3
"""
TFLIX Master Live Stream M3U Scraper (IPTV Ready)
- Extracts real unencrypted .m3u8 / HLS media streams from embedded player frames.
- Ignores internal API routes (/api/chat/...) and DRM/AQ streams.
- Generates M3U playlist formatted with raw GitHub asset logos.
"""

import asyncio
from datetime import datetime, timezone
import os
import re
import shutil
import urllib.parse

import aiohttp
from camoufox.async_api import AsyncCamoufox
from PIL import Image, ImageDraw, ImageFont

TARGET_URL = "https://tflix.su/watch"
OUTPUT_FILE = "live_playlist.m3u"
LOGOS_DIR = "logos"
WAIT_PLAYER = 8  # Seconds to wait per stream tab to allow player iframe buffer

GITHUB_USER = "sani3122"
GITHUB_REPO = "zyphovy"
GITHUB_BRANCH = "main"

TEAM_NAME_MAP = {
    "rep tcheque": "Czech Republic",
    "republique tcheque": "Czech Republic",
    "pays bas": "Netherlands",
    "angleterre": "England",
    "espagne": "Spain",
    "allemagne": "Germany",
    "italie": "Italy",
    "etats unis": "United States",
    "arabie saoudite": "Saudi Arabia",
    "coree du sud": "South Korea",
}


def clear_logos_dir():
    if os.path.exists(LOGOS_DIR):
        shutil.rmtree(LOGOS_DIR)
    os.makedirs(LOGOS_DIR, exist_ok=True)
    print(f"[+] Cleared and reset '{LOGOS_DIR}' folder.")


def get_base_logo_url() -> str:
    repo = os.getenv("GITHUB_REPOSITORY")
    if repo:
        return f"https://raw.githubusercontent.com/{repo}/{GITHUB_BRANCH}/{LOGOS_DIR}"
    return f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/{GITHUB_BRANCH}/{LOGOS_DIR}"


def normalize_team_name(name: str) -> str:
    if not name:
        return ""
    clean = name.strip()
    return TEAM_NAME_MAP.get(clean.lower(), clean)


def get_system_font(size: int = 28):
    font_candidates = [
        "arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for font_path in font_candidates:
        try:
            return ImageFont.truetype(font_path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def get_fitted_font(text: str, max_width: int = 240, start_size: int = 28):
    size = start_size
    while size >= 14:
        font = get_system_font(size)
        try:
            bbox = font.getbbox(text)
            if (bbox[2] - bbox[0]) <= max_width:
                return font
        except Exception:
            return ImageFont.load_default()
        size -= 2
    return get_system_font(14)


async def generate_vs_banner(
    session: aiohttp.ClientSession,
    team1: str,
    team2: str,
    team1_img: str,
    team2_img: str,
    match_slug: str,
) -> str:
    os.makedirs(LOGOS_DIR, exist_ok=True)
    file_name = f"{match_slug}.png"
    local_path = os.path.join(LOGOS_DIR, file_name)

    t1_name = (normalize_team_name(team1) or "TEAM 1").upper()
    t2_name = (normalize_team_name(team2) or "TEAM 2").upper()

    try:
        canvas = Image.new("RGBA", (700, 300), (18, 20, 29, 255))
        draw = ImageDraw.Draw(canvas)

        font_vs = get_system_font(48)
        font_t1 = get_fitted_font(t1_name)
        font_t2 = get_fitted_font(t2_name)

        draw.text((180, 150), t1_name, fill=(255, 65, 65, 255), anchor="mm", font=font_t1)
        draw.text((350, 150), "VS", fill=(255, 215, 0, 255), anchor="mm", font=font_vs)
        draw.text((520, 150), t2_name, fill=(255, 65, 65, 255), anchor="mm", font=font_t2)

        canvas.save(local_path, "PNG")
        return f"{get_base_logo_url()}/{file_name}"
    except Exception as e:
        print(f"    [!] Banner creation error for {match_slug}: {e}")
        return ""


def is_stream_url(url: str) -> bool:
    """Strictly validates actual HLS / M3U8 streaming media feeds."""
    lower = url.lower()

    # Reject internal TFLIX API routes, static web resources, and DRM DASH (.mpd)
    if "tflix.su/api/" in lower or "/api/chat/" in lower:
        return False

    if any(
        lower.endswith(ext)
        for ext in [
            ".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", 
            ".woff", ".woff2", ".ttf", ".html", ".json", ".mpd", ".ico"
        ]
    ):
        return False

    # Targeted HLS / M3U8 video stream signatures
    stream_keywords = [
        ".m3u8",
        "/hls/",
        "chunklist",
        "playlist.m3u",
        "index.m3u",
        "m3u8=",
        "/mono.m3u8",
        "/master.m3u8",
        "/index.m3u8"
    ]
    return any(kw in lower for kw in stream_keywords)


class TflixStreamScraper:

    def __init__(self, target_url=TARGET_URL):
        self.target_url = target_url
        self.captured_streams = []

    async def run(self):
        clear_logos_dir()

        print(f"[*] Initializing Stealth Browser for target: {self.target_url}")
        is_headless = os.environ.get("HEADLESS", "true").lower() == "true"
        async with AsyncCamoufox(headless=is_headless) as browser:
            page = await browser.new_page()

            print("\n[Tier 1] Scanning 'LIVE' Section for Matches...")
            match_links = await self._tier_1_find_live_matches(page)
            print(f"    -> Found {len(match_links)} live match(es).")

            async with aiohttp.ClientSession() as http_session:
                for title, url, team1, team2, team1_img, team2_img, slug in match_links:
                    print(f"\n[Tier 2] Processing Match: {title}")

                    logo_url = await generate_vs_banner(
                        http_session, team1, team2, team1_img, team2_img, slug
                    )

                    try:
                        match_streams = await self._process_match_page(
                            browser, url, title, logo_url
                        )
                        self.captured_streams.extend(match_streams)
                    except Exception as e:
                        print(f"    [!] Error processing match {title}: {str(e)}")

            print(
                f"\n[*] Generating M3U Playlist ({len(self.captured_streams)} streams captured)..."
            )
            self._generate_m3u(self.captured_streams)

    async def _tier_1_find_live_matches(self, page):
        try:
            await page.goto(self.target_url, wait_until="commit", timeout=30000)
            await page.wait_for_selector('a[href*="/match/"]', timeout=15000)
            await asyncio.sleep(2)
        except Exception as e:
            print(f"[!] Target page load failed or timed out: {e}")
            body_exists = await page.evaluate(
                "() => !!document.body && document.body.children.length > 0"
            )
            if not body_exists:
                print("    [!] Page body is completely empty. Skipping scanning.")
                return []

        matches = await page.evaluate(r"""
            () => {
                const results = [];
                const seen = new Set();
                const rootArea = document.body || document.documentElement || document;
                if (!rootArea) return results;

                const matchLinks = Array.from(rootArea.querySelectorAll('a[href*="/match/"]'));

                for (const a of matchLinks) {
                    const href = a.href;
                    if (href.includes('/channel/') || href.includes('/channels/')) continue;
                    if (!href.includes('/match/') || seen.has(href)) continue;

                    let cleanTitle = "";
                    let team1 = "";
                    let team2 = "";
                    let slug = "match";

                    try {
                        const matchSlug = href.split('/match/')[1].split('?')[0];
                        slug = matchSlug;
                        const slugNoId = matchSlug.replace(/-\d+$/, '');
                        
                        if (slugNoId.includes('-vs-')) {
                            const teams = slugNoId.split('-vs-');
                            team1 = teams[0].replace(/-/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
                            team2 = teams[1].replace(/-/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
                            cleanTitle = `${team1} VS ${team2}`;
                        }
                    } catch(e) {}

                    if (!cleanTitle) cleanTitle = "Live Match";

                    seen.add(href);
                    results.push([cleanTitle, href, team1, team2, "", "", slug]);
                }
                return results;
            }
        """)
        return matches

    async def _process_match_page(self, browser, match_url, match_title, match_logo):
        context = await browser.new_context()
        page = await context.new_page()
        found_streams = []
        captured_urls = set()

        # Listen at CONTEXT level to capture cross-origin iframe requests
        def handle_request(request):
            url = request.url
            if is_stream_url(url) and url not in captured_urls:
                captured_urls.add(url)
                headers = request.headers
                found_streams.append({
                    "title": match_title,
                    "url": url,
                    "logo": match_logo,
                    "headers": headers,
                    "referer": request.headers.get("referer", match_url),
                })
                print(f"        -> [HIT] Captured Stream: {url[:85]}")

        context.on("request", handle_request)

        try:
            await page.goto(match_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)

            # Discover non-DRM channel tabs
            tabs_info = await page.evaluate("""
                () => {
                    const IGNORED = [
                        'APPS', 'DISCORD', 'DARK MODE', 'CHANNELS', 'ALL MATCHES', 
                        'TFLIX', 'JOIN', 'TELEGRAM', 'MIRROR', 'LOG IN', 'SIGN UP', 
                        'SEARCH', 'HOME', 'WATCH', 'MATCHES', 'REFRESH', 'CLOSE', 
                        'LIVE CHAT', 'RELOAD STREAM', 'STATS', 'EVENTS', 'LINEUP', 'STATSEVENTSLINEUP'
                    ];
                    
                    const elements = Array.from(document.querySelectorAll('button, div[class*="channel"], div[class*="server"], div[class*="tab"], div[class*="stream"]'));
                    const tabs = [];
                    
                    elements.forEach((el) => {
                        if (el.closest('header, nav, footer, [class*="nav"], [class*="header"], [class*="footer"], [class*="chat"]')) return;
                        const text = el.textContent.trim();
                        const upper = text.toUpperCase();
                        
                        if (!text || text.length > 40) return;
                        if (IGNORED.some(kw => upper.includes(kw))) return;
                        if (upper.includes('AQ')) return;
                        
                        if (tabs.some(t => t.text === text)) return;
                        tabs.push({ text: text });
                    });

                    return tabs;
                }
            """)

            if tabs_info:
                print(f"    -> Found {len(tabs_info)} standard stream tab(s).")
                for tab in tabs_info:
                    tab_text = tab["text"]
                    print(f"    -> Switching to Stream Tab: {tab_text}")

                    try:
                        tab_locator = page.get_by_text(tab_text, exact=False).last
                        if await tab_locator.count() > 0:
                            await tab_locator.click(timeout=3000)
                    except Exception:
                        await page.evaluate(
                            """
                            (txt) => {
                                const els = Array.from(document.querySelectorAll('*'));
                                const match = els.find(el => el.children.length <= 2 && el.textContent.trim() === txt);
                                if (match) match.click();
                            }
                            """,
                            tab_text,
                        )

                    # Trigger playback on video/iframe containers to unblock player initialization
                    try:
                        for frame in page.frames:
                            if frame != page.main_frame:
                                await frame.evaluate("""() => {
                                    const v = document.querySelector('video');
                                    if (v) v.play().catch(()=>{});
                                    const overlay = document.querySelector('.play-button, .vjs-big-play-button, #play-btn');
                                    if (overlay) overlay.click();
                                }""").catch(lambda e: None)
                    except Exception:
                        pass

                    await asyncio.sleep(WAIT_PLAYER)
            else:
                await asyncio.sleep(WAIT_PLAYER)

        except Exception as e:
            print(f"    [!] Error during match stream extraction: {e}")
        finally:
            await context.close()

        for idx, s in enumerate(found_streams):
            s["logo"] = match_logo
            if len(found_streams) > 1:
                s["title"] = f"{s['title']} (Stream {idx + 1})"

        return found_streams

    def _generate_m3u(self, streams):
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines = [
            "#EXTM3U",
            f"# generated: {ts}",
            f"# source: {self.target_url}",
            f"# streams: {len(streams)}",
            "",
        ]

        for s in streams:
            title = s["title"].replace('"', "'")
            url = s["url"]
            logo = s.get("logo", "")
            ref = s["headers"].get("referer", s["referer"])
            ua = s["headers"].get("user-agent", "Mozilla/5.0")
            origin = s["headers"].get("origin", "")

            lines.append(f'#EXTINF:-1 tvg-logo="{logo}" group-title="Live Sports",{title}')
            lines.append(f"#EXTVLCOPT:http-referrer={ref}")
            lines.append(f"#EXTVLCOPT:http-user-agent={ua}")
            if origin:
                lines.append(f"#EXTVLCOPT:http-origin={origin}")

            kodi_props = [f"Referer={ref}", f"User-Agent={ua}"]
            if origin:
                kodi_props.append(f"Origin={origin}")
            lines.append("#KODIPROP:inputstream.adaptive.stream_headers=" + "&".join(kodi_props))

            lines.append(url)
            lines.append("")

        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"\n[+] Success! Playlist saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    scraper = TflixStreamScraper()
    asyncio.run(scraper.run())
