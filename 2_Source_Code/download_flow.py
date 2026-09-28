# 公共下载流程 - gui.py 与 main.py 共用
# 三种模式：
#   1. 自动扫描：地址 + (可选关键词) → 浏览器自动扫描页面/列表页 → 自动下载保存
#   2. 直接链接下载：粘贴 mp4/m3u8 地址直接下载（无需浏览器）
#   3. 站点下载：站点插件搜索剧集 → 选集 → 逐集下载
import os
import json
import time
from urllib.parse import urlparse

from utils import sanitize_filename, ensure_dir, is_normal_url
from video_downloader import download_media, download_dash_pair, is_hls_url

DEFAULT_EPISODE_PADDING = 3  # 集号补零位数：001、002...
VIDEO_EXTS = ('.mp4', '.webm', '.mkv', '.mov', '.flv', '.avi', '.m4v', '.ts')


def episode_save_name(ep, padding=DEFAULT_EPISODE_PADDING):
    """生成单集文件名（不含扩展名），标题过长自动截断"""
    num = int(ep.get('episode_num', 1))
    title = ep.get('title') or ''
    name = f"{num:0{padding}d}"
    if title:
        t = sanitize_filename(title)
        if len(t) > 40:
            t = t[:40].rstrip(' -—')  # 去掉截断处可能残留的分隔符
        name = f"{name} {t}"
    return name


def _direct_ext(url):
    """从直链URL推断扩展名，默认 .mp4"""
    path = urlparse(url).path
    ext = os.path.splitext(path)[1].lower()
    if ext in VIDEO_EXTS:
        return ext
    return '.mp4'


def download_episode_video(ep, series_dir, use_ffmpeg=True, progress_callback=None,
                           max_workers=6, timeout=20, log=print):
    """下载单集视频

    Args:
        ep: {'episode_num', 'title', 'video_url', 'video_type', 'referer'}
    Returns:
        (success, final_path, info)
    """
    url = ep.get('video_url')
    if not url:
        return False, None, "无视频地址"
    vtype = ep.get('video_type') or ('hls' if is_hls_url(url) else 'direct')
    referer = ep.get('referer')
    name = episode_save_name(ep)

    # DASH 双流（B站等）：视频流+音频流分别下载，ffmpeg 合成单个文件
    audio_url = ep.get('audio_url')
    if audio_url and vtype == 'dash':
        save_path = os.path.join(series_dir, name + '.mp4')
        log(f"  第{ep.get('episode_num', 1)}集 DASH音视频流下载(自动合成): {url[:80]}")
        ok, final_path, info = download_dash_pair(
            url, audio_url, save_path, referer=referer,
            progress=progress_callback, max_workers=max_workers, timeout=timeout, log=log)
        return ok, final_path, info

    if vtype == 'hls' or is_hls_url(url):
        save_path = os.path.join(series_dir, name)  # 无扩展名，由HLS引擎决定 .ts/.mp4
        log(f"  第{ep.get('episode_num', 1)}集 HLS下载: {url[:100]}")
        ok, final_path, info = download_media(
            {'url': url, 'type': 'hls'}, save_path, referer=referer,
            progress=progress_callback, max_workers=max_workers, timeout=timeout,
            use_ffmpeg=use_ffmpeg, log=log)
    else:
        save_path = os.path.join(series_dir, name + _direct_ext(url))
        log(f"  第{ep.get('episode_num', 1)}集 直链下载: {url[:100]}")
        ok, final_path, info = download_media(
            {'url': url, 'type': 'direct'}, save_path, referer=referer,
            progress=progress_callback, max_workers=max_workers, timeout=timeout, log=log)

    return ok, final_path, info


def _derive_series_name(url):
    """从URL推导默认下载目录名"""
    host = urlparse(url).netloc
    host = host.replace('www.', '') or 'video'
    return sanitize_filename(host)


# ==================== 模式1：直接链接 ====================
def run_direct_link(url, download_path=None, log=print, progress_callback=None,
                    max_workers=6, use_ffmpeg=True, timeout=20):
    """直接下载一个视频链接（mp4直链 或 m3u8）"""
    url = url.strip()
    if not url:
        raise RuntimeError("链接不能为空")
    if not (url.startswith('http://') or url.startswith('https://')):
        raise RuntimeError("请输入 http/https 开头的有效链接")

    base = ensure_dir(download_path) if download_path else ensure_dir(os.getcwd())
    series_dir = ensure_dir(os.path.join(base, _derive_series_name(url)))
    vtype = 'hls' if is_hls_url(url) else 'direct'
    ep = {'episode_num': 1, 'title': 'video', 'video_url': url,
          'video_type': vtype, 'referer': None}
    log(f"下载类型: {'HLS(m3u8)' if vtype == 'hls' else '直链'}")
    ok, final_path, info = download_episode_video(
        ep, series_dir, use_ffmpeg=use_ffmpeg, progress_callback=progress_callback,
        max_workers=max_workers, timeout=timeout, log=log)
    return {'success': ok, 'final_path': final_path, 'info': info,
            'failed': [] if ok else [ep]}


# ==================== 模式1：自动扫描 ====================
def run_auto_scan(crawler, url, keyword=None, download_path=None, log=print,
                  url_progress_callback=None, progress_callback=None,
                  max_workers=6, use_ffmpeg=True, timeout=20, collect_only=False):
    """输入地址（+可选关键词），自动扫描页面/列表页，找到视频即自动下载保存。

    collect_only=True 时只扫描收集剧集表（不下载），返回 {'eps': [...], 'total': n}
    """
    url = url.strip()
    if not is_normal_url(url):
        raise RuntimeError("请输入 http/https 开头的有效地址")
    if keyword:
        keyword = keyword.strip()

    log(f"正在打开地址: {url}")
    tab = crawler.search_series(url)
    if not tab:
        raise RuntimeError("页面打开失败")
    if keyword:
        log(f"使用关键词过滤: {keyword}")
    log("正在自动扫描（列表页将自动逐条深入，最多80页/4层）...")
    eps = crawler.collect_episode_videos(tab, keyword=keyword,
                                         progress_callback=url_progress_callback)
    eps = [e for e in eps if e.get('video_url')]
    if not eps:
        raise RuntimeError("未扫描到可下载的视频。可能原因：需登录（请填Cookie重试）、"
                           "播放器为blob流、或页面结构特殊。")
    if collect_only:
        log(f"扫描到 {len(eps)} 个视频（仅加载剧集表，未下载）")
        return {'eps': eps, 'total': len(eps)}
    log(f"扫描到 {len(eps)} 个视频，开始自动下载保存...")
    series_name = sanitize_filename(keyword) if keyword else _derive_series_name(url)
    return _download_episodes(eps, download_path, series_name, log, progress_callback,
                              max_workers, use_ffmpeg, timeout)


# ==================== 模式3：站点搜索下载 ====================
def run_site_download(crawler, name, episode_start=1, episode_end=0,
                      download_path=None, log=print, url_progress_callback=None,
                      progress_callback=None, pre_collect_hook=None, episode_callback=None,
                      max_workers=6, use_ffmpeg=True, timeout=20, collect_only=False):
    """搜索剧集 → 收集各集视频地址 → 逐集下载"""
    name = name.strip()
    if not name:
        raise RuntimeError("剧集名称不能为空")

    log(f"正在搜索: {name}")
    tab = crawler.search_series(name)
    if not tab:
        raise RuntimeError("未找到剧集，请检查名称/ID是否正确")
    log("成功打开剧集详情页")

    log("正在获取剧集数量...")
    total = crawler.get_episode_count(tab)

    # 章节计数稳定性校验（懒加载/折叠列表时重读）
    if episode_end <= 0 and getattr(crawler, 'needs_browser', True):
        last = total
        for _ in range(3):
            time.sleep(1.5)
            re_count = crawler.get_episode_count(tab)
            if re_count == last:
                total = re_count
                break
            if re_count > last:
                last = total = re_count
                log(f"剧集数变化: {re_count}（等待页面加载完成）")
        log(f"剧集数稳定: {total}")

    if total <= 0:
        raise RuntimeError("未获取到剧集列表")

    actual_start = max(episode_start, 1)
    actual_end = min(episode_end, total) if episode_end > 0 else total
    actual_count = actual_end - actual_start + 1
    log(f"总集数: {total}, 将下载: 第{actual_start}-{actual_end}集 共{actual_count}集")
    if pre_collect_hook:
        pre_collect_hook(actual_count)

    log("正在收集各集视频地址...")
    eps = crawler.collect_episode_videos(
        tab, episode_start=actual_start, episode_end=actual_end,
        progress_callback=url_progress_callback)
    eps = [e for e in eps if e.get('video_url')]
    if not eps:
        raise RuntimeError("未收集到任何视频地址")
    log(f"收集到 {len(eps)} 集视频地址")
    if collect_only:
        log(f"剧集表已加载（仅收集，未下载）")
        return {'eps': eps, 'total': len(eps)}

    return _download_episodes(eps, download_path, sanitize_filename(name),
                              log, progress_callback, max_workers, use_ffmpeg, timeout,
                              episode_callback=episode_callback)


# ==================== 公共：批量下载 ====================
def _download_episodes(eps, download_path, series_name, log, progress_callback,
                       max_workers, use_ffmpeg, timeout, episode_callback=None):
    base = ensure_dir(download_path) if download_path else ensure_dir(os.getcwd())
    series_dir = ensure_dir(os.path.join(base, series_name))

    failed = []
    done = 0
    total = len(eps)
    manifest = []
    sorted_eps = sorted(eps, key=lambda e: int(e.get('episode_num', 0) or 0))
    for _idx, ep in enumerate(sorted_eps):
        if _idx > 0:
            time.sleep(2)  # 集间短暂休息，降低 CDN 连续高频请求被风控挂起的概率
        log(f"[{done + 1}/{total}] 正在下载第{ep.get('episode_num')}集...")
        try:
            ok, final_path, info = download_episode_video(
                ep, series_dir, use_ffmpeg=use_ffmpeg,
                progress_callback=progress_callback,
                max_workers=max_workers, timeout=timeout, log=log)
        except Exception as e:
            ok, final_path, info = False, None, str(e)
        done += 1
        if episode_callback:
            episode_callback(done, total)
        manifest.append({
            '集数': ep.get('episode_num'),
            '标题': ep.get('title', ''),
            '地址': ep.get('video_url', ''),
            '状态': '成功' if ok else '失败',
            '文件': os.path.basename(final_path) if ok and final_path else '',
            '信息': '' if ok else str(info),
        })
        if ok:
            log(f"  完成: {os.path.basename(final_path)} ({info})")
        else:
            failed.append({**ep, 'info': info})
            log(f"  失败: {info}")

    # 自动保存扫描结果清单
    try:
        manifest_path = os.path.join(series_dir, '扫描结果.json')
        with open(manifest_path, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        log(f"扫描结果清单已保存: {manifest_path}")
    except Exception as e:
        log(f"保存扫描结果清单失败: {e}")

    return {'total': total, 'done': done, 'failed': failed,
            'series_dir': series_dir}
