import os
import re
import sys
import time


def ensure_console_safe():
    """入口加固：避免 GBK 控制台打印特殊字符崩溃、无控制台环境下 print 崩溃。

    与漫画下载器一致：应在程序入口（gui.py/main.py）最开头调用。
    """
    class _NullWriter:
        def write(self, s):
            pass
        def flush(self):
            pass

    if sys.stdout is None:
        sys.stdout = _NullWriter()
    if sys.stderr is None:
        sys.stderr = sys.stdout
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, 'reconfigure', None)
        if reconfigure:
            try:
                reconfigure(errors='replace')
            except Exception:
                pass


def is_normal_url(url):
    """URL 是否有效"""
    return bool(url) and ('http://' in str(url) or 'https://' in str(url))


def sanitize_filename(name):
    """清洗文件名/文件夹名：替换 Windows 非法字符，压缩空白"""
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', ' ', str(name))
    name = re.sub(r'\s+', ' ', name).strip(' .')
    return name or 'video'


def ensure_dir(path):
    """确保目录存在"""
    if not os.path.exists(path):
        os.makedirs(path, exist_ok=True)
    return path


def human_size(num):
    """字节数转可读字符串"""
    try:
        num = float(num)
    except (TypeError, ValueError):
        return '未知'
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if num < 1024 or unit == 'TB':
            return f"{num:.1f} {unit}" if unit != 'B' else f"{int(num)} B"
        num /= 1024
    return f"{num:.1f} TB"


def now_str():
    return time.strftime('%H:%M:%S')
