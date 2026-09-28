# 通用视频下载器 - 全局配置（不含网站特定信息）

BROWSER_PATHS = {
    'edge': r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    'chrome': r"C:\Program Files\Google\Chrome\Application\chrome.exe"
}

DEFAULT_BROWSER = 'edge'
DEFAULT_HEADLESS = False
DEFAULT_SITE = '自动扫描'

# ffmpeg 路径探测（用于 HLS 合并后封装 mp4）
def detect_ffmpeg_path():
    import os
    import shutil
    candidates = [
        r"C:\Program Files\ffmpe\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        r"C:\ffmpeg\bin\ffmpeg.exe",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    try:
        return shutil.which('ffmpeg')
    except Exception:
        return None

# 下载默认参数
DEFAULT_MAX_WORKERS = 6      # HLS 分段并发下载数 / 直链下载线程
DEFAULT_TIMEOUT = 20         # 单段/单次下载超时（秒）
DEFAULT_HLS_RETRY = 3        # HLS 分段失败重试次数
DEFAULT_USE_FFMPEG = True    # 有 ffmpeg 时把 HLS 合并结果封装为 mp4

# 媒体提取默认参数
DEFAULT_EXTRACT_WAIT = 15    # 打开页面后等待捕获媒体流的时间（秒）
DEFAULT_AUTOPLAY = True      # 尝试自动播放页面内 <video>

# Cookie 目录
DEFAULT_COOKIES_DIR = 'cookies'
CONFIG_FILE = 'config.json'
