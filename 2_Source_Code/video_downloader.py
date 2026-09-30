"""视频下载引擎：直链下载 + m3u8(HLS) 分段下载 + AES-128 解密 + ffmpeg 封装 mp4。

设计目标（与漫画下载器一脉相承）：
- 无额外重量依赖：分段下载用 requests 多线程；AES 解密用 pycryptodome；
  仅当系统存在 ffmpeg 时才把 .ts 封装为 .mp4（可选，没有则保留 .ts）。
- 支持 Referer（防盗链）与系统代理（CDN 拒绝代理出口时自动直连重试）。
- 进度通过回调上报，兼容 GUI 进度条与 CLI。
"""
import os
import re
import base64
import tempfile
import shutil
import subprocess
import threading
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from utils import is_normal_url, sanitize_filename, ensure_dir
try:
    from config import detect_ffmpeg_path
except Exception:
    detect_ffmpeg_path = None

# ---------- 全局代理（惰性检测，线程安全） ----------
_SYSTEM_PROXY = None
_SYSTEM_PROXY_CHECKED = False
_SYSTEM_PROXY_LOCK = threading.Lock()

# ---------- 共享 Session（保持 Cookie；防盗链 CDN 常要求清单→分片带同一鉴权） ----------
_SESSION = None
_SESSION_LOCK = threading.Lock()


def _get_session():
    """进程级共享 requests.Session：自动持久化 Cookie + 连接复用 + 自动重试"""
    global _SESSION
    if _SESSION is None:
        with _SESSION_LOCK:
            if _SESSION is None:
                s = requests.Session()
                try:
                    from requests.adapters import HTTPAdapter, Retry
                    retry = Retry(total=2, backoff_factor=0.5,
                                  status_forcelist=(403, 429, 500, 502, 503, 504))
                    s.mount('https://', HTTPAdapter(pool_connections=8, pool_maxsize=16,
                                                    max_retries=retry))
                    s.mount('http://', HTTPAdapter(pool_connections=8, pool_maxsize=16,
                                                   max_retries=retry))
                except Exception:
                    pass
                _SESSION = s
    return _SESSION


def _valid_proxy_server(server):
    """校验代理服务器字符串是否形如 host:port 或 http(s)://host:port，垃圾配置直接忽略"""
    if not server or not isinstance(server, str):
        return False
    s = server.strip()
    if not s:
        return False
    if '=' in s:
        return all(_valid_proxy_server(p.split('=', 1)[1]) for p in s.split(';') if p)
    if re.match(r'^[\w.\-]+:\d+$', s):
        return True
    return bool(re.match(r'^https?://[\w.\-]+:\d+', s))


def _read_system_proxy():
    """读取 Windows 系统代理设置（格式非法时返回 None）"""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            0, winreg.KEY_READ)
        proxy_enable = winreg.QueryValueEx(key, "ProxyEnable")[0]
        if not proxy_enable:
            winreg.CloseKey(key)
            return None
        proxy_server = winreg.QueryValueEx(key, "ProxyServer")[0]
        winreg.CloseKey(key)
        if not _valid_proxy_server(proxy_server):
            return None
        if '=' not in proxy_server:
            return {'http': f'http://{proxy_server}', 'https': f'http://{proxy_server}'}
        proxies = {}
        for item in proxy_server.split(';'):
            if '=' in item:
                proto, addr = item.split('=', 1)
                proxies[proto] = f'http://{addr}'
        return proxies or None
    except Exception:
        return None


def get_active_proxy():
    global _SYSTEM_PROXY, _SYSTEM_PROXY_CHECKED
    if not _SYSTEM_PROXY_CHECKED:
        with _SYSTEM_PROXY_LOCK:
            if not _SYSTEM_PROXY_CHECKED:
                _SYSTEM_PROXY = _read_system_proxy()
                _SYSTEM_PROXY_CHECKED = True
    return _SYSTEM_PROXY


def _mk_headers(referer=None, extra=None):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36'}
    if referer:
        headers['Referer'] = referer
    if extra:
        headers.update(extra)
    return headers


def _resolve_proxy(proxy):
    """返回尝试顺序：直连优先、代理兜底（避免本机代理配置异常导致长时间卡死）"""
    active = proxy if proxy is not None else get_active_proxy()
    if active:
        return [None, active]
    return [None]


def _tmo(timeout):
    """连接超时8秒 + 读取超时N秒，防止连接阶段长时间无响应挂死"""
    if isinstance(timeout, (int, float)) and timeout:
        return (8, timeout)
    return timeout


# ==================== 直链下载 ====================
def download_direct(url, save_path, referer=None, progress=None, timeout=20, proxy=None,
                    extra_headers=None):
    """直链下载一个媒体文件到 save_path。

    Returns:
        (success: bool, info: str)  info 成功为字节数，失败为错误信息
    """
    for attempt_proxies in _resolve_proxy(proxy):
        try:
            with requests.get(url, stream=True, timeout=_tmo(timeout),
                              headers=_mk_headers(referer, extra_headers),
                              proxies=attempt_proxies) as resp:
                if resp.status_code != 200:
                    return False, f"HTTP {resp.status_code}"
                total = int(resp.headers.get('Content-Length') or 0)
                downloaded = 0
                with open(save_path, 'wb') as f:
                    for chunk in resp.iter_content(1024 * 256):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            if progress:
                                progress(downloaded, total)
                if downloaded <= 0:
                    return False, "内容为空"
                return True, downloaded
        except Exception as e:
            if attempt_proxies is None:
                return False, str(e)[:80]
    return False, "未知错误"


# ==================== 直链极速下载（多线程分段） ====================
def _probe_range(url, referer, timeout, proxy, extra_headers=None):
    """探测服务器是否支持 Range 分段，支持则返回文件总字节数，否则 None"""
    for attempt_proxies in _resolve_proxy(proxy):
        try:
            r = requests.get(url, timeout=_tmo(timeout),
                             headers=_mk_headers(referer, dict(extra_headers or {}, Range='bytes=0-0')),
                             proxies=attempt_proxies, stream=True)
            r.close()
            if r.status_code == 206 and 'Content-Range' in r.headers:
                try:
                    total = int(r.headers['Content-Range'].split('/')[-1])
                    if total > 0:
                        return total
                except Exception:
                    return None
            return None
        except Exception:
            if attempt_proxies is None:
                return None
    return None


def _download_range(url, start, end, save_path, referer, timeout, proxy, extra_headers=None):
    """下载指定字节范围 [start, end] 到文件"""
    headers = _mk_headers(referer, dict(extra_headers or {}, Range=f'bytes={start}-{end}'))
    for attempt_proxies in _resolve_proxy(proxy):
        try:
            r = requests.get(url, timeout=_tmo(timeout), headers=headers,
                             proxies=attempt_proxies, stream=True)
            if r.status_code not in (200, 206):
                raise RuntimeError(f"HTTP {r.status_code}")
            with open(save_path, 'wb') as f:
                for chunk in r.iter_content(1024 * 256):
                    if chunk:
                        f.write(chunk)
            return True
        except Exception:
            if attempt_proxies is None:
                raise
    raise RuntimeError("分段下载失败")


def download_direct_parallel(url, save_path, workers=6, referer=None, progress=None,
                             timeout=20, proxy=None, extra_headers=None):
    """多线程分段下载直链（需服务器支持 Range），不支持或文件过小时自动回退单线程。

    Returns:
        (success: bool, info: str) 与 download_direct 契约一致
    """
    if workers <= 1:
        return download_direct(url, save_path, referer, progress, timeout, proxy, extra_headers)

    total = _probe_range(url, referer, timeout, proxy, extra_headers)
    if not total or total < 1024 * 1024:  # <1MB 不值得分段
        return download_direct(url, save_path, referer, progress, timeout, proxy, extra_headers)

    import tempfile
    tmp_dir = tempfile.mkdtemp(prefix='vdl_mp_')
    try:
        # 按 workers 切分字节区间
        chunk = max(total // workers, 1)
        ranges = []
        start = 0
        while start < total:
            end = min(start + chunk - 1, total - 1)
            ranges.append((start, end))
            start = end + 1
        part_files = [os.path.join(tmp_dir, f'p{i:04d}') for i in range(len(ranges))]
        done_bytes = [0] * len(ranges)
        lock = threading.Lock()
        failed = [0]

        def work(i):
            try:
                _download_range(url, ranges[i][0], ranges[i][1], part_files[i],
                                referer, timeout, proxy, extra_headers)
                size = os.path.getsize(part_files[i])
                done_bytes[i] = size
                with lock:
                    if progress:
                        progress(sum(done_bytes), total)
            except Exception:
                with lock:
                    failed[0] += 1

        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(work, i) for i in range(len(ranges))]
            for fut in futs:
                fut.result()

        if failed[0]:
            return False, f"{failed[0]}/{len(ranges)} 个分段失败"

        with open(save_path, 'wb') as out:
            for pf in part_files:
                if os.path.exists(pf) and os.path.getsize(pf) > 0:
                    with open(pf, 'rb') as fh:
                        shutil.copyfileobj(fh, out)
        return True, sum(done_bytes)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ==================== m3u8 (HLS) 下载 ====================
_M3U8_STREAM_INF = re.compile(r'#EXT-X-STREAM-INF:[^\n]*RESOLUTION=(\d+)x(\d+)')
_M3U8_KEY = re.compile(r'#EXT-X-KEY:METHOD=([^,]+)(?:,URI="([^"]+)")?(?:,IV=0x([0-9a-fA-F]+))?')
_M3U8_IV = re.compile(r'#EXT-X-KEY:[^\n]*IV=0x([0-9a-fA-F]+)')


def fetch_text(url, referer=None, proxy=None, timeout=20):
    """下载文本（m3u8 清单）"""
    last = '未知错误'
    for attempt_proxies in _resolve_proxy(proxy):
        try:
            r = _get_session().get(url, timeout=_tmo(timeout), headers=_mk_headers(referer),
                                   proxies=attempt_proxies)
            if r.status_code == 200:
                return r.text
            last = f"HTTP {r.status_code}"
        except Exception as e:
            last = str(e)[:80]
            if attempt_proxies is None:
                break
    raise RuntimeError(f"获取清单失败: {last}")


def _pick_best_variant(text, base_url):
    """从 master 清单挑选最高分辨率的分片清单 URL"""
    best = None
    best_h = 0
    lines = text.splitlines()
    pending_inf = False
    for line in lines:
        line = line.strip()
        m = _M3U8_STREAM_INF.search(line)
        if m:
            h = int(m.group(2))
            if h > best_h:
                best_h = h
                pending_inf = True
            else:
                pending_inf = False
            continue
        if pending_inf and line and not line.startswith('#'):
            best = line
            pending_inf = False
    if not best:
        raise RuntimeError("master 清单中未找到分片清单")
    return urljoin(base_url, best)


def _parse_variant(text, base_url):
    """解析分片清单，返回 (segments, key_uri, iv_hex)"""
    segments = []
    key_uri = None
    iv_hex = None
    lines = text.splitlines()
    for i, line in enumerate(lines):
        line = line.strip()
        km = _M3U8_KEY.search(line)
        if km:
            method = km.group(1)
            if method == 'AES-128':
                uri = km.group(2)
                if uri:
                    key_uri = urljoin(base_url, uri)
                ivm = _M3U8_IV.search(line)
                iv_hex = ivm.group(1) if ivm else None
            elif method.upper() == 'NONE':
                # 无加密（METHOD=NONE），分片直接下载，无需解密
                pass
            else:
                # 其他加密（如 SAMPLE-AES），无法处理
                raise RuntimeError(f"不支持的加密方式: {method}")
        elif line.startswith('#EXTINF'):
            continue
        elif line and not line.startswith('#'):
            segments.append(urljoin(base_url, line))
    if not segments:
        raise RuntimeError("分片清单中未找到视频分段")
    return segments, key_uri, iv_hex


def _aes_decrypt(data, key, iv):
    """AES-128-CBC 解密并去除 PKCS7 填充"""
    try:
        from Crypto.Cipher import AES
    except Exception:
        raise RuntimeError("缺少 pycryptodome，无法解密加密的 HLS（pip install pycryptodome）")
    cipher = AES.new(key, AES.MODE_CBC, iv)
    plain = cipher.decrypt(data)
    if not plain:
        return b''
    pad = plain[-1]
    if 1 <= pad <= 16:
        return plain[:-pad]
    return plain


def _download_segment(url, save_path, key, iv, index, referer, proxy, timeout):
    """下载单个分段，必要时解密；走共享 Session（Cookie 保持）"""
    last = ''
    try:
        t_read = 12 if not timeout else min(int(timeout), 12)
    except Exception:
        t_read = 12
    for attempt_proxies in _resolve_proxy(proxy):
        try:
            r = _get_session().get(url, timeout=(5, t_read), headers=_mk_headers(referer),
                                   proxies=attempt_proxies)
            if r.status_code != 200:
                last = f"HTTP {r.status_code}"
                raise RuntimeError(last)
            data = r.content
            if key:
                if iv is None:
                    iv = index.to_bytes(16, 'big')  # 默认 IV = 分片序号(16字节大端)
                data = _aes_decrypt(data, key, iv)
            if data:
                with open(save_path, 'wb') as f:
                    f.write(data)
                return True
            last = '内容为空'
        except Exception as e:
            last = str(e)[:60] if not last else last
            if attempt_proxies is None:
                raise RuntimeError(last)
    raise RuntimeError(last or '下载失败')


def _find_ffmpeg():
    """定位 ffmpeg.exe：优先程序目录/核心目录/当前目录/脚本目录，最后查 PATH"""
    bases = []
    try:
        if getattr(sys, 'frozen', False):
            exe_dir = os.path.dirname(sys.executable)
            bases.append(exe_dir)
            bases.append(os.path.join(exe_dir, '_internal'))
    except Exception:
        pass
    bases += [os.getcwd(), os.path.dirname(os.path.abspath(__file__))]
    for base in bases:
        cand = os.path.join(base, 'ffmpeg.exe')
        if os.path.isfile(cand):
            return cand
        cand2 = os.path.join(base, 'ffmpeg')
        if os.path.isfile(cand2):
            return cand2
    try:
        import shutil
        w = shutil.which('ffmpeg')
        if w:
            return w
    except Exception:
        pass
    return None


def _has_ffmpeg():
    ff = _find_ffmpeg()
    if not ff:
        return False
    try:
        r = subprocess.run([ff, '-version'], capture_output=True, timeout=8)
        return r.returncode == 0
    except Exception:
        return False


def _remux_to_mp4(ts_path, mp4_path, log=print):
    """用 ffmpeg 把合并后的 .ts 转封装为 .mp4"""
    ff = _find_ffmpeg()
    if not ff:
        log("  未找到 ffmpeg，跳过 mp4 封装（保留 .ts）")
        return False
    log("  正在用 ffmpeg 封装为 mp4 ...")
    try:
        r = subprocess.run(
            [ff, '-y', '-i', ts_path, '-c', 'copy', '-bsf:a', 'aac_adtstoasc', mp4_path],
            capture_output=True, timeout=600)
        if r.returncode == 0 and os.path.exists(mp4_path) and os.path.getsize(mp4_path) > 0:
            return True
        log("  ffmpeg 封装失败，保留 .ts 文件")
        return False
    except Exception as e:
        log(f"  ffmpeg 封装异常: {e}，保留 .ts 文件")
        return False


def download_hls(m3u8_url, save_path, referer=None, progress=None, max_workers=6,
                 timeout=20, max_retries=3, use_ffmpeg=True, proxy=None, log=print):
    """下载 m3u8(HLS) 视频，合并后输出为 .ts，若有 ffmpeg 则封装为 .mp4。

    Returns:
        (success: bool, final_path: str, info: str)
    """
    # 1. 获取并解析清单
    text = fetch_text(m3u8_url, referer, proxy, timeout)
    if '#EXT-X-STREAM-INF' in text:
        log("  检测到 master 清单，自动选择最高清晰度分片清单 ...")
        variant = _pick_best_variant(text, m3u8_url)
        text = fetch_text(variant, referer, proxy, timeout)
        m3u8_url = variant

    base_url = m3u8_url
    segments, key_uri, iv_hex = _parse_variant(text, base_url)
    total = len(segments)
    log(f"  HLS 清单: 共 {total} 个分片, 加密={'AES-128' if key_uri else '无'}")

    # 2. 准备临时目录
    tmp_dir = ensure_dir(os.path.join(tempfile.gettempdir(),
                                      f"video_dl_{os.getpid()}_{int(__import__('time').time()*1000)}"))
    try:
        # 3. 下载密钥
        key = None
        if key_uri:
            for attempt_proxies in _resolve_proxy(proxy):
                try:
                    r = _get_session().get(key_uri, timeout=_tmo(timeout), headers=_mk_headers(referer),
                                           proxies=attempt_proxies)
                    if r.status_code == 200:
                        key = r.content
                        break
                except Exception:
                    if attempt_proxies is None:
                        raise
            if not key:
                raise RuntimeError("下载 AES 密钥失败")
        iv = bytes.fromhex(iv_hex) if iv_hex else None

        # 4. 并发下载分片
        seg_files = [os.path.join(tmp_dir, f"seg_{i:05d}.ts") for i in range(total)]
        done = 0
        lock = threading.Lock()

        def work(i):
            for attempt in range(max_retries + 1):
                try:
                    _download_segment(segments[i], seg_files[i], key, iv, i,
                                      referer, proxy, timeout)
                    return True
                except Exception:
                    if attempt >= max_retries:
                        return False

        failed = 0
        stop_early = threading.Event()
        # 连续失败阈值：一旦大量分片失败，立即取消剩余任务、切 ffmpeg 兜底
        # （避免 299 个分片全部超时重试完（约30分钟）才触发兜底——看起来像卡死）
        fail_threshold = max(4, min(12, max(total // 4, 1)))
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = {ex.submit(work, i): i for i in range(total)}
            for fut in as_completed(futs):
                ok = fut.result()
                with lock:
                    done += 1
                    if not ok:
                        failed += 1
                        if failed >= fail_threshold:
                            stop_early.set()
                    if progress:
                        progress(done, total)
                if stop_early.is_set():
                    break
            if stop_early.is_set():
                for f in futs:
                    f.cancel()

        if failed:
            # 对失败分片做第二轮单独重试（降低并发竞争、逐个重试）
            retry_ids = [i for i in range(total)
                         if not (os.path.exists(seg_files[i]) and os.path.getsize(seg_files[i]) > 0)]
            if retry_ids:
                log(f"  对 {len(retry_ids)} 个失败分片进行二次重试 ...")
                import time as _t
                for i in retry_ids[:5]:
                    log(f"    分片{i}: {segments[i][:90]}")
                for i in retry_ids:
                    ok_i = False
                    for attempt in range(3):
                        try:
                            # 重试用更长超时 + 短暂随机延迟（避免被CDN限流）
                            _t.sleep(0.3 + (i % 5) * 0.3)
                            _download_segment(segments[i], seg_files[i], key, iv, i,
                                              referer, proxy, max(timeout, 30))
                            ok_i = True
                            break
                        except Exception:
                            _t.sleep(0.6)
                    if not ok_i:
                        # 第三轮：更长等待（3/8/13秒）+ 去掉 Referer 再试（部分CDN拒绝带Referer）
                        for attempt in range(3):
                            try:
                                _t.sleep(3 + attempt * 5)
                                _download_segment(segments[i], seg_files[i], key, iv, i,
                                                  None, proxy, max(timeout, 60))
                                ok_i = True
                                break
                            except Exception:
                                _t.sleep(2)
            failed = sum(1 for i in range(total)
                         if not (os.path.exists(seg_files[i]) and os.path.getsize(seg_files[i]) > 0))
            if failed:
                log(f"  {failed}/{total} 个分片仍失败，改用 ffmpeg 整段直下兜底 ...")
                if use_ffmpeg and _has_ffmpeg():
                    ts_out = os.path.splitext(save_path)[0] + '.ts'
                    hdrs = {'User-Agent': _mk_headers(referer)['User-Agent'],
                            'Referer': referer or ''}
                    hdr_str = '\\r\\n'.join(f'{k}: {v}' for k, v in hdrs.items() if v) + '\\r\\n'
                    cmd = [_find_ffmpeg(), '-y', '-loglevel', 'error',
                           '-reconnect', '1', '-reconnect_streamed', '1',
                           '-reconnect_delay_max', '30',
                           '-headers', hdr_str, '-i', m3u8_url, '-c', 'copy', ts_out]
                    try:
                        rr = subprocess.run(cmd, capture_output=True, timeout=3600)
                    except Exception as e:
                        rr = None
                        log(f"  ffmpeg 兜底异常: {str(e)[:60]}")
                    if rr is not None and rr.returncode != 0:
                        err_txt = (rr.stderr or b'').decode('utf-8', 'replace')[:300]
                        log(f"  ffmpeg 兜底 stderr: {err_txt}")
                    if rr is not None and rr.returncode == 0 and os.path.exists(ts_out) \
                            and os.path.getsize(ts_out) > 0:
                        final_path = ts_out
                        mp4_path = os.path.splitext(save_path)[0] + '.mp4'
                        if _remux_to_mp4(ts_out, mp4_path, log):
                            final_path = mp4_path
                            try:
                                os.remove(ts_out)
                            except Exception:
                                pass
                        log(f"  ffmpeg 兜底成功: {final_path}")
                        return True, final_path, f"ffmpeg:{total} 分片"
                    log("  ffmpeg 兜底失败")
                # 合并已下载分片（>=60% 输出部分视频，避免整集全废）
                got = total - failed
                if got > 0 and got >= max(1, int(total * 0.6)):
                    log(f"  合并已下载 {got}/{total} 个分片（部分失败，视频可能不完整）...")
                    merged_ts = os.path.splitext(save_path)[0] + '.ts'
                    with open(merged_ts, 'wb') as out:
                        for f in seg_files:
                            if os.path.exists(f) and os.path.getsize(f) > 0:
                                with open(f, 'rb') as fh:
                                    shutil.copyfileobj(fh, out)
                    if os.path.getsize(merged_ts) > 0:
                        final_path = merged_ts
                        if use_ffmpeg and _has_ffmpeg():
                            mp4_path = os.path.splitext(save_path)[0] + '.mp4'
                            if _remux_to_mp4(merged_ts, mp4_path, log):
                                final_path = mp4_path
                                try:
                                    os.remove(merged_ts)
                                except Exception:
                                    pass
                        log(f"  \u26a0 \u5df2\u4fdd\u5b58\u4e0d\u5b8c\u6574\u89c6\u9891: {final_path} \uff08\u7f3a\u5931 {failed} \u4e2a\u5206\u7247\uff0c\u53ef\u91cd\u65b0\u4e0b\u8f7d\uff09")
                        return True, final_path, f"partial:{got}/{total}"
                raise RuntimeError(f"{failed}/{total} 个分片下载失败")


        # 5. 合并分片
        merged_ts = os.path.splitext(save_path)[0] + '.ts'
        with open(merged_ts, 'wb') as out:
            for f in seg_files:
                if os.path.exists(f) and os.path.getsize(f) > 0:
                    with open(f, 'rb') as fh:
                        shutil.copyfileobj(fh, out)

        # 6. 封装为 mp4（可选）
        final_path = merged_ts
        if use_ffmpeg and _has_ffmpeg():
            mp4_path = os.path.splitext(save_path)[0] + '.mp4'
            if _remux_to_mp4(merged_ts, mp4_path, log):
                final_path = mp4_path
                try:
                    os.remove(merged_ts)
                except Exception:
                    pass

        return True, final_path, f"{total} 分片"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ==================== 便捷入口 ====================
def is_hls_url(url):
    """按 URL 判断是否为 m3u8(HLS)"""
    u = url.lower()
    return '.m3u8' in u or 'mpegurl' in u


def download_media(source, save_path, referer=None, progress=None, max_workers=6,
                   timeout=20, max_retries=3, use_ffmpeg=True, proxy=None, log=print):
    """统一下载入口：自动识别直链 / HLS。

    source: {'url':..., 'type': 'direct'|'hls'|'auto'}
    Returns: (success, final_path, info)
    """
    url = source.get('url')
    stype = source.get('type', 'auto')
    if stype == 'hls' or (stype == 'auto' and is_hls_url(url)):
        return download_hls(url, save_path, referer, progress, max_workers,
                            timeout, max_retries, use_ffmpeg, proxy, log)
    if max_workers and max_workers > 1:
        ok, info = download_direct_parallel(url, save_path, workers=max_workers,
                                            referer=referer, progress=progress,
                                            timeout=timeout, proxy=proxy)
    else:
        ok, info = download_direct(url, save_path, referer, progress, timeout, proxy)
    return ok, (save_path if ok else None), info


# ==================== DASH 音视频双流合成（B站等） ====================
def download_dash_pair(video_url, audio_url, save_path, referer=None, progress=None,
                       timeout=20, proxy=None, max_workers=6, ffmpeg_path=None, log=print):
    """下载 DASH 视频流 + 音频流（B站等站音视频分离），用 ffmpeg 无重编码合成单文件。

    Returns:
        (success: bool, final_path: str, info: str)
    """
    try:
        os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)
    except Exception:
        pass
    base = os.path.splitext(save_path)[0]
    vtmp = base + '.video.m4s'
    atmp = base + '.audio.m4s'

    def _cleanup():
        for p in (vtmp, atmp):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass

    log("  下载视频流 ...")
    # B站 CDN 需要 Referer + Origin 才放行
    extra = {'Origin': 'https://www.bilibili.com'} if referer and 'bilibili.com' in referer else None
    ok_v, info_v = download_direct_parallel(video_url, vtmp, workers=max_workers,
                                            referer=referer, timeout=timeout, proxy=proxy,
                                            extra_headers=extra)
    if ok_v:
        try:
            if os.path.getsize(vtmp) < 1024:
                ok_v = False  # 过小多半是错误提示/防盗链占位
        except Exception:
            pass
    log("  下载音频流 ...")
    ok_a, info_a = download_direct_parallel(audio_url, atmp, workers=max_workers,
                                            referer=referer, timeout=timeout, proxy=proxy,
                                            extra_headers=extra)
    if not (ok_v and ok_a):
        _cleanup()
        return False, None, f"视频流:{'OK' if ok_v else (info_v or '失败')} 音频流:{'OK' if ok_a else (info_a or '失败')}"

    ffmpeg = ffmpeg_path or (shutil.which('ffmpeg') if callable(shutil.which) else None)
    if not ffmpeg and detect_ffmpeg_path:
        ffmpeg = detect_ffmpeg_path()
    if not ffmpeg:
        _cleanup()
        return False, None, "未检测到ffmpeg，无法合成音视频"

    log("  正在用 ffmpeg 合成音视频为单个文件 ...")
    cmd = [ffmpeg, '-y', '-loglevel', 'error', '-i', vtmp, '-i', atmp,
           '-c', 'copy', save_path]
    r = subprocess.run(cmd, capture_output=True, timeout=900)
    if r.returncode != 0:
        # 容器不兼容时回退：视频copy + 音频重编码aac
        cmd2 = [ffmpeg, '-y', '-loglevel', 'error', '-i', vtmp, '-i', atmp,
                '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', save_path]
        r2 = subprocess.run(cmd2, capture_output=True, timeout=900)
        if r2.returncode != 0 or not os.path.exists(save_path) or os.path.getsize(save_path) <= 0:
            _cleanup()
            err = (r2.stderr or b'').decode('gbk', 'ignore')[-200:]
            return False, None, f"ffmpeg合成失败: {err or '未知错误'}"
    _cleanup()
    return True, save_path, "视频+音频已合成为单个文件"
