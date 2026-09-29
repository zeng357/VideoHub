# VideoToolkit User Guide / 使用说明（English）
## UI Overview
Dark-themed main window, three areas:
| Area | Function |
|---|---|
| Left navigation | Site selection (A** Anime / B** / Y** Anime / D** Short Video / Auto-Scan / plugin sites) |
| Right main area | Address/keyword bar, search, episode table (check episodes), speed tier, download button |
| Bottom status area | Dual progress bars (overall + per-episode), speed display, log output, completion popup |
| 🔒 Side panel | Password management (set / change / clear container password) |
## 5-Step Quick Start
### Step 1: Choose a Site
Click a site in the left navigation (e.g. "A** Anime"). Built-in sites need no address; in Auto-Scan mode you can enter a site homepage URL.
### Step 2: Search
- Type a **title abbreviation or original name** into the keyword bar;
- Click **Search**. The program opens search results and locates the target series homepage (this ensures the episode table is correct).
### Step 3: Load Episodes
Click **Load Episodes / Scan**:
- The program first detects the **total episode count**;
- Then reads the full episode list by count (with auto-scroll, avoiding "only episode 1");
- The list appears and you can check episodes.
### Step 4: Check Episodes & Choose Speed
- Single: click an episode row;
- All: use "Select All";
- **Speed tier is chosen separately**: Turbo (32 threads, fastest) / High / Standard (6) / Steady (most stable, for weak servers). **Choose speed first, then click Download.**
### Step 5: Download
Click **Start Download**:
- Bottom progress bars show overall progress and network speed;
- A popup appears when finished;
- Videos are saved under `Download/<site>/<title>/<ep>/`.
## Security: Login Container & Password (v2.0)
- The app saves browser cookies into an **encrypted container** (machine-fingerprint bound). No plaintext cookies on disk.
- **No password needed for normal use**: the app's own browser loads logins seamlessly on your machine.
- **Verification required for anything else**: copied program, another machine, or manual unlock → enter the container password. **4 wrong tries destroy all saved login data**.
- **Program integrity check**: if the program files are modified, the app asks for re-verification before loading saved logins. If it is your own update, confirm and continue; otherwise deny.
- **Set a password** (optional but recommended): open the 🔒 side panel → "Set password" → input ≥6 chars with digits + symbols + letters + set **security questions** (favorite, name of a loved one, etc.). Changing / clearing the password requires answering the questions.
- **Copying the program to others**: on their machine open the 🔒 panel → "Reset container" → set their own password → re-login needed sites. Your saved data is never exposed.
## Site-Specific Notes
| Site | Notes |
|---|---|
| A** Anime | Search + multi-source auto-switch: if the first source fails, the next is tried; duplicate-free |
| B** Bullet-Screen | DASH audio/video split; ffmpeg auto-mux. For login-gated anime, log in in your browser and paste the Cookie into the "B** Cookie" field |
| Y** Anime | Search `/video-search/...html?wd=`, play `/video-play/...html`; update plugin if the site changes |
| D** Short Video | Random grab on featured page: click "🎲 Random Grab" (turns into "Stop"), human-controlled by time/count; watermarked-free mp4 direct links |
| Auto-Scan | Enter a homepage URL; list all videos; check single / multiple / all |
| Plugin sites | Put a plugin in `sites_data/`, restart; auto-registered |
## Common Problems (summary)
- **Black window closes instantly**: Python or dependencies missing; run `python gui.py` from a terminal to see the error.
- **Only episode 1 downloads**: use the current version (lazy-load fixed); confirm the table shows all episodes before checking.
- **Download hangs on click**: choose a speed tier first; disable proxy; the 90s watchdog auto-interrupts.
- **Rate-limited / blocked**: switch to Standard/Steady; pause for a few hours.
- **B** video has no audio**: confirm ffmpeg is installed and `config.py` path is correct.
- **"Need password" when loading episodes**: a password-protected login container was detected; enter the container password (4 wrong tries destroy the data).
- **"Program modified" prompt**: the program files changed; confirm it is your own update, or deny.
Full 15-item FAQ: `FAQ.md`.
