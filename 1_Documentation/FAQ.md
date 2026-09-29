# VideoToolkit FAQ / 常见问题（English）
## Startup & Environment
**Q1. Double-clicking start.bat opens a black window that closes instantly.**
A: Python or a dependency is missing. Open a terminal in the folder and run `python gui.py` to see the real error, then `pip install -r requirements.txt`. Also check `python --version` (need 3.10+).
**Q2. ModuleNotFoundError on launch.**
A: Run `pip install -r requirements.txt`. If a module still fails, install it manually: `pip install <module>`.
**Q3. ffmpeg not found / no sound in B** videos.**
A: B** uses DASH (separate audio & video). Install ffmpeg from ffmpeg.org, then confirm the path in `config.py` (default: `C:\Program Files\ffmpe\bin\ffmpeg.exe`).
## Search & Episodes
**Q4. Search results are correct, but the episode table only has episode 1.**
A: Older bug (lazy loading), fixed in the current version. Click **Load Episodes** again and wait for "scan complete"; make sure all episodes appear before downloading.
**Q5. It scans more episodes than the total (e.g. 27 for a 23-episode series).**
A: The app now detects the **total episode count first** and only collects that many. If you still see extras, the site page lists wrong episode links — update the site plugin.
**Q6. Search garbled / jumps back to homepage / results are wrong.**
A: The site changed its search route. Check the search URL in the plugin (e.g. `/video-search/...html?wd=`), update it, and restart.
**Q7. After searching, it returns to the homepage and searches random things.**
A: The site's search requires landing on the detail page first. The app now **locates the series homepage after search** before scanning. If the site changed, update the plugin.
**Q8. Search returns multiple seasons / works.**
A: A chooser window pops up — pick the season you want, then the app crawls that one only.
## Downloading
**Q9. Clicking Download does nothing / hangs.**
A: 1) Choose a speed tier first (Turbo/High/Standard/Steady); 2) disable proxy/VPN; 3) the built-in 90s watchdog auto-interrupts a dead download and logs it.
**Q10. Only some episodes download; later ones fail.**
A: Weak server or rate limiting. Switch to Standard/Steady; the app lowers concurrency automatically and retries. Wait a few hours if the site rate-limits.
**Q11. Does it support downloading everything at once?**
A: Yes — check "Select All" and the app downloads checked episodes in order. Choose Standard/Steady for long lists on weak servers.
**Q12. Multiple sources — can it switch?**
A: Yes. If the first source fails, the app tries the next and keeps the working one (deduplicated).
## Anti-Crawling / Site Issues
**Q13. The site got rate-limited / blocked my requests.**
A: The app has random delays (3.5–6s), 4 rotating UAs, browser-like headers, simulated scrolling, and serial fallback. If blocked anyway, stop for a few hours and use Steady mode. Do not stress target servers.
**Q14. The site asks for login (B** member-only anime).**
A: Log in in your browser, then paste the Cookie into the "B** Cookie" field in the app.
**Q15. Can I add my own site?**
A: Yes — copy `3_Site_Extensions/sites_data/template_crawler.example.py`, fill in search/episode/play rules, save as `<site>_crawler.py` in `sites_data/`, and restart. See `How_It_Works.md`.
## Security: Login Container (v2.0)
**Q16. Why does it ask for a password when I load episodes?**
A: A login container with a password was detected. Enter the container password to unlock. **4 wrong tries automatically destroy all saved login data** — be careful.
**Q17. I never set a password, why ask me now?**
A: If the program files were modified (hash changed), the app requires re-verification. If it is your own update, confirm and continue; otherwise deny access.
**Q18. I copied the program to a friend — what should they do?**
A: On their machine open the 🔒 panel → "Reset container" → set their own password → re-login needed sites. Your saved data stays protected.
**Q19. How do I change / clear the container password?**
A: 🔒 panel → "Change password" / "Clear password". You must first answer the **security questions** set when the password was created (they can be customized, no length limit).
**Q20. What happens if the password is wrong too many times?**
A: After **4 wrong tries** the container is destroyed — all saved login data is erased and cannot be recovered. Re-login needed sites afterwards.
## Output
**Q21. Where are the downloaded videos?**
A: `Download/<site>/<title>/<ep>/`, with a progress bar and a popup when finished.
