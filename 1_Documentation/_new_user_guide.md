# VideoToolkit User Guide / 使用说明（English）

Full manual for the four modules + the Login Vault.

> **Module split by design**: Home page = anime / drama / long-form series · **General Download** = long videos · **Resource Sniffer** = short videos / web media · **Video Converter** = format conversion.

---

## 1. Home — Multi-Site Batch Downloader

Dark-themed main window, three areas:

| Area | Function |
| --- | --- |
| Left navigation | Site selection (A** Anime / B** / Y** Anime / D** Short Video / Auto-Scan / plugin sites) |
| Right main area | Address/keyword bar, search, episode table (check episodes), speed tier, download button |
| Bottom status area | Dual progress bars (overall + per-episode), speed display, log output, completion popup |

### Step 1: Choose a Site
Click a site in the left navigation (e.g. "A** Anime"). Built-in sites need no address; in Auto-Scan mode you can enter a site homepage URL.

### Step 2: Search
- Type a **title abbreviation or original name** into the keyword bar;
- Click **Search**. The program opens search results and locates the target series page (this ensures the episode table is correct).
- If the site offers multiple series entries (e.g. season 1 / 2), a chooser appears — pick the one you want, then it returns to the main page.

### Step 3: Load Episodes
Click **Load Episodes / Scan**:
- The program first detects the **total episode count**;
- Then reads the full episode list by count (auto-scroll, avoiding "only episode 1");
- The list appears and you can check episodes.

### Step 4: Check Episodes & Choose Speed
- Single: click an episode row;
- Range / all: use the check controls;
- **Speed tier is chosen separately**: Turbo (32 threads, fastest) / Fast (16) / Standard (6) / Steady (3, most stable for weak servers). **Choose speed first, then click Download.**
- If the episode count is large (> 50), you get three options: **Stop / Select / All**.

### Step 5: Download
Click **Start Download**:
- Bottom progress bars show overall progress and network speed;
- A popup appears when finished;
- Videos are saved under `Download/<site>/<title>/<ep>/` (or a folder named after the title).

---

## 2. General Download — Long Videos

Use this page for **long videos** (anime, B** bullet-screen videos, drama, etc.).

1. Paste the page URL into the input box;
2. Click **Parse / Get Info** — the yt-dlp engine extracts the real stream info (formats, quality);
3. Choose a quality / format if offered;
4. Click **Download** — DASH audio+video are auto-muxed into a single mp4 via ffmpeg;
5. If parsing fails, or the result is a generic source, or the download fails, the program automatically opens the page in a background browser and **extracts the direct video address** as a fallback, then downloads it.

> Tip: short videos and general web media are better handled by the **Resource Sniffer** page.

---

## 3. Resource Sniffer — Short Videos / Web Media

Use this page for **short videos and general web media** (pages where media starts playing when opened).

1. Two modes:
   - **Paste URL**: enter the page URL, click Start — the tool opens it in a background browser;
   - **Open in browser**: jump straight into the browser and search / browse yourself;
2. When the video plays (or the page loads media), the tool captures real media addresses through **two channels** — a network listener + the video tag polling — and lists them in real time;
3. **Check** the items you want (checkbox is the first column);
4. Choose the **save folder** (shown in the path box);
5. Click **Download** — direct multi-thread download of the selected media.

---

## 4. Video Converter

Lossless / compatible format conversion between common video formats.

1. Pick one or more video files (or drag them in);
2. Choose the target format (mp4, mkv, webm, mov, ts, flv, avi …);
3. Choose quality mode (lossless copy / re-encode if needed);
4. Click **Convert** — ffmpeg runs the conversion; progress and output path are shown.

---

## 5. Login Vault (Encrypted Cookie Storage)

Store site cookies (e.g. B** login cookies) safely.

- **Encryption**: cookies are encrypted and bound to this PC; only this machine can open the vault without a password.
- **External PC**: if the program folder is copied to another computer, opening the vault requires the password.
- **Password rule**: must contain **letters + digits + symbols** and be at least **6 characters** long.
- **Wrong attempts**: 4 consecutive wrong passwords **destroy the stored data**.
- **Change password**: answered your **security questions** (e.g. favorite hobby, loved one's name — you set them yourself) to reset/change it.

> The Home page never shows the vault; it lives in the Settings / folded menu as its own page.

---

## 6. Site-Specific Notes

| Site | Notes |
| --- | --- |
| A** Anime | Search + multi-source auto-switch: if the first source fails, the next is tried; duplicate-free |
| B** Bullet-Screen | DASH audio/video split; ffmpeg auto-mux. For login-gated anime, log in in your browser and store the Cookie in the Login Vault |
| Y** Anime | Search `/video-search/...html?wd=`, play `/video-play/...html`; update plugin if the site changes |
| D** Short Video | Random grab on featured page: "Random Grab" (turns into "Stop"), human-controlled by time/count |
| Auto-Scan | Enter a homepage URL; list all videos; check single / multiple / all |
| Plugin sites | Put a plugin in `sites_data/`, restart; auto-registered |

---

## 7. Common Problems

| Problem | Cause / Solution |
| --- | --- |
| Black window closes instantly | Python or dependencies missing; run `python gui.py` from a terminal to see the error |
| Only episode 1 downloads | use the current version (lazy-load fixed); confirm the table shows all episodes before checking |
| Download hangs on click | choose a speed tier first; disable proxy; the 90s watchdog auto-interrupts |
| Rate-limited / blocked | switch to Standard/Steady; pause for a few hours; random delays + UA rotation + simulated scroll are built in |
| B** video has no audio | confirm ffmpeg exists (bundled `_internal\ffmpeg.exe`) |
| Sniffer finds nothing | make sure the page actually starts playing; try "Open in browser" mode and play the video |
| Vault password lost | use your security questions to reset; 4 wrong attempts destroy the stored cookies |

Full 15-item FAQ: `FAQ.md`.
