# How It Works / 原理说明（English）

## Architecture

VideoHub is a Python GUI application that combines browser automation with HTTP downloading:

```
GUI (gui.py)
  └── main.py (entry)
        ├── video_crawler.py   — site crawling interface (search / episodes / play pages)
        ├── video_downloader.py — multi-thread download engine (Turbo 32 / Standard 6 threads)
        ├── download_flow.py   — pipeline: search → scan → collect → download → mux
        ├── site_discovery.py  — plugin loader (sites_data/*.py auto-registered)
        ├── config.py          — ffmpeg path, threads, timeouts
        └── utils.py           — helpers
```

## Crawling Pipeline

1. **Search**: the site crawler opens the site's search route, parses candidate results, and locates the target series page.
2. **Episode count**: the detail page is parsed to get the **total episode count** first, preventing over-crawling.
3. **Episode collection**: per-episode play-page URLs are collected (auto-scroll for lazy-loaded lists).
4. **Source resolution**: play pages may contain multiple sources/players; the crawler tries them in order and keeps the first working source (deduplicated).
5. **Download**: the download engine fetches the video stream. B** uses DASH — audio and video are separate streams (AES-128 encrypted), decrypted and then **muxed into a single mp4 by ffmpeg**.

## Anti-Anti-Crawling Measures (human-like behavior)

- Random delays of 3.5–6 s between requests;
- 4 rotating User-Agents;
- Full Sec-Fetch headers (browser-like);
- Simulated scrolling to trigger lazy loading;
- Serial fallback to low concurrency when the site rate-limits;
- A 90-second watchdog that interrupts a stalled download and reports it.

## Plugin Interface

Each site is a Python plugin in `sites_data/` implementing:

```python
search_series(keyword) -> [{"url", "title"}, ...]
get_episode_count(url) -> int
collect_episode_videos(url, count) -> [{"episode_num", "title", "video_url", "video_type", "referer"}, ...]
```

The loader scans `sites_data/*.py`, finds classes ending with `Crawler` that have a `SITE_NAME` attribute, and registers them automatically.

## Notes

- Downloading speed depends on the target site's server and your network; weak servers are naturally slower.
- Sites may change their markup or enable stronger anti-crawling at any time; plugins may need updates.