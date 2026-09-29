# 视频爬虫框架 - 由漫画下载器 crawler.py 改造
# 相同部分：浏览器管理（DrissionPage、调试端口、独立临时配置目录、无头模式、Cookie）
# 改动部分：搜索/章节/图片  →  搜索/剧集/视频URL
import time
import os
import json
import cookie_guard as _cg

from config import DEFAULT_COOKIES_DIR
from utils import app_base
from site_discovery import get_site_crawler_class


class VideoCrawler:
    def __init__(self, site_name, browser_path, headless=False, cookie_str=None, cookies_dir=None):
        from DrissionPage import ChromiumOptions, ChromiumPage
        from urllib.parse import urlparse

        self.site_name = site_name

        # 动态加载站点爬虫类（sites_data/*_crawler.py）
        self.site_crawler_class = get_site_crawler_class(site_name)
        self.site_config = self.site_crawler_class.CONFIG

        self.cookie_str = cookie_str
        self.cookies_dir = cookies_dir if cookies_dir else DEFAULT_COOKIES_DIR
        self._container_unlock = None   # 加密容器解锁状态（need_password/wrong_password/destroyed）

        # 站点是否需要浏览器：纯 requests 实现的站点声明 NEEDS_BROWSER=False
        self.needs_browser = bool(getattr(self.site_crawler_class, 'NEEDS_BROWSER', True))

        if not self.cookie_str:
            self.cookie_str = self.load_cookie_str()

        if not self.needs_browser:
            self.page = None
            self.tab = None
            print(f"站点 {site_name} 无需浏览器（纯HTTP实现），跳过浏览器启动")
            self.site_crawler = self.site_crawler_class(self)
            if self.cookie_str:
                self.set_cookie()
            return

        print(f"正在初始化浏览器...")
        co = ChromiumOptions()
        co.set_browser_path(browser_path)

        # 查找可用端口并直接设置地址，避免端口冲突导致浏览器无法启动或不显示窗口
        import socket
        debug_port = None
        for port in range(9223, 9323):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(('127.0.0.1', port))
                    debug_port = port
                    break
            except OSError:
                continue
        if debug_port is None:
            import random
            debug_port = random.randint(9223, 9322)
        co.set_local_port(debug_port)
        print(f"使用调试端口: {debug_port}")

        # 每次启动使用独立的用户数据临时目录，避免复用残留目录导致浏览器进程连接异常
        import tempfile
        user_data_path = tempfile.mkdtemp(prefix='video_dl_')
        co.set_user_data_path(user_data_path)

        # 禁用窗口遮挡检测与后台节流（依赖 rAF 的播放器/懒加载页面需要）
        co.set_argument("--disable-backgrounding-occluded-windows")
        co.set_argument("--disable-renderer-backgrounding")
        co.set_argument("--disable-background-timer-throttling")
        co.set_argument("--disable-features=CalculateNativeWinOcclusion")
        # 快速加载：DOM 就绪即返回，不等图片/广告等慢资源（搜索结果/详情列表读取无需完整渲染）
        try:
            co.set_load_mode('eager')
        except Exception:
            pass

        if headless:
            co.headless()
            co.set_argument("--disable-gpu")
            co.set_argument("--no-sandbox")
            co.set_argument("--disable-dev-shm-usage")
            co.set_argument("--disable-features=SmartScreen")
            co.set_user_agent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0")
            co.set_argument("--disable-blink-features=AutomationControlled")
            print("已启用无头模式")
        else:
            print("已启用有头模式")

        # 直连模式：站点 CONFIG 声明 direct_connect 时禁用系统代理
        if self.site_config.get('direct_connect'):
            co.set_argument('--no-proxy-server')
            print("该站点启用直连模式（禁用系统代理）")

        try:
            self.page = ChromiumPage(co)
            self.tab = self.page
        except Exception as e:
            print(f"浏览器连接失败: {e}")
            print("尝试清理残留的调试浏览器进程并重新启动...")
            self._kill_leftover_debug_browsers()
            time.sleep(2)
            self.page = ChromiumPage(co)
            self.tab = self.page

        # 初始化站点爬虫实例
        self.site_crawler = self.site_crawler_class(self)

        if self.cookie_str:
            self.set_cookie()
        elif not self.cookie_str and self.has_saved_cookies():
            # 登录窗口保存的浏览器 Cookie（json）自动加载
            self.load_cookies()

    # ============ Cookie ============
    def get_cookie_str_path(self):
        if not os.path.exists(self.cookies_dir):
            os.makedirs(self.cookies_dir)
        return os.path.join(self.cookies_dir, f"{self.site_name}_cookie_str.txt.enc")

    def _guard_base(self):
        """cookie_guard 的 cookies 根目录基准（程序根目录）"""
        return app_base()

    def save_cookie_str(self, cookie_str):
        try:
            cookie_str = (cookie_str or '').strip()
            path = self.get_cookie_str_path()
            if not cookie_str:
                if os.path.exists(path):
                    os.remove(path)
                return True
            container = _cg.encrypt_container(
                cookie_str.encode('utf-8'),
                password=_cg.current_password(),
                base_dir=self._guard_base())
            with open(path, 'w', encoding='utf-8') as f:
                f.write(container)
            print(f"Cookie字符串已加密保存到: {path}")
            return True
        except Exception as e:
            print(f"保存Cookie字符串失败: {e}")
            return False

    def load_cookie_str(self):
        try:
            path = self.get_cookie_str_path()
            if os.path.exists(path):
                raw, st = _cg.decrypt_file(path, base_dir=self._guard_base(), internal=True)
                if st == 'ok' and raw:
                    s = raw.decode('utf-8', 'ignore').strip()
                    if s:
                        print(f"已解密加载Cookie字符串: {path}")
                        return s
                self._container_unlock = st
                print(f"Cookie容器解密未通过: {st}")
            # 兼容旧版未加密文件
            old = path.replace('.enc', '')
            if os.path.exists(old):
                with open(old, 'r', encoding='utf-8') as f:
                    return f.read().strip()
        except Exception as e:
            print(f"加载Cookie字符串失败: {e}")
        return None

    def has_cookie_str(self):
        p = self.get_cookie_str_path()
        return os.path.exists(p) or os.path.exists(p.replace('.enc', ''))

    def clear_cookie_str(self):
        try:
            path = self.get_cookie_str_path()
            if os.path.exists(path):
                os.remove(path)
        except Exception as e:
            print(f"清除Cookie字符串失败: {e}")

    def _kill_leftover_debug_browsers(self):
        """仅清理带调试端口的残留浏览器进程（上次异常退出遗留），不影响用户正常使用的浏览器"""
        import subprocess
        import re
        for name in ('msedge.exe', 'chrome.exe'):
            try:
                r = subprocess.run(
                    ['wmic', 'process', 'where', f"name='{name}'",
                     'get', 'ProcessId,CommandLine', '/format:csv'],
                    capture_output=True, text=True, encoding='gbk', errors='ignore')
                for line in r.stdout.splitlines():
                    if 'remote-debugging-port' in line:
                        m = re.search(r'(\d+)\s*$', line.strip())
                        if m:
                            subprocess.run(['taskkill', '/F', '/PID', m.group(1)],
                                           capture_output=True)
            except Exception:
                pass

    def get_cookies_path(self):
        if not os.path.exists(self.cookies_dir):
            os.makedirs(self.cookies_dir)
        return os.path.join(self.cookies_dir, f"{self.site_name}_cookies.json.enc")

    def save_cookies(self):
        if not self.needs_browser:
            return False
        try:
            cookies = self.tab.cookies()
            raw = json.dumps(cookies, ensure_ascii=False).encode('utf-8')
            container = _cg.encrypt_container(
                raw, password=_cg.current_password(), base_dir=self._guard_base())
            cookies_path = self.get_cookies_path()
            with open(cookies_path, 'w', encoding='utf-8') as f:
                f.write(container)
            print(f"Cookie已加密保存到: {cookies_path}")
            return True
        except Exception as e:
            print(f"保存Cookie失败: {e}")
            return False

    def load_cookies(self):
        if not self.needs_browser:
            return False
        try:
            cookies_path = self.get_cookies_path()
            raw = None
            if os.path.exists(cookies_path):
                raw, st = _cg.decrypt_file(cookies_path, base_dir=self._guard_base(), internal=True)
                if st != 'ok':
                    self._container_unlock = st
                    print(f"Cookie容器解密未通过: {st}")
            if raw is None:
                old = cookies_path.replace('.enc', '')
                if os.path.exists(old):
                    with open(old, 'r', encoding='utf-8') as f:
                        raw = f.read().encode('utf-8')
            if raw is None:
                print(f"Cookie文件不存在: {cookies_path}")
                return False
            cookies = json.loads(raw.decode('utf-8', 'ignore'))
            self.tab.set.cookies(cookies)
            print(f"已解密并加载Cookie: {cookies_path}")
            return True
        except Exception as e:
            print(f"加载Cookie失败: {e}")
            return False

    def has_saved_cookies(self):
        if not self.needs_browser:
            return False
        p = self.get_cookies_path()
        return os.path.exists(p) or os.path.exists(p.replace('.enc', ''))

    def open_login_page(self):
        if not self.needs_browser:
            print(f"{self.site_name} 无需浏览器登录，请使用Cookie字符串输入")
            return False
        print(f"正在打开 {self.site_name} 登录页面...")
        self.tab.get(self.site_config['site_url'])
        print(f"请在浏览器中完成登录操作，登录完成后点击'登录完成'按钮")
        return True

    def complete_login(self):
        self.save_cookies()
        print(f"登录完成，Cookie已保存")
        return True

    def parse_cookie_str(self, cookie_str, domain):
        cookies = []
        items = [item.strip() for item in cookie_str.split(';') if item.strip()]
        for item in items:
            if '=' in item:
                name, value = item.split('=', 1)
                cookies.append({'name': name.strip(), 'value': value.strip(),
                                'domain': domain, 'path': '/'})
            else:
                cookies.append({'name': item.strip(), 'value': '',
                                'domain': domain, 'path': '/'})
        return cookies

    def set_cookie(self):
        if not self.cookie_str:
            return False
        if not self.needs_browser:
            print("该站点无需浏览器，Cookie将由爬虫请求头直接携带")
            return True
        try:
            from urllib.parse import urlparse
            print("正在设置Cookie...")
            netloc = urlparse(self.site_config['site_url']).netloc
            parts = netloc.split(':')[0].split('.')
            domain = '.' + '.'.join(parts[-2:]) if len(parts) >= 2 else netloc
            cookies = self.parse_cookie_str(self.cookie_str, domain)
            print(f"解析到 {len(cookies)} 个Cookie项 (domain={domain})")
            self.tab.set.cookies(cookies)
            print("Cookie已设置（后续访问自动生效）")
            return True
        except Exception as e:
            print(f"设置Cookie失败: {e}")
            return False

    # ============ 剧集相关（由站点插件实现） ============
    def __getattr__(self, name):
        """未定义属性转发给站点插件（如 DouyinCrawler.collect_random/scan_homepage 等新方法自动可用）"""
        sc = self.__dict__.get('site_crawler')
        if sc is not None and hasattr(sc, name):
            return getattr(sc, name)
        raise AttributeError(
            f"'{type(self).__name__}' object has no attribute '{name}'")

    def search_series(self, name, series_id=None):
        """搜索剧集/番剧并打开详情页，返回详情页标签"""
        return self.site_crawler.search_series(name, series_id)

    def get_episode_count(self, target_tab):
        return self.site_crawler.get_episode_count(target_tab)

    def get_episode_list(self, target_tab):
        """返回带标题的完整剧集表（站点支持时）；不支持则返回 None"""
        if hasattr(self.site_crawler, 'get_episode_list') and callable(self.site_crawler.get_episode_list):
            try:
                return self.site_crawler.get_episode_list(target_tab)
            except Exception as e:
                print(f"获取剧集标题失败: {e}")
                return None
        return None

    def collect_episode_videos(self, target_tab, episode_start=1, episode_end=0,
                               max_threads=5, progress_callback=None, keyword=None):
        """收集每集的视频URL列表

        Args:
            keyword: 可选关键词（自动扫描模式用于过滤/限定搜索）
        Returns:
            list[dict]: [{episode_num, title, video_url, video_type, referer}]
        """
        return self.site_crawler.collect_episode_videos(
            target_tab,
            episode_start=episode_start,
            episode_end=episode_end,
            max_threads=max_threads,
            progress_callback=progress_callback,
            keyword=keyword
        )
