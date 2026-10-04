#!/usr/bin/env python3
"""
TFLIX Live Stream M3U Scraper (AQ Stream & IPTV Ready)
- Uses passive request sniffing to capture m3u8/HLS streams without stalling the player.
- Uses native Playwright click events so AQ stream buttons switch properly.
- Automatically clears the logos/ directory on each run to prevent clutter.
- Outputs full raw.githubusercontent.com URLs for IPTV player compatibility.
"""

import asyncio
from datetime import datetime, timezone
import io
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
WAIT_PLAYER = 6  # Seconds to wait per stream tab to allow HLS buffer

# Fallback GitHub settings
GITHUB_USER = "YOUR_GITHUB_USERNAME"
GITHUB_REPO = "YOUR_REPO_NAME"
GITHUB_BRANCH = "main"

# Non-English / Slug Name to Standard English Name Mappings
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
    """Deletes all previous banners to prevent storage bloat."""
    if os.path.exists(LOGOS_DIR):
        shutil.rmtree(LOGOS_DIR)
    os.makedirs(LOGOS_DIR, exist_ok=True)
    print(f"[+] Cleared and reset '{LOGOS_DIR}' folder.")


def get_base_logo_url() -> str:
    """Detects GitHub Actions environment or builds raw GitHub URL."""
    repo = os.getenv("GITHUB_REPOSITORY")
    if repo:
        return f"https://raw.githubusercontent.com/{repo}/{GITHUB_BRANCH}/{LOGOS_DIR}"
    return f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/{GITHUB_BRANCH}/{LOGOS_DIR}"


def normalize_team_name(name: str) -> str:
    """Translates common French/slug team names to standard English names."""
    if not name:
        return ""
    clean = name.strip()
    lower = clean.lower()
    return TEAM_NAME_MAP.get(lower, clean)


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
            width = bbox[2] - bbox[0]
            if width <= max_width:
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
    """Generates a text banner: TEAM 1 (Red) VS (Yellow) TEAM 2 (Red)."""
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

        # 1. Team 1 Name (Left Side - Red)
        draw.text(
            (180, 150),
            t1_name,
            fill=(255, 65, 65, 255),
            anchor="mm",
            font=font_t1,
        )

        # 2. VS Emblem (Center - Yellow)
        draw.text(
            (350, 150), "VS", fill=(255, 215, 0, 255), anchor="mm", font=font_vs
        )

        # 3. Team 2 Name (Right Side - Red)
        draw.text(
            (520, 150),
            t2_name,
            fill=(255, 65, 65, 255),
            anchor="mm",
            font=font_t2,
        )

        canvas.save(local_path, "PNG")
        return f"{get_base_logo_url()}/{file_name}"
    except Exception as e:
        print(f"    [!] Banner creation error for {match_slug}: {e}")
        return ""


def is_stream_url(url: str) -> bool:
    """Checks if a network request URL is an active HLS/M3U8 video stream."""
    lower = url.lower()

    # Ignore standard static web files
    if any(
        lower.endswith(ext)
        for ext in [
            ".js",
            ".css",
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".svg",
            ".woff",
            ".woff2",
            ".ttf",
            ".html",
            ".json",
        ]
    ):
        return False

    # Primary video stream signatures
    stream_keywords = [
        ".m3u8",
        ".mpd",
        "/hls/",
        "chunklist",
        "playlist",
        "manifest",
        "index.m3u",
        "/live/",
        "/stream/",
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
                for (
                    title,
                    url,
                    team1,
                    team2,
                    team1_img,
                    team2_img,
                    slug,
                ) in match_links:
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
                f"\n[*] Generating M3U Playlist ({len(self.captured_streams)} streams"
                " captured)..."
            )
            self._generate_m3u(self.captured_streams)

    async def _tier_1_find_live_matches(self, page):
        """Scrapes live match links and team names."""
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

                const allNodes = Array.from(rootArea.querySelectorAll('*'));
                const liveHeader = allNodes.find(el => 
                    el.children.length === 0 && 
                    el.textContent.trim().toUpperCase() === 'LIVE'
                );

                let liveContainer = null;
                if (liveHeader) {
                    let parent = liveHeader.parentElement;
                    while (parent && parent !== document.body) {
                        const links = parent.querySelectorAll('a[href*="/match/"]');
                        if (links.length > 0) {
                            liveContainer = parent;
                            break;
                        }
                        parent = parent.parentElement;
                    }
                }

                const searchArea = liveContainer || rootArea;
                if (!searchArea || typeof searchArea.querySelectorAll !== 'function') {
                    return results;
                }

                const matchLinks = Array.from(searchArea.querySelectorAll('a[href*="/match/"]'));

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

                    if (!cleanTitle) {
                        cleanTitle = "Live Match";
                    }

                    seen.add(href);
                    results.push([cleanTitle, href, team1, team2, "", "", slug]);
                }

                return results;
            }
        """)
        return matches

    async def _process_match_page(
        self, browser, match_url, match_title, match_logo
    ):
        """Navigates to match page and captures stream links passively."""
        context = await browser.new_context()
        page = await context.new_page()
        found_streams = []
        captured_urls = set()

        # Non-blocking passive request listener
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
                    "referer": match_url,
                })
                print(f"        -> [HIT] Captured Stream: {url[:85]}")

        page.on("request", handle_request)

        try:
            await page.goto(match_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)

            # Locate stream tab buttons on the page
            tabs_info = await page.evaluate("""
                () => {
                    const IGNORED = ['APPS', 'DISCORD', 'DARK MODE', 'CHANNELS', 'ALL MATCHES', 'TFLIX', 'JOIN', 'TELEGRAM', 'MIRROR', 'LOG IN', 'SIGN UP', 'SEARCH', 'HOME', 'WATCH', 'MATCHES', 'REFRESH', 'CLOSE', 'LIVE CHAT'];
                    
                    const elements = Array.from(document.querySelectorAll('button, div[class*="channel"], div[class*="server"], div[class*="tab"], div[class*="stream"]'));
                    const tabs = [];
                    
                    elements.forEach((el) => {
                        if (el.closest('header, nav, footer, [class*="nav"], [class*="header"], [class*="footer"], [class*="chat"]')) return;
                        
                        const text = el.textContent.trim();
                        const upper = text.toUpperCase();
                        
                        if (!text || text.length > 40) return;
                        if (IGNORED.some(kw => upper.includes(kw))) return;
                        
                        if (tabs.some(t => t.text === text)) return;

                        tabs.push({
                            text: text,
                            isAQ: upper.includes('AQ')
                        });
                    });

                    // Prioritize AQ tabs first
                    tabs.sort((a, b) => (b.isAQ ? 1 : 0) - (a.isAQ ? 1 : 0));
                    return tabs;
                }
            """)

            if tabs_info:
                print(f"    -> Found {len(tabs_info)} stream tab(s).")
                for tab in tabs_info:
                    tab_text = tab["text"]
                    tag = " [AQ Priority]" if tab["isAQ"] else ""
                    print(f"    -> Switching to Stream Tab: {tab_text}{tag}")

                    try:
                        # Use native Playwright click on element matching the exact text
                        tab_locator = page.get_by_text(tab_text, exact=False).last
                        if await tab_locator.count() > 0:
                            await tab_locator.click(timeout=3000)
                    except Exception:
                        # Fallback JS click
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
        """Outputs valid M3U playlist file with tvg-logo."""
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

            lines.append(
                f'#EXTINF:-1 tvg-logo="{logo}" group-title="Live Sports",{title}'
            )
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
        print(f"\n[+] Success! Playlist saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    scraper = TflixStreamScraper()
    asyncio.run(scraper.run())
