# 通用视频下载器 - 内置资源嗅探页（主页格式版，单开一页）
# 功能：两种嗅探模式（输入网址 / 自由浏览）+ 主页式下载功能
#       （速度档选择、下载进度条、网速显示、状态日志）
# 实现：DrissionPage 浏览器 + 网络监听(Listen) + 每2秒 DOM 轮询双通道扫描
import os
import re
import socket as _sock
import tempfile
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

from utils import ensure_console_safe, app_base
ensure_console_safe()

ACCENT = "#3b82f6"
MEDIA_URL = re.compile(r'\.(m3u8|mp4|flv|webm|mkv|mp3|aac|m4a|wav|mov)(\?|#|$)', re.I)
SKIP_EXT = re.compile(r'\.(js|css|png|jpe?g|gif|svg|woff2?|ico|json|xml|webp|bmp)(\?|#|$)', re.I)
SPEED_MAP = {'极速(32线程)': 32, '高速(16线程)': 16, '标准(6线程)': 6, '平稳(3线程)': 3}


class ResDownloaderPage:
    """内置资源嗅探页：主页格式布局 + 主页式下载功能"""

    def __init__(self, parent, gui=None):
        self.parent = parent
        self.gui = gui
        self._sniff_thread = None
        self._dl_thread = None
        self._page = None
        self._stop_flag = False
        self._found = []          # [(url, rtype)]
        self._dl_total = 0
        self._dl_done = 0
        self._last_bytes = 0
        self._last_time = 0.0
        self._build_ui()

    # ---------------- UI（主页格式） ----------------
    def _build_ui(self):
        top = ttk.Frame(self.parent)
        top.pack(fill=tk.X, pady=(0, 4))
        self.state_var = tk.StringVar(
            value="内置资源嗅探：适用于短视频等网页媒体解析下载，播放视频后自动捕获资源（长视频·番剧/哔哩哔哩请用「通用下载」页）")
        ttk.Label(top, textvariable=self.state_var, font=("微软雅黑", 9),
                  foreground=ACCENT).pack(anchor='w')

        # ---- 方式一：输入网址 ----
        url_lf = ttk.LabelFrame(self.parent, text="方式一：输入网址嗅探（打开后播放视频即自动捕获资源）",
                                style="Card.TLabelframe")
        url_lf.pack(fill=tk.X, pady=2)
        row = ttk.Frame(url_lf)
        row.pack(fill=tk.X, pady=3)
        self.url_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.url_var, font=("微软雅黑", 10)).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 4))
        self.sniff_btn = ttk.Button(row, text="开始嗅探", width=10, command=self._start_sniff)
        self.sniff_btn.pack(side=tk.LEFT, padx=2)

        # ---- 方式二：自由浏览 ----
        free_lf = ttk.LabelFrame(self.parent, text="方式二：自由浏览嗅探（直接打开浏览器，自己搜索/打开视频）",
                                 style="Card.TLabelframe")
        free_lf.pack(fill=tk.X, pady=2)
        free_row = ttk.Frame(free_lf)
        free_row.pack(fill=tk.X, pady=3)
        ttk.Label(free_row, text="无需输入地址，点按钮直接打开浏览器，可自行搜索、刷新、滑动页面，"
                                 "所有出现的视频/音频资源都会被自动捕获。",
                  font=("微软雅黑", 9)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 4))
        ttk.Button(free_row, text="打开浏览器自由嗅探", width=16,
                   command=self._start_free_sniff).pack(side=tk.LEFT, padx=2)

        # ---- 视频设置（保存位置，与主界面一致） ----
        path_frame = ttk.LabelFrame(self.parent, text="视频设置", style="Card.TLabelframe")
        path_frame.pack(fill=tk.X, pady=2)
        path_row = ttk.Frame(path_frame)
        path_row.pack(fill=tk.X, pady=2)
        ttk.Label(path_row, text="下载路径:", width=9).pack(side=tk.LEFT)
        self.path_var = tk.StringVar(value=os.path.join(app_base(), 'downloads', '嗅探下载'))
        ttk.Entry(path_row, textvariable=self.path_var, font=("微软雅黑", 10)).pack(
            side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(path_row, text="浏览", width=6,
                   command=self._browse_path).pack(side=tk.LEFT, padx=4)

        # ---- 下载设置（主页同款速度档） ----
        dl_frame = ttk.LabelFrame(self.parent, text="下载设置　（请先选择速度，再点击下载）",
                                  style="Card.TLabelframe")
        dl_frame.pack(fill=tk.X, pady=2)
        dl_row = ttk.Frame(dl_frame)
        dl_row.pack(fill=tk.X, pady=3)
        self.speed_var = tk.StringVar(value='标准(6线程)')
        ttk.Combobox(dl_row, textvariable=self.speed_var, state='readonly', width=16,
                     values=['极速(32线程)', '高速(16线程)', '标准(6线程)', '平稳(3线程)']).pack(
            side=tk.LEFT, padx=2)
        ttk.Label(dl_row, text="极速=32线程并发（HLS分片/直链分段），速度最快；选完速度再点“下载选中资源”",
                  font=("微软雅黑", 9), foreground="#64748b").pack(side=tk.LEFT, padx=6)

        # ---- 嗅探到的资源列表（带勾选框） ----
        list_lf = ttk.LabelFrame(self.parent, text="嗅探到的资源（点击首列方框勾选 → 再点“下载选中资源”）",
                                 style="Card.TLabelframe")
        list_lf.pack(fill=tk.BOTH, expand=True, pady=2)
        cols = ("check", "time", "type", "url")
        self.tree = ttk.Treeview(list_lf, columns=cols, show="headings",
                                 selectmode="none", height=9)
        self.tree.heading("check", text="勾选")
        self.tree.heading("time", text="时间")
        self.tree.heading("type", text="类型")
        self.tree.heading("url", text="资源地址")
        self.tree.column("check", width=45, anchor='center', stretch=False)
        self.tree.column("time", width=70, anchor='w', stretch=False)
        self.tree.column("type", width=90, anchor='w', stretch=False)
        self.tree.column("url", width=600, anchor='w')
        vsb = ttk.Scrollbar(list_lf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=2, pady=2)
        self.tree.bind("<Button-1>", self._on_tree_click)

        # ---- 下载进度（主页同款） ----
        prog_frame = ttk.LabelFrame(self.parent, text="下载进度", style="Card.TLabelframe")
        prog_frame.pack(fill=tk.X, pady=2)
        dl_info = ttk.Frame(prog_frame)
        dl_info.pack(fill=tk.X, pady=2)
        self.progress_label = ttk.Label(dl_info, text="进度: 0/0 个资源", font=("微软雅黑", 10))
        self.progress_label.pack(side=tk.LEFT, padx=4)
        self.speed_label = ttk.Label(dl_info, text="网速: 0 KB/s", font=("微软雅黑", 10))
        self.speed_label.pack(side=tk.RIGHT, padx=4)
        self.progress_bar = ttk.Progressbar(prog_frame, mode='determinate')
        self.progress_bar.pack(fill=tk.X, pady=3)

        # ---- 操作按钮 ----
        btn_row = ttk.Frame(self.parent)
        btn_row.pack(fill=tk.X, pady=4)
        self.stop_btn = ttk.Button(btn_row, text="停止嗅探/关闭浏览器", width=16, command=self._stop_sniff)
        self.stop_btn.pack(side=tk.LEFT, padx=2)
        self.dl_btn = ttk.Button(btn_row, text="下载选中资源", width=14, command=self._download_selected)
        self.dl_btn.pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_row, text="清空列表", width=8,
                   command=self._clear_list).pack(side=tk.LEFT, padx=2)

        # ---- 状态日志 ----
        log_lf = ttk.LabelFrame(self.parent, text="状态日志", style="Card.TLabelframe")
        log_lf.pack(fill=tk.BOTH, expand=True, pady=2)
        self.log_text = tk.Text(log_lf, height=6, font=("Consolas", 9),
                                bg="#f8fafc", wrap=tk.WORD)
        lsb = ttk.Scrollbar(log_lf, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=lsb.set)
        lsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    # ---------------- 工具函数 ----------------
    def _log(self, s):
        def _do():
            try:
                self.log_text.insert(tk.END, s + "\n")
                self.log_text.see(tk.END)
            except Exception:
                pass
        try:
            self.parent.after(0, _do)
        except Exception:
            pass

    def _browse_path(self):
        from tkinter import filedialog
        try:
            d = filedialog.askdirectory(parent=self.parent, title="选择下载保存位置")
            if d:
                self.path_var.set(d)
        except Exception:
            pass

    def _on_tree_click(self, event):
        """点击首列（勾选）切换 ✓/空"""
        try:
            if self.tree.identify("region", event.x, event.y) != "cell":
                return
            if self.tree.identify_column(event.x) != "#1":
                return
            row = self.tree.identify_row(event.y)
            if not row:
                return
            cur = self.tree.set(row, "check")
            self.tree.set(row, "check", "☐" if cur == "☑" else "☑")
        except Exception:
            pass

    @staticmethod
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

    def _ask_manual_browser(self):
        result = {'path': None}
        ev = threading.Event()

        def _do():
            from tkinter import filedialog
            try:
                p = filedialog.askopenfilename(
                    parent=self.parent, title="请选择浏览器程序（msedge.exe 或 chrome.exe）",
                    filetypes=[("浏览器程序", "*.exe")])
                result['path'] = p
            except Exception:
                pass
            finally:
                ev.set()
        try:
            self.parent.after(0, _do)
            ev.wait(180)
        except Exception:
            pass
        return result['path']

    # ---------------- 嗅探启动 ----------------
    def _start_sniff(self):
        url = self.url_var.get().strip()
        if not url.startswith(('http://', 'https://')):
            messagebox.showwarning("地址无效", "请输入 http/https 开头的网页地址")
            return
        if self._sniff_running():
            messagebox.showinfo("已在嗅探", "嗅探正在进行中，可先停止后再开始。")
            return
        self._log(f"[嗅探] 方式一：打开 {url}")
        self.state_var.set("正在启动浏览器...")
        self._sniff_thread = threading.Thread(target=self._sniff_worker,
                                              args=(url,), daemon=True)
        self._sniff_thread.start()

    def _start_free_sniff(self):
        if self._sniff_running():
            messagebox.showinfo("已在嗅探", "嗅探正在进行中，可先停止后再开始。")
            return
        self._log("[嗅探] 方式二：自由浏览模式")
        self.state_var.set("正在启动浏览器...")
        self._sniff_thread = threading.Thread(target=self._sniff_worker,
                                              args=(None,), daemon=True)
        self._sniff_thread.start()

    def _sniff_running(self):
        return (self._sniff_thread is not None and self._sniff_thread.is_alive()
                and not self._stop_flag)

    def _stop_sniff(self):
        """停止嗅探：关闭浏览器"""
        self._stop_flag = True
        self._log("[嗅探] 正在停止并关闭浏览器...")
        try:
            if self._page:
                try:
                    self._page.listen.stop()
                except Exception:
                    pass
                try:
                    self._page.quit()
                except Exception:
                    pass
                self._page = None
        except Exception:
            pass
        self.state_var.set("嗅探已停止")
        self._log("[嗅探] 已停止")

    # ---------------- 嗅探线程 ----------------
    def _sniff_worker(self, url):
        try:
            from DrissionPage import ChromiumOptions, ChromiumPage
        except Exception as e:
            self._log(f"[错误] 加载浏览器引擎失败: {e}")
            self.state_var.set("浏览器引擎加载失败")
            return
        page = None
        try:
            browser_path = self._find_browser_path()
            if not browser_path:
                self._log("[嗅探] 未自动找到浏览器，请手动选择")
                self.state_var.set("未找到浏览器，请手动选择...")
                browser_path = self._ask_manual_browser()
                if not browser_path:
                    self._log("[错误] 未选择浏览器，嗅探已取消")
                    self.state_var.set("未选择浏览器，嗅探已取消")
                    return
            self._log(f"[嗅探] 使用浏览器: {browser_path}")
            co = ChromiumOptions()
            co.set_browser_path(browser_path)
            debug_port = None
            for port in range(9323, 9423):
                try:
                    with _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM) as s:
                        s.bind(('127.0.0.1', port))
                        debug_port = port
                        break
                except OSError:
                    continue
            co.set_local_port(debug_port or 9366)
            user_data_path = tempfile.mkdtemp(prefix='sniff_')
            co.set_user_data_path(user_data_path)
            co.set_argument("--disable-backgrounding-occluded-windows")
            co.set_argument("--disable-renderer-backgrounding")
            co.set_argument("--disable-background-timer-throttling")
            self._log("[嗅探] 正在启动浏览器（首次约 3~8 秒）...")
            page = ChromiumPage(co)
            self._page = page
            self._log("[嗅探] 浏览器已启动")
            if url:
                page.get(url)
                self._log(f"[嗅探] 页面已打开: {url[:80]}")
                self.state_var.set("请在弹出的浏览器中播放视频，资源会自动捕获 ↓")
            else:
                self._log("[嗅探] 自由浏览模式：请在浏览器中自行搜索并打开视频")
                self.state_var.set("自由浏览模式：请自行搜索/打开视频，资源会自动捕获 ↓")
            page.listen.start()
            self._stop_flag = False
            last_dom = 0.0
            while not self._stop_flag:
                # 通道A：网络监听
                try:
                    packets = page.listen.steps(timeout=0.3)
                    for p in packets:
                        try:
                            u = p.url or ''
                        except Exception:
                            continue
                        if not u or SKIP_EXT.search(u):
                            continue
                        if MEDIA_URL.search(u):
                            self._add_found(u, p.resourceType or 'media')
                except Exception:
                    break
                # 通道B：DOM 轮询（每2秒）
                now = time.time()
                if now - last_dom >= 2.0:
                    last_dom = now
                    self._scan_dom(page)
            self._log("[嗅探] 监听已停止")
            self.state_var.set("嗅探已停止")
        except Exception as e:
            self._log(f"[错误] 嗅探失败: {e}")
            self.state_var.set(f"嗅探失败: {e}")
            self._show_error(f"嗅探启动失败：{e}\n\n常见原因：\n1. 浏览器被占用或未安装；\n2. 网络连接异常。")
        finally:
            try:
                if page:
                    try:
                        page.listen.stop()
                    except Exception:
                        pass
                    try:
                        page.quit()
                    except Exception:
                        pass
            except Exception:
                pass
            self._page = None

    def _show_error(self, msg):
        def _do():
            try:
                messagebox.showerror("嗅探失败", msg, parent=self.parent)
            except Exception:
                pass
        try:
            self.parent.after(0, _do)
        except Exception:
            pass

    # ---------------- DOM 轮询扫描（通道B） ----------------
    _DOM_JS = """() => {
        const out = [];
        document.querySelectorAll('video,audio').forEach(v => {
            const s = (v.currentSrc || v.src || '');
            if (s && s.startsWith('http')) out.push(s);
        });
        document.querySelectorAll('video source,audio source').forEach(s => {
            if (s.src && s.src.startsWith('http')) out.push(s.src);
        });
        return out;
    }"""
    _SRC_RE = re.compile(
        r'https?://[^\s"\'<>\\]+?\.(?:m3u8|mp4|flv|webm|mkv|mp3|aac|m4a|wav)(?:\?[^\s"\'<>\\]*)?',
        re.I)

    def _scan_dom(self, page):
        try:
            urls = page.run_js(self._DOM_JS)
            if urls:
                for u in urls:
                    if isinstance(u, str) and MEDIA_URL.search(u) and not SKIP_EXT.search(u):
                        self._add_found(u, 'DOM')
        except Exception:
            pass
        try:
            html = page.html or ''
            for m in self._SRC_RE.findall(html):
                m = m.rstrip('\\')
                if MEDIA_URL.search(m) and not SKIP_EXT.search(m):
                    self._add_found(m, 'SRC')
        except Exception:
            pass

    def _add_found(self, url, rtype):
        if any(x[0] == url for x in self._found):
            return
        self._found.append((url, rtype))
        try:
            self.parent.after(0, self._append_row, url, rtype)
        except Exception:
            pass

    def _append_row(self, url, rtype):
        try:
            self.tree.insert('', 'end',
                             values=('☐', time.strftime('%H:%M:%S'),
                                     rtype or 'media', url))
        except Exception:
            pass

    def _clear_list(self):
        try:
            for i in self.tree.get_children():
                self.tree.delete(i)
        except Exception:
            pass
        self._found = []
        self._log("[嗅探] 列表已清空")

    # ---------------- 下载（主页式） ----------------
    def _download_selected(self):
        sel = [i for i in self.tree.get_children() if self.tree.set(i, "check") == "☑"]
        if not sel:
            messagebox.showinfo("未勾选", "请先点击列表首列的方框勾选要下载的资源")
            return
        if self._dl_thread is not None and self._dl_thread.is_alive():
            messagebox.showinfo("下载中", "已有下载任务进行中，请稍候")
            return
        urls = [self.tree.item(i, 'values')[3] for i in sel]
        self._dl_total = len(urls)
        self._dl_done = 0
        self.progress_label.configure(text=f"进度: 0/{self._dl_total} 个资源")
        self.progress_bar['maximum'] = max(self._dl_total, 1)
        self.progress_bar['value'] = 0
        self._last_bytes = 0
        self._last_time = time.time()
        self.dl_btn.configure(state='disabled')
        self._log(f"[下载] 开始下载 {len(urls)} 个资源，线程数={SPEED_MAP.get(self.speed_var.get(), 6)}")
        self._dl_thread = threading.Thread(target=self._download_worker,
                                           args=(urls,), daemon=True)
        self._dl_thread.start()

    def _download_worker(self, urls):
        try:
            from video_downloader import download_media
        except Exception as e:
            self._log(f"[错误] 下载引擎加载失败: {e}")
            self._dl_done_ui()
            return
        workers = SPEED_MAP.get(self.speed_var.get(), 6)
        base = self.path_var.get().strip() or os.path.join(app_base(), 'downloads', '嗅探下载')
        try:
            os.makedirs(base, exist_ok=True)
        except Exception:
            pass
        total = len(urls)
        for idx, u in enumerate(urls, 1):
            self._log(f"[下载 {idx}/{total}] {u[:90]}")
            name = self._gen_name(u, idx)
            save_path = os.path.join(base, name)

            def progress_cb(done_bytes, total_bytes):
                # 单资源进度 → 当前集进度条
                try:
                    cur = self._dl_done + (done_bytes / max(total_bytes, 1))
                    pct = int(cur / max(total, 1) * 100)
                    self.parent.after(0, self._set_progress_bar, pct)
                    self.parent.after(0, self._update_speed, done_bytes, total_bytes)
                except Exception:
                    pass

            try:
                ok, fp, info = download_media({'url': u, 'type': 'auto'}, save_path,
                                              max_workers=workers, use_ffmpeg=True,
                                              progress=progress_cb, log=self._log)
                if ok and fp:
                    self._log(f"  ✓ 完成: {fp}  ({info})")
                else:
                    self._log(f"  ✗ 失败: {info}")
            except Exception as e:
                self._log(f"  ✗ 下载异常: {e}")
            self._dl_done += 1
            self.parent.after(0, self._set_dl_done, self._dl_done, total)
        self.parent.after(0, self._set_progress_bar, 100)
        self._dl_done_ui()
        self._log(f"[下载] 全部结束（共 {total} 个任务）")

    def _set_progress_bar(self, pct):
        try:
            self.progress_bar['value'] = pct
        except Exception:
            pass

    def _set_dl_done(self, done, total):
        try:
            self.progress_label.configure(text=f"进度: {done}/{total} 个资源")
        except Exception:
            pass

    def _update_speed(self, done_bytes, total_bytes):
        try:
            now = time.time()
            dt = now - self._last_time
            if dt >= 1.0:
                speed = (done_bytes - self._last_bytes) / dt / 1024
                self.speed_label.configure(text=f"网速: {speed:,.0f} KB/s")
                self._last_bytes = done_bytes
                self._last_time = now
        except Exception:
            pass

    def _dl_done_ui(self):
        try:
            self.dl_btn.configure(state='normal')
            self.speed_label.configure(text="网速: 0 KB/s")
        except Exception:
            pass

    @staticmethod
    def _gen_name(url, idx):
        from urllib.parse import urlparse, unquote
        base = os.path.basename(urlparse(url).path)
        base = unquote(base)
        base = re.sub(r'[\\/:*?"<>|]', '_', base)
        if not base or '.' not in base:
            ext = '.mp4'
            m = re.search(r'\.(m3u8|mp4|flv|webm|mkv|mp3|aac|m4a|wav)', url, re.I)
            if m:
                ext = '.' + m.group(1).lower()
            base = f"resource_{idx}{ext}"
        else:
            base = f"{idx:02d}_{base}"
        return base
