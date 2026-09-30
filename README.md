# VideoToolkit · Video Toolkit (视频工具集)

A Windows desktop video toolbox built with Python + Tkinter. One application, four working pages:
**Home (batch downloader)** · **General Download (long videos)** · **Resource Sniffer (short videos / web media)** · **Video Converter** — plus a **secure Login Vault** for cookies.

> **Page split by design:** the Home page is for anime / drama / long-form series; the **General Download** page parses and downloads long videos (anime, B** bullet-screen videos, etc.); the **Resource Sniffer** page is for short videos and general web media. Pick the page that matches your content.

See also: `1_Documentation/User_Guide.md` (full manual) | `How_It_Works.md` (principles) | `FAQ.md` | `Disclaimer.md` (bilingual).

---

## 1. Feature Overview

| Module | What it does |
| --- | --- |
| **Home** | Multi-site search (A** Anime, B** Bullet-Screen, Y** Anime, D** Short Video, Auto-Scan, plugin sites) → detect total episode count → load full episode table → check single / range / all → pick a speed tier → batch download with dual progress bars |
| **General Download** | Long-video download page. yt-dlp engine parses the real stream; B** DASH audio+video are auto-muxed into one mp4 via ffmpeg; if parsing or download fails, an automatic browser-extract fallback opens the page and grabs the direct video address |
| **Resource Sniffer** | Short-video / web-media page. Open or paste any page, then play — the tool captures real media addresses in two channels (network listener + video tag); check the list and download; custom save folder |
| **Video Converter** | Lossless / compatible video format conversion between common formats (mp4, mkv, webm, mov, ts, flv, avi …) via ffmpeg |
| **Login Vault** | Encrypted storage for site cookies. Only this PC can open it; on another PC a password is required (must contain letters + digits + symbols, ≥ 6 chars); 4 wrong attempts destroy the stored data; changing the password requires answering your security questions |

---

## 2. UI Guide

Dark-themed main window with a **left navigation / tab bar**:

| Area | Description |
| --- | --- |
| Left site nav | Choose a video site (built-in sites + plugin sites) |
| Keyword / address bar | Enter a title keyword, or a site homepage URL (Auto-Scan mode) |
| Search button | Searches the selected site and locates the target series page |
| Load episodes | First detects the **total episode count**, then reads the full episode list (no more "only episode 1") |
| Episode table | Check single episodes, a range, or all |
| Speed tier | Turbo (32 threads) / Fast (16) / Standard (6) / Steady (3). **Pick speed first, then click Download** |
| Start download | Batch download checked episodes; dual progress bars + speed; finished popup |
| Log window | Live log of search → scan → download → mux → done; errors shown here |

**Tabs above the main area** open the other modules: `General Download`, `Resource Sniffer`, `Video Converter`, `Settings / Login Vault`.

> **Keep the black console window open**: the program shows progress there. Close the console or the main window to exit.

---

## 3. Supported Sites

| Site | Notes |
| --- | --- |
| A** Anime | Search `/search?wd=`, detail `/detail/{id}`; **auto-switches between multiple sources** if one fails |
| B** Bullet-Screen Video | Audio/video are separate (DASH); the app auto-muxes via ffmpeg. For login-gated episodes, log in in your browser and paste the Cookie into the vault |
| Y** Anime | Search `/video-search/...html?wd=`, play `/video-play/...html`; update plugin if the site changes |
| D** Short Video | Random grab on the featured page: click "Random Grab" (turns into "Stop"), human-controlled by time/count |
| Auto-Scan | Enter a homepage URL; list all videos; check single / multiple / all |
| Plugin sites | Put a plugin in `sites_data/` and restart; auto-registered |

**Anti-blocking built in**: random delays, UA rotation, simulated scrolling, and a 90s watchdog on downloads — switch to Standard / Steady if a server is rate-limiting you.

---

## 4. Adding a New Site (Plugin)

**No code → use a template**: copy `3_Site_Extensions/sites_data/template_crawler.example.py`, fill in the search / episode / play-page rules, save, restart.
**Python → three interfaces**:
1. Copy the template to `<site>_crawler.py`;
2. Implement: `search_series(keyword)` → `[{url, title}]`; `get_episode_count(url)` → total; `collect_episode_videos(url, count)` → per-episode video URLs;
3. Return format: `[{episode_num, title, video_url, video_type, referer}]`;
4. Place in `sites_data/`, restart.

---

## 5. Environment & Install

Requires Python 3.10+ (verified on 3.10):

| Dependency | Purpose |
| --- | --- |
| DrissionPage | Browser automation (scan / play-page parsing / sniffing) |
| aiohttp / aiofiles | Concurrent downloads |
| pycryptodome | Stream decryption (AES-128) |
| requests / lxml | HTTP + parsing |
| yt-dlp | General-download stream parsing (bundled `yt-dlp.exe`) |
| ffmpeg | Audio/video mux + format conversion (bundled `ffmpeg.exe`) |

```bash
pip install -r requirements.txt
```

---

## 6. One-Click Start

Unzip and double-click `start.bat` (auto-checks Python, dependencies, and launches the GUI).
If a module is missing: `pip install <module>` and restart.

---

## 7. FAQ (highlights)

| Problem | Cause / Solution |
| --- | --- |
| Black window closes instantly | Python / deps missing; run `python gui.py` from a terminal to see the error |
| Only episode 1 downloads | Old lazy-load bug; fixed. Confirm the table shows all episodes before checking |
| Download hangs on click | Pick a speed tier first; disable proxy/VPN; the built-in 90s watchdog auto-interrupts |
| Rate-limited / blocked by site | Switch to Standard/Steady; wait a few hours; the app already adds random delays + UA rotation + simulated scroll |
| B** video has no audio | DASH split; check ffmpeg exists (bundled `_internal\ffmpeg.exe`) |
| Search garbled / back to homepage | Site revamped; update the plugin |
| First source fails | App auto-switches to the next source and keeps the working one |
| Vault password lost | Security questions reset it; 4 wrong attempts destroy the stored cookies |

Full FAQ: `1_Documentation/FAQ.md`.

---

## 8. Disclaimer / 免责声明

**This software is for learning and personal use only. Commercial use is prohibited. / 本软件仅供学习交流使用，请勿用于商业用途。**

1. **Purpose / 用途限制**: for learning browser automation, crawling, and audio/video processing only; download only content you are **entitled** to (your own works, authorized resources, public free content). / 本工具仅用于学习浏览器自动化、网络爬虫、音视频处理等技术原理，以及下载您**有权获取**的内容（如自己发布的作品、已获授权的资源、公开免费资源）。
2. **Copyright / 版权**: all media belong to their respective owners. / 所有影视、动漫、短视频等内容版权归其权利人所有。
3. **Liability / 责任**: the author is not liable for any misuse, loss, or legal consequence caused by the user. / 因用户使用本工具产生的任何直接或间接损失及法律责任，均由用户自行承担。
4. **No warranty / 无担保**: provided "as is" without any warranty. / 本工具按现状提供，不提供任何明示或默示的担保。
5. **Compliance / 合规**: users must comply with local laws and platform terms of service. / 用户须遵守所在地法律法规及相关平台的服务条款。
