# -*- coding: utf-8 -*-
"""通用网页视频地址提取器：处理 yt-dlp 无法解析的 JS 渲染/反爬网站（如快手 short-video）。

流程：内置浏览器打开页面 → 等待 video 元素出现 → 提取 currentSrc 真实直链。
成功返回 (True, 真实视频URL)；失败返回 (False, 原因)。
"""
import os
import time
import tempfile
import socket as _sock


def _find_browser_path():
    """自动探测 Edge/Chrome 可执行文件路径"""
    cands = []
    try:
        from config import BROWSER_PATHS
        for p in BROWSER_PATHS.values():
            cands.append(p)
    except Exception:
        pass
    cands += [
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
    ]
    for p in cands:
        if p and os.path.isfile(p):
            return p
    return None


_VIDEO_JS = """
(() => {
  try {
    const v = document.querySelector('video');
    if (v) return (v.currentSrc || v.src || '');
    // 退而求其次：页面上所有带 mp4/m3u8 的链接
    const links = Array.from(document.querySelectorAll('a,source'));
    for (const a of links) {
      const h = (a.href || a.src || '');
      if (/\.(mp4|m3u8|flv)(\?|$)/i.test(h)) return h;
    }
    return '';
  } catch (e) { return ''; }
})()
"""


def extract_video_url(url, log=print, max_wait=12):
    """打开网页，等待 video 元素，提取真实视频地址。

    Returns:
        (True, 真实视频URL) 成功
        (False, 失败原因)   失败
    """
    try:
        from DrissionPage import ChromiumOptions, ChromiumPage
    except Exception as e:
        return False, f"加载浏览器引擎失败: {e}"

    browser_path = _find_browser_path()
    if not browser_path:
        return False, "未自动找到 Edge/Chrome 浏览器"

    page = None
    try:
        co = ChromiumOptions()
        co.set_browser_path(browser_path)
        debug_port = None
        for port in range(9423, 9523):
            try:
                with _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM) as s:
                    s.bind(('127.0.0.1', port))
                    debug_port = port
                    break
            except OSError:
                continue
        co.set_local_port(debug_port or 9466)
        co.set_user_data_path(tempfile.mkdtemp(prefix='extract_'))
        co.set_argument("--disable-backgrounding-occluded-windows")
        co.set_argument("--disable-renderer-backgrounding")
        co.set_argument("--disable-background-timer-throttling")
        # 移动端 UA：快手等站点对手机页面更易直接渲染视频
        co.set_argument("--user-agent=Mozilla/5.0 (Linux; Android 13; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36")
        log("正在启动内置浏览器提取真实视频地址（首次约 3~8 秒）...")
        page = ChromiumPage(co)
        page.get(url)
        log(f"页面已打开: {url[:70]}")
        page.listen.start()
        import re as _re
        for i in range(max_wait):
            # 通道A：网络请求监听（页面未渲染 video 也能捕获 mp4/m3u8 请求）
            try:
                pkts = page.listen.steps(timeout=0.4)
                for pk in pkts:
                    try:
                        u = pk.url or ''
                    except Exception:
                        u = ''
                    if u and _re.search(r'\.(mp4|m3u8|flv)(\?|$)', u, _re.I) and 'kuaishou.com' not in u:
                        log(f"已捕获真实视频地址(网络): {u[:90]}...")
                        return True, u
            except Exception:
                pass
            # 通道B：video 元素
            try:
                src = page.run_js(_VIDEO_JS)
            except Exception:
                src = ''
            # 校验是真视频链接（mp4/m3u8/flv 或含视频域名），防提取到空壳
            if src and _re.search(r'\.(mp4|m3u8|flv)(\?|$)|oskwai|kuaishou|/video/', src, _re.I):
                log(f"已捕获真实视频地址: {src[:90]}...")
                return True, src
            if i % 2 == 1:
                log(f"正在等待视频出现…（第 {i+1}/{max_wait} 秒，提取到后自动关闭浏览器）")
            time.sleep(0.5)
        return False, "等待视频出现超时（可能需登录或手动播放）"
    except Exception as e:
        return False, f"浏览器提取异常: {str(e)[:100]}"
    finally:
        try:
            if page is not None:
                page.quit()
        except Exception:
            pass
