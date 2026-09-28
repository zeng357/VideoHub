# VideoHub · Multi-Site Video Batch Downloader

🎬 See also: `1_Documentation/User_Guide.md` (UI areas + 5-step quick start + site usage + FAQ) | `How_It_Works.md` (principles) | `Disclaimer.md` (bilingual) | `FAQ.md`.

A video downloader that runs on Windows and supports multiple video sites: built-in crawlers (A** Anime, B** Bullet-Screen Video, Y** Anime, D** Short Video, Auto-Scan) + a plugin-based site extension system — drop a new site plugin into `sites_data/` and it registers automatically. Features: in-site search to load episode tables, check individual / whole / all episodes, choose a speed tier (Turbo / Fast / Standard / Steady) and batch download with one click; B** videos are auto-muxed into a single mp4 (audio + video); D** short videos support human-controlled random grabbing.

---

## 1. UI Guide

After launching (double-click `start.bat`), the dark-themed main window contains:

| Area | Description |
|---|---|
| Left site nav | Choose a video site (built-in sites + plugin sites) |
| Keyword / address bar | Enter a title keyword, or a site homepage URL (Auto-Scan mode) |
| Search button | Searches the selected site and locates the target series page |
| Load episodes | First detects the **total episode count**, then reads the full episode list (auto-scroll, no more "only episode 1") |
| Episode table | Check single episodes, a range, or all |
| Speed tier | Turbo (32 threads) / Fast / High / Standard (6) / Steady. **Pick speed first, then click Download** |
| Start download | Batch download checked episodes; dual progress bars + speed |
| Log window | Live log of search → scan → download → mux → done; errors shown here |
| Done notice | Popup when finished; videos saved to `Download/<site>/<title>/<ep>/` |

> **Keep the black console window open**: the program shows progress there. Close the console or the main window to exit.

## 2. Supported Sites

| Site | Notes |
|---|---|
| A** Anime | Search `/search?wd=`, detail `/detail/{id}`; **auto-switch between multiple sources** if one fails |
| B** Bullet-Screen Video | Audio/video are separate (DASH); the app auto-muxes via ffmpeg. For login-gated episodes, log in in your browser and paste the Cookie into the "B** Cookie" field |
| Y** Anime | Search `/video-search/...html?wd=`, play `/video-play/...html`; update plugin if site changes |
| D** Short Video | Random grab on the featured page: click "🎲 Random Grab" (turns into "Stop"), human-controlled by time/count; watermarked-free mp4 direct links |
| Auto-Scan | Enter a homepage URL; list all videos and check single / multiple / all |
| Plugin sites | Put a plugin in `sites_data/` and restart; auto-registered |

## 3. Adding a New Site (Plugin)

**No code → use a template**: copy `3_Site_Extensions/sites_data/template_crawler.example.py`, fill in the search / episode / play-page rules, save, restart.

**Python → three interfaces**:
1. Copy template to `<site>_crawler.py`;
2. Implement: `search_series(keyword)` → `[{url, title}]`; `get_episode_count(url)` → total; `collect_episode_videos(url, count)` → per-episode video URLs;
3. Return format: `[{episode_num, title, video_url, video_type, referer}]`;
4. Place in `sites_data/`, restart.

## 4. Environment & Install

Requires Python 3.10+ (verified on 3.10):

| Dependency | Purpose |
|---|---|
| DrissionPage | Browser automation (scan / play-page parsing) |
| aiohttp / aiofiles | Concurrent downloads |
| pycryptodome | B** stream decryption (AES-128) |
| requests / lxml | HTTP + parsing |

```bash
pip install -r requirements.txt
```

**ffmpeg** (required for B** audio/video mux): install from ffmpeg.org; make sure `C:\Program Files\ffmpe\bin\ffmpeg.exe` exists (or edit `config.py`).

## 5. One-Click Start

Unzip and double-click `start.bat` (auto-checks Python, dependencies, and launches the GUI).

If a module is missing: `pip install <module>` and restart.

## 6. FAQ

| Problem | Cause / Solution |
|---|---|
| Black window closes instantly | Python / deps missing; run `python gui.py` from a terminal to see the error |
| Only episode 1 downloads | Old lazy-load bug; fixed. Confirm the table shows all episodes before checking |
| Download hangs on click | Pick a speed tier first; disable proxy/VPN; the built-in 90s watchdog auto-interrupts |
| Rate-limited / blocked by site | Switch to Standard/Steady; wait a few hours; the app already adds random delays + UA rotation + simulated scroll |
| B** video has no audio | DASH split; check ffmpeg installed and `config.py` path |
| Search garbled / back to homepage | Site revamped; update the plugin |
| First source fails | App auto-switches to the next source and keeps the working one |

Full 15-item FAQ: `1_Documentation/FAQ.md`.

## 7. Disclaimer (EN)

**This software (VideoHub) is for learning and personal use only. Commercial use is prohibited.**

1. **Purpose**: for learning browser automation, crawling, and audio/video processing only; download only content you are **entitled** to (your own works, authorized resources, public free content).
2. **Copyright**: content belongs to the original creators and platforms; do not download or redistribute protected content without authorization (paid films, member-only content, unauthorized reprints); support the original creators.
3. **Responsibility**: users must follow local laws and the target site's Terms of Service; any legal consequences are the user's own responsibility.
4. **Technical boundary**: this tool contains no cracking or paywall-bypass features; logged-in/paid content is to be used within legal authorization; sites may change APIs or enable anti-crawling at any time.
5. **Site stability**: third-party sites (e.g., weak servers) may be temporarily unavailable due to rate limiting or maintenance; use responsibly and do not stress target sites.

**Download only what you are entitled to; respect copyright.**

中文版见 `1_Documentation/Disclaimer.md`（中英双语）。