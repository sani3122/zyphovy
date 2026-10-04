#!/usr/bin/env python3
"""
TFLIX Live Stream M3U Scraper (Universal Flag & Logo Support)
- Scrapes live matches from https://tflix.su/watch
- Automatically loads HD flags for ALL 200+ countries dynamically via pycountry
- Searches online for club badges (TheSportsDB & Wikipedia)
- Safely processes WebP/PNG/JPG images
"""

import asyncio
import io
import os
import re
from datetime import datetime, timezone
import urllib.parse

import aiohttp
from camoufox.async_api import AsyncCamoufox
from PIL import Image, ImageDraw, ImageFont
import pycountry

TARGET_URL = "https://tflix.su/watch"
OUTPUT_FILE = "live_playlist.m3u"
LOGOS_DIR = "logos"
WAIT_PLAYER = 5  # Seconds to wait per stream tab

STREAM_RE = re.compile(
    r"(\.m3u8(\?|$)|/hls/|manifest\.mpd|/chunklist|/index\.m3u)", re.IGNORECASE
)

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


def normalize_team_name(name: str) -> str:
  """Translates common French/slug team names to standard English names."""
  clean = name.strip()
  lower = clean.lower()
  return TEAM_NAME_MAP.get(lower, clean)


async def fetch_online_team_logo(
    session: aiohttp.ClientSession, raw_team_name: str
) -> str:
  """Searches online for an official team logo/flag with dynamic 200+ country support."""
  team_name = normalize_team_name(raw_team_name)
  if not team_name:
    return ""

  lower_name = team_name.lower()

  # 1. Manual Regional Flags (For nations without 2-letter ISO codes)
  CUSTOM_FLAGS = {
      "scotland": "https://flagcdn.com/w320/gb-sct.png",
      "england": "https://flagcdn.com/w320/gb-eng.png",
      "wales": "https://flagcdn.com/w320/gb-wls.png",
      "northern ireland": "https://flagcdn.com/w320/gb-nir.png",
  }
  if lower_name in CUSTOM_FLAGS:
    return CUSTOM_FLAGS[lower_name]

  # 2. Dynamic Country Flag Generator (Works for Norway, Japan, Brazil, etc.)
  try:
    country = pycountry.countries.search_fuzzy(team_name)[0]
    iso_code = country.alpha_2.lower()
    return f"https://flagcdn.com/w320/{iso_code}.png"
  except Exception:
    pass

  # 3. Fallback: Search TheSportsDB API for Club Teams
  for query in [f"{team_name} Football", team_name]:
    try:
      encoded = urllib.parse.quote(query)
      sports_db_url = f"https://www.thesportsdb.com/api/v1/json/3/searchteams.php?t={encoded}"
      async with session.get(sports_db_url, timeout=5) as resp:
        if resp.status == 200:
          data = await resp.json()
          if data and data.get("teams"):
            badge = data["teams"][0].get("strBadge")
            if badge:
              return badge
    except Exception:
      pass

  # 4. Fallback: Wikipedia Thumbnail Search
  try:
    wiki_query = urllib.parse.quote(f"{team_name} FC")
    wiki_url = f"https://en.wikipedia.org/w/api.php?action=query&titles={wiki_query}&prop=pageimages&format=json&pithumbsize=400"
    async with session.get(wiki_url, timeout=5) as resp:
      if resp.status == 200:
        data = await resp.json()
        pages = data.get("query", {}).get("pages", {})
        for _, page_info in pages.items():
          if "thumbnail" in page_info:
            src = page_info["thumbnail"]["source"]
            if not src.endswith(".svg"):
              return src
  except Exception:
    pass

  return ""


async def load_image_safely(
    session: aiohttp.ClientSession, url: str
) -> Image.Image:
  """Downloads and safely converts image into a Pillow RGBA object."""
  if not url:
    return None
  try:
    async with session.get(url, timeout=8) as resp:
      if resp.status == 200:
        content_type = resp.headers.get("Content-Type", "").lower()
        if "svg" in content_type or url.endswith(".svg"):
          return None

        bytes_data = await resp.read()
        img = Image.open(io.BytesIO(bytes_data)).convert("RGBA")
        return img
  except Exception:
    pass
  return None


def get_fitted_font(
    text: str,
    font_path: str = "arial.ttf",
    max_width: int = 240,
    start_size: int = 28,
):
  """Dynamically scales font size down so long team names don't overflow."""
  size = start_size
  while size >= 14:
    try:
      font = ImageFont.truetype(font_path, size)
      bbox = font.getbbox(text)
      width = bbox[2] - bbox[0]
      if width <= max_width:
        return font
    except Exception:
      return ImageFont.load_default()
    size -= 2
  try:
    return ImageFont.truetype(font_path, 14)
  except Exception:
    return ImageFont.load_default()


def get_fitted_font(
    text: str,
    font_path: str = "arial.ttf",
    max_width: int = 240,
    start_size: int = 28,
):
  """Dynamically scales font size down so long team names don't overflow."""
  size = start_size
  while size >= 14:
    try:
      font = ImageFont.truetype(font_path, size)
      bbox = font.getbbox(text)
      width = bbox[2] - bbox[0]
      if width <= max_width:
        return font
    except Exception:
      return ImageFont.load_default()
    size -= 2
  try:
    return ImageFont.truetype(font_path, 14)
  except Exception:
    return ImageFont.load_default()


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
  output_path = os.path.join(LOGOS_DIR, f"{match_slug}.png")

  t1_name = (normalize_team_name(team1) or "TEAM 1").upper()
  t2_name = (normalize_team_name(team2) or "TEAM 2").upper()

  try:
    # 700x300 Dark Card Canvas
    canvas = Image.new("RGBA", (700, 300), (18, 20, 29, 255))
    draw = ImageDraw.Draw(canvas)

    # Load VS Font
    try:
      font_vs = ImageFont.truetype("arial.ttf", 48)
    except Exception:
      font_vs = ImageFont.load_default()

    # Load auto-fitted fonts for team names
    font_t1 = get_fitted_font(t1_name)
    font_t2 = get_fitted_font(t2_name)

    # 1. Team 1 Name (Left Side - Bright Red)
    draw.text(
        (180, 150),
        t1_name,
        fill=(255, 65, 65, 255),
        anchor="mm",
        font=font_t1,
    )

    # 2. VS Emblem (Center - Bright Yellow)
    draw.text(
        (350, 150), "VS", fill=(255, 215, 0, 255), anchor="mm", font=font_vs
    )

    # 3. Team 2 Name (Right Side - Bright Red)
    draw.text(
        (520, 150),
        t2_name,
        fill=(255, 65, 65, 255),
        anchor="mm",
        font=font_t2,
    )

    canvas.save(output_path, "PNG")
    return output_path
  except Exception as e:
    print(f"    [!] Banner creation error for {match_slug}: {e}")
    return ""


class TflixStreamScraper:

  def __init__(self, target_url=TARGET_URL):
    self.target_url = target_url
    self.captured_streams = []

  async def run(self):
    print(f"[*] Initializing Stealth Browser for target: {self.target_url}")
    async with AsyncCamoufox(headless=True) as browser:
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

          logo_path = await generate_vs_banner(
              http_session, team1, team2, team1_img, team2_img, slug
          )

          try:
            match_streams = await self._process_match_page(
                browser, url, title, logo_path
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
      """Scrapes match links and team names with timeout safety guards."""
      try:
        # Reduced timeout to 30s and use 'commit' so slow assets don't hang execution
        await page.goto(self.target_url, wait_until="commit", timeout=30000)
        await page.wait_for_selector('a[href*="/match/"]', timeout=15000)
        await asyncio.sleep(2)
      except Exception as e:
        print(
            f"[!] Target page load failed or timed out: {e}\n    -> Retrying"
            " page evaluation on current state..."
        )
        # Check if any content loaded before giving up completely
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

                // Safe fallback for search area if document.body is missing/incomplete
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

                    let team1Img = "";
                    let team2Img = "";
                    const card = a.closest('div, tr, li') || a.parentElement;
                    if (card) {
                        const imgs = Array.from(card.querySelectorAll('img'))
                            .map(i => i.src)
                            .filter(src => src && !src.includes('avatar') && !src.includes('logo'));
                        
                        if (imgs.length >= 2) {
                            team1Img = imgs[0];
                            team2Img = imgs[1];
                        } else if (imgs.length === 1) {
                            team1Img = imgs[0];
                        }
                    }

                    seen.add(href);
                    results.push([cleanTitle, href, team1, team2, team1Img, team2Img, slug]);
                }

                return results;
            }
        """)
      return matches

  async def _process_match_page(
      self, browser, match_url, match_title, match_logo
  ):
    """Navigates to match page and captures stream links."""
    context = await browser.new_context()
    page = await context.new_page()
    found_streams = []
    captured_urls = set()

    async def route_handler(route):
      request = route.request
      url = request.url

      if STREAM_RE.search(url) and url not in captured_urls:
        captured_urls.add(url)
        headers = await request.all_headers()
        found_streams.append({
            "title": match_title,
            "url": url,
            "logo": match_logo,
            "headers": headers,
            "referer": match_url,
        })
        print(f"        -> [HIT] Captured Stream: {url[:80]}")
      await route.continue_()

    await page.route("**/*", route_handler)

    try:
      await page.goto(match_url, wait_until="domcontentloaded", timeout=30000)
      await asyncio.sleep(2)

      stream_tabs = await page.evaluate("""
                () => {
                    const IGNORED_KEYWORDS = [
                        'APPS', 'DISCORD', 'DARK MODE', 'CHANNELS', 'ALL MATCHES', 
                        'TFLIX', 'JOIN', 'TELEGRAM', 'MIRROR', 'LOG IN', 'SIGN UP', 
                        'SEARCH', 'HOME', 'WATCH', 'MATCHES', 'REFRESH', 'CLOSE'
                    ];

                    const candidates = Array.from(document.querySelectorAll('button, div[class*="channel"], div[class*="server"], div[class*="tab"]'));
                    const validTabs = [];
                    const seenNames = new Set();

                    candidates.forEach((el, index) => {
                        if (el.closest('header, nav, footer, [class*="nav"], [class*="header"], [class*="footer"]')) return;

                        const text = el.textContent.trim();
                        const upper = text.toUpperCase();

                        if (!text || text.length > 35) return;
                        if (IGNORED_KEYWORDS.some(kw => upper.includes(kw))) return;

                        if (seenNames.has(upper)) return;
                        seenNames.add(upper);

                        validTabs.push({
                            index: index,
                            name: text,
                            isAQ: upper.includes('AQ')
                        });
                    });

                    validTabs.sort((a, b) => (b.isAQ ? 1 : 0) - (a.isAQ ? 1 : 0));
                    return validTabs;
                }
            """)

      if stream_tabs:
        print(f"    -> Found {len(stream_tabs)} stream tab(s).")
        for tab in stream_tabs:
          tab_name = tab["name"]
          tag = " [AQ Priority]" if tab["isAQ"] else ""
          print(f"    -> Switching to Stream Tab: {tab_name}{tag}")

          try:
            await page.evaluate(
                """
                            (idx) => {
                                const candidates = Array.from(document.querySelectorAll('button, div[class*="channel"], div[class*="server"], div[class*="tab"]'));
                                if (candidates[idx]) candidates[idx].click();
                            }
                        """,
                tab["index"],
            )
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