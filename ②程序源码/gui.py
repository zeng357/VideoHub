# 通用视频下载器 - GUI（仿漫画下载器界面：左侧导航 + 站点工具栏 + 剧集表勾选下载 + 双进度 + 网速）
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from utils import ensure_console_safe
ensure_console_safe()

from config import BROWSER_PATHS, DEFAULT_SITE, detect_ffmpeg_path
from site_discovery import get_all_site_names, refresh_sites
from video_crawler import VideoCrawler
from download_flow import (run_direct_link, run_auto_scan, run_site_download,
                           _download_episodes, _derive_series_name)

# ---------- 配色（与漫画下载器一致） ----------
SIDEBAR_BG = "#1e293b"        # 侧栏底色 slate-800
SIDEBAR_SEP_BG = "#475569"   # 分隔线 slate-600
NAV_TEXT = "#cbd5e1"          # 未选中导航文字 slate-300
NAV_ACTIVE_BG = "#334155"     # 选中导航底色 slate-700
NAV_ACTIVE_TEXT = "#ffffff"   # 选中导航文字
ACCENT = "#3b82f6"            # 主按钮蓝


class VideoDownloaderGUI:
    def __init__(self, root):
        self.root = root
        root.title("通用视频下载器")
        # 窗口尺寸自适应屏幕，避免内容超出屏幕导致下方“下载状态”日志框不可见
        try:
            sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
            w = min(1000, max(760, sw - 40))
            h = min(800, max(600, sh - 120))
            root.geometry(f"{w}x{h}")
        except Exception:
            root.geometry("1000x800")
        root.minsize(760, 600)

        self.msg_queue = queue.Queue()
        self.worker = None
        self.running = False
        self._random_collecting = False
        self._random_stop = False
        self.ffmpeg_path = detect_ffmpeg_path()

        # 日志落盘：界面日志框被挤出屏幕时也能从 下载日志.txt 看到完整记录
        self._log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '下载日志.txt')
        try:
            with open(self._log_path, 'a', encoding='utf-8') as _f:
                _f.write('\n===== 启动 %s =====\n' % time.strftime('%Y-%m-%d %H:%M:%S'))
        except Exception:
            pass

        # 剧集表状态
        self._episodes = []          # 已加载的剧集列表
        self._episode_vars = {}      # 下标 -> BooleanVar
        self._series_name = ''       # 下载目录名

        # 网速统计
        self._speed_win = []         # [(time, bytes), ...]
        self._speed_prev_done = 0
        self._speed_reset = True
        self._ep_count = 0           # 扫描过程中已找到的视频数

        # 看门狗：下载长时间无进展时提示（防“卡住”无感知）
        self._last_activity = time.time()
        self._stall_warned = False
        self._collecting = False      # 收集剧集阶段（看门狗豁免，防误报）
        self._collect_t0 = 0.0

        self._apply_style()
        self._build_ui()
        self.refresh_site_list()
        self._poll_queue()
        self._watchdog()

    # ==================== 样式 ====================
    def _apply_style(self):
        style = ttk.Style()
        try:
            style.theme_use('clam')
        except Exception:
            pass
        style.configure("Card.TLabelframe", padding="8")
        style.configure("Card.TLabelframe.Label", font=("微软雅黑", 10, "bold"))
        style.configure("TButton", font=("微软雅黑", 9))
        style.configure("Accent.TButton", font=("微软雅黑", 10, "bold"))
        style.map("Accent.TButton",
                  background=[('!disabled', ACCENT), ('active', '#2563eb')],
                  foreground=[('!disabled', 'white')])
        style.configure("TLabel", font=("微软雅黑", 9))
        style.configure("TLabelframe.Label", font=("微软雅黑", 9, "bold"))
        style.configure("TRadiobutton", font=("微软雅黑", 9))
        style.configure("TCheckbutton", font=("微软雅黑", 9))

    # ==================== UI ====================
    def _build_ui(self):
        self.root_container = ttk.Frame(self.root)
        self.root_container.pack(fill=tk.BOTH, expand=True)

        # ===== 左侧导航栏 =====
        self.sidebar = tk.Frame(self.root_container, bg=SIDEBAR_BG, width=110)
        self.sidebar.pack(side=tk.LEFT, fill=tk.Y)
        self.sidebar.pack_propagate(False)

        tk.Label(self.sidebar, text="通用视频\n下载器", bg=SIDEBAR_BG, fg="#ffffff",
                 font=("微软雅黑", 11, "bold"), justify="center").pack(fill=tk.X, pady=(16, 8))
        tk.Frame(self.sidebar, bg=SIDEBAR_SEP_BG, height=1).pack(fill=tk.X, padx=14, pady=6)

        self.nav_main_btn = tk.Button(self.sidebar, text="🏠 主页", font=("微软雅黑", 11),
                                      relief="flat", bd=0, anchor="w", padx=14,
                                      bg=SIDEBAR_BG, fg=NAV_TEXT, activebackground=NAV_ACTIVE_BG,
                                      activeforeground=NAV_ACTIVE_TEXT,
                                      command=lambda: self._switch_page('main'))
        self.nav_main_btn.pack(fill=tk.X, padx=6, pady=2)
        self.nav_settings_btn = tk.Button(self.sidebar, text="⚙ 设置", font=("微软雅黑", 11),
                                          relief="flat", bd=0, anchor="w", padx=14,
                                          bg=SIDEBAR_BG, fg=NAV_TEXT, activebackground=NAV_ACTIVE_BG,
                                          activeforeground=NAV_ACTIVE_TEXT,
                                          command=lambda: self._switch_page('settings'))
        self.nav_settings_btn.pack(fill=tk.X, padx=6, pady=2)

        # ===== 右侧内容区 =====
        self.main_frame = ttk.Frame(self.root_container, padding="10")
        self.main_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # ---------- 主页面 ----------
        self.page_main = ttk.Frame(self.main_frame)
        self.page_main.pack(fill=tk.BOTH, expand=True)

        # ---- 站点选择 + 管理按钮 ----
        site_row = ttk.Frame(self.page_main)
        site_row.pack(fill=tk.X, pady=(0, 2))
        ttk.Label(site_row, text="站点:").pack(side=tk.LEFT, padx=(0, 4))
        self.site_var = tk.StringVar()
        self.site_combo = ttk.Combobox(site_row, textvariable=self.site_var,
                                       state='readonly', width=14)
        self.site_combo.pack(side=tk.LEFT, padx=2)
        ttk.Button(site_row, text="刷新", width=6,
                   command=self.refresh_site_list).pack(side=tk.LEFT, padx=2)
        ttk.Button(site_row, text="打开站点目录", width=11,
                   command=self.open_sites_dir).pack(side=tk.LEFT, padx=2)
        self.site_count_label = ttk.Label(site_row, text="已加载 0 个站点",
                                          font=("微软雅黑", 9))
        self.site_count_label.pack(side=tk.RIGHT, padx=4)
        self.site_var.trace('w', lambda *a: self._on_site_change())

        # ---- 搜索方式 ----
        mode_row = ttk.Frame(self.page_main)
        mode_row.pack(fill=tk.X, pady=(0, 2))
        ttk.Label(mode_row, text="搜索方式:", font=("微软雅黑", 9)).pack(side=tk.LEFT, padx=(0, 4))
        self.mode_var = tk.StringVar(value='1')
        ttk.Radiobutton(mode_row, text="① 自动扫描页面", variable=self.mode_var, value='1',
                        command=self._on_mode_change).pack(side=tk.LEFT, padx=6)
        ttk.Radiobutton(mode_row, text="② 站内搜索下载", variable=self.mode_var, value='2',
                        command=self._on_mode_change).pack(side=tk.LEFT, padx=6)
        ttk.Radiobutton(mode_row, text="③ 直接链接下载", variable=self.mode_var, value='3',
                        command=self._on_mode_change).pack(side=tk.LEFT, padx=6)
        self.mode_hint = ttk.Label(mode_row, text="①自动扫描列表页，填关键词会先搜索定位目标页再扫全集；②按站点搜索；③粘贴直链",
                                   font=("微软雅黑", 8), foreground="gray")
        self.mode_hint.pack(side=tk.LEFT, padx=8)

        # ---- 地址 / 关键词 ----
        addr_row = ttk.Frame(self.page_main)
        addr_row.pack(fill=tk.X, pady=(0, 3))
        ttk.Label(addr_row, text="地址:", width=9).pack(side=tk.LEFT)
        self.addr_var = tk.StringVar()
        ttk.Entry(addr_row, textvariable=self.addr_var, font=("微软雅黑", 10)).pack(
            side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(addr_row, text="复制", width=5,
                   command=self.copy_addr).pack(side=tk.LEFT, padx=(4, 0))

        kw_row = ttk.Frame(self.page_main)
        kw_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(kw_row, text="关键词:", width=9).pack(side=tk.LEFT)
        self.kw_var = tk.StringVar()
        self.kw_entry = ttk.Entry(kw_row, textvariable=self.kw_var, font=("微软雅黑", 10))
        self.kw_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.kw_hint = ttk.Label(kw_row, text="可选：填关键词先站内搜索定位目标页，再在该页扫描全集（如 冰上的尤里）",
                                 font=("微软雅黑", 8), foreground="gray")
        self.kw_hint.pack(side=tk.LEFT, padx=8)

        # ---- 视频设置（下载路径） ----
        path_frame = ttk.LabelFrame(self.page_main, text="视频设置", style="Card.TLabelframe")
        path_frame.pack(fill=tk.X, pady=2)
        path_row = ttk.Frame(path_frame)
        path_row.pack(fill=tk.X, pady=2)
        ttk.Label(path_row, text="下载路径:", width=9).pack(side=tk.LEFT)
        self.path_var = tk.StringVar(value=os.path.dirname(os.path.abspath(__file__)))
        ttk.Entry(path_row, textvariable=self.path_var, font=("微软雅黑", 10)).pack(
            side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(path_row, text="浏览", width=6,
                   command=self.browse_path).pack(side=tk.LEFT, padx=4)

        # ---- 下载设置（独立框：先选速度，再点开始下载） ----
        dl_frame = ttk.LabelFrame(self.page_main, text="下载设置　（请先选择速度，再点击开始下载）",
                                  style="Card.TLabelframe")
        dl_frame.pack(fill=tk.X, pady=2)
        dl_row = ttk.Frame(dl_frame)
        dl_row.pack(fill=tk.X, pady=2)
        ttk.Label(dl_row, text="下载速度:").pack(side=tk.LEFT, padx=(0, 8))
        self.speed_var = tk.StringVar(value='标准(6线程)')
        ttk.Combobox(dl_row, textvariable=self.speed_var, state='readonly', width=16,
                     values=['极速(32线程)', '高速(16线程)', '标准(6线程)', '平稳(3线程)']).pack(
            side=tk.LEFT)
        ttk.Label(dl_row, text="极速=32线程并发（HLS分片/直链分段），速度最快；选完速度再点“开始下载”",
                  font=("微软雅黑", 8), foreground="gray").pack(side=tk.LEFT, padx=12)

        # ---- 按钮行 ----
        btn_row = ttk.Frame(self.page_main)
        btn_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Button(btn_row, text="清空状态", width=10,
                   command=self.clear_status).pack(side=tk.LEFT, padx=3)
        self.load_table_btn = ttk.Button(btn_row, text="加载剧集表", width=12,
                                         command=self.load_episode_table)
        self.load_table_btn.pack(side=tk.LEFT, padx=3)
        self.random_btn = ttk.Button(btn_row, text="🎲 随机抓取", width=10,
                                     command=self.random_grab, state='disabled')
        self.random_btn.pack(side=tk.LEFT, padx=3)
        ttk.Button(btn_row, text="退出", width=8,
                   command=self.root.quit).pack(side=tk.RIGHT, padx=3)
        self.start_btn = ttk.Button(btn_row, text="开始下载", width=14,
                                    style="Accent.TButton", command=self.start_download)
        self.start_btn.pack(side=tk.RIGHT, padx=3)

        # ---- 剧集表（勾选后点“开始下载”，仅下载勾选剧集） ----
        table_frame = ttk.LabelFrame(
            self.page_main,
            text="剧集表（勾选后点“开始下载”，仅下载勾选剧集）",
            style="Card.TLabelframe")
        table_frame.pack(fill=tk.X, pady=2)
        ct_tools = ttk.Frame(table_frame)
        ct_tools.pack(fill=tk.X, pady=(0, 3))
        self.table_info_var = tk.StringVar(value="未加载。填地址/关键词点“加载剧集表”；或选择已添加的站点后只填剧名直接加载")
        ttk.Label(ct_tools, textvariable=self.table_info_var, font=("微软雅黑", 9),
                  foreground="gray").pack(side=tk.LEFT, padx=4)
        ttk.Button(ct_tools, text="全选", width=6,
                   command=self.select_all).pack(side=tk.RIGHT, padx=3)
        ttk.Button(ct_tools, text="全不选", width=7,
                   command=self.select_none).pack(side=tk.RIGHT, padx=3)

        ct_body = ttk.Frame(table_frame)
        ct_body.pack(fill=tk.X)
        self.ep_canvas = tk.Canvas(ct_body, height=88, highlightthickness=0, bg="#ffffff")
        self.ep_scroll = ttk.Scrollbar(ct_body, orient="vertical", command=self.ep_canvas.yview)
        self.ep_list_frame = ttk.Frame(self.ep_canvas)
        self.ep_list_frame.bind(
            "<Configure>",
            lambda e: self.ep_canvas.configure(scrollregion=self.ep_canvas.bbox("all")))
        self.ep_canvas.create_window((0, 0), window=self.ep_list_frame, anchor="nw")
        self.ep_canvas.configure(yscrollcommand=self.ep_scroll.set)
        self.ep_canvas.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.ep_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # ---- 获取视频地址进度 ----
        self.url_progress_frame = ttk.LabelFrame(self.page_main, text="获取视频地址进度",
                                                 style="Card.TLabelframe")
        self.url_progress_frame.pack(fill=tk.X, pady=2)
        self.url_progress_label = ttk.Label(self.url_progress_frame, text="进度: 0/0 个剧集",
                                            font=("微软雅黑", 10))
        self.url_progress_label.pack(side=tk.LEFT, padx=4)
        self.url_progress_bar = ttk.Progressbar(self.url_progress_frame, mode='determinate')
        self.url_progress_bar.pack(fill=tk.X, side=tk.LEFT, expand=True, padx=8, pady=3)

        # ---- 下载进度 ----
        self.progress_frame = ttk.LabelFrame(self.page_main, text="下载进度",
                                             style="Card.TLabelframe")
        self.progress_frame.pack(fill=tk.X, pady=2)
        dl_info = ttk.Frame(self.progress_frame)
        dl_info.pack(fill=tk.X)
        self.progress_label = ttk.Label(dl_info, text="进度: 0/0 个视频", font=("微软雅黑", 10))
        self.progress_label.pack(side=tk.LEFT, padx=4)
        self.speed_label = ttk.Label(dl_info, text="网速: 0 KB/s", font=("微软雅黑", 10))
        self.speed_label.pack(side=tk.RIGHT, padx=4)
        self.progress_bar = ttk.Progressbar(self.progress_frame, mode='determinate')
        self.progress_bar.pack(fill=tk.X, pady=3)
        seg_row = ttk.Frame(self.progress_frame)
        seg_row.pack(fill=tk.X, pady=(0, 2))
        self.seg_label = ttk.Label(seg_row, text="当前集: 0/0 分片", font=("微软雅黑", 9))
        self.seg_label.pack(side=tk.LEFT, padx=4)
        self.seg_bar = ttk.Progressbar(seg_row, mode='determinate')
        self.seg_bar.pack(fill=tk.X, side=tk.LEFT, expand=True, padx=8)

        # ---- 下载状态 ----
        self.status_frame = ttk.LabelFrame(self.page_main, text="下载状态",
                                           style="Card.TLabelframe")
        self.status_frame.pack(fill=tk.BOTH, expand=True, pady=(2, 0))
        self.status_text = tk.Text(self.status_frame, font=("微软雅黑", 9), wrap=tk.WORD,
                                   state=tk.DISABLED, height=5)
        self.status_text.pack(fill=tk.BOTH, expand=True)
        ss = ttk.Scrollbar(self.status_text, command=self.status_text.yview)
        ss.pack(side=tk.RIGHT, fill=tk.Y)
        self.status_text.config(yscrollcommand=ss.set)

        # ---------- 设置页面 ----------
        self.page_settings = ttk.Frame(self.main_frame)

        dl_card = ttk.LabelFrame(self.page_settings, text="下载设置", style="Card.TLabelframe")
        dl_card.pack(fill=tk.X, pady=3)
        row = ttk.Frame(dl_card)
        row.pack(fill=tk.X, pady=2)
        ttk.Label(row, text="下载速度:").pack(side=tk.LEFT, padx=(0, 8))
        ttk.Combobox(row, textvariable=self.speed_var, state='readonly', width=16,
                     values=['极速(32线程)', '高速(16线程)', '标准(6线程)', '平稳(3线程)']).pack(
            side=tk.LEFT)
        ttk.Label(row, text="HLS分片/直链分段的并发线程数", font=("微软雅黑", 8),
                  foreground="gray").pack(side=tk.LEFT, padx=12)

        br_card = ttk.LabelFrame(self.page_settings, text="浏览器设置", style="Card.TLabelframe")
        br_card.pack(fill=tk.X, pady=3)
        r1 = ttk.Frame(br_card)
        r1.pack(fill=tk.X, pady=2)
        ttk.Label(r1, text="浏览器:").pack(side=tk.LEFT, padx=(0, 8))
        self.browser_var = tk.StringVar(value='edge')
        ttk.Combobox(r1, textvariable=self.browser_var, state='readonly',
                     values=['edge', 'chrome'], width=10).pack(side=tk.LEFT)
        self.headless_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(r1, text="无头模式（不显示浏览器窗口）",
                        variable=self.headless_var).pack(side=tk.LEFT, padx=16)

        r2 = ttk.Frame(br_card)
        r2.pack(fill=tk.X, pady=2)
        ttk.Label(r2, text="Cookie:").pack(side=tk.LEFT, padx=(0, 8))
        self.cookie_var = tk.StringVar()
        ttk.Entry(r2, textvariable=self.cookie_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(r2, text="(可选)需登录的站点填浏览器Cookie", font=("微软雅黑", 8),
                  foreground="gray").pack(side=tk.LEFT, padx=8)

        ffmpeg_txt = self.ffmpeg_path or "未检测到（HLS将保留为.ts格式，B站合成需ffmpeg）"
        ttk.Label(br_card, text=f"ffmpeg: {ffmpeg_txt}", font=("微软雅黑", 9)).pack(
            anchor='w', padx=4, pady=(4, 2))

        self._switch_page('main')
        self._on_mode_change()

    def _switch_page(self, page):
        if page == 'main':
            self.page_settings.pack_forget()
            self.page_main.pack(fill=tk.BOTH, expand=True)
            self._update_nav_highlight('main')
        else:
            self.page_main.pack_forget()
            self.page_settings.pack(fill=tk.BOTH, expand=True)
            self._update_nav_highlight('settings')

    def _update_nav_highlight(self, page):
        for btn, name, active in ((self.nav_main_btn, 'main', page == 'main'),
                                  (self.nav_settings_btn, 'settings', page == 'settings')):
            if active:
                btn.configure(bg=NAV_ACTIVE_BG, fg=NAV_ACTIVE_TEXT)
            else:
                btn.configure(bg=SIDEBAR_BG, fg=NAV_TEXT)

    # ==================== 模式切换 ====================
    def _real_site(self):
        """当前选中的真实站点（非“自动扫描”）"""
        site = self.site_var.get()
        return site if site and site != '自动扫描' else None

    def _on_site_change(self):
        if getattr(self, '_random_collecting', False):
            self._random_stop = True
        self._refresh_hints()
        self._refresh_random_btn()

    def _refresh_random_btn(self):
        """随机抓取按钮：仅抖音精选启用（独立按键，与搜索方式无关）"""
        if hasattr(self, 'random_btn'):
            self.random_btn.configure(
                state='normal' if self._real_site() == '抖音精选' else 'disabled')

    def _refresh_hints(self):
        """根据 搜索方式 + 站点选择 更新提示文案"""
        mode = self.mode_var.get()
        site = self._real_site()
        if mode == '1':
            if site:
                self.mode_hint.configure(
                    text=f"已选站点「{site}」：只需填剧名点“加载剧集表”，无需复制地址")
                self.kw_hint.configure(text=f"剧名（必填，将直接用站点「{site}」搜索并加载全部剧集）")
            else:
                self.mode_hint.configure(text="①自动扫描列表页，填关键词会先搜索定位目标页再扫全集")
                self.kw_hint.configure(text="可选：填关键词先站内搜索定位目标页，再在该页扫描全集（如 冰上的尤里）")
        elif mode == '2':
            self.kw_hint.configure(text="站内搜索模式：地址栏填剧集名称，站点选对应站点")
            self.mode_hint.configure(text="②按所选站点搜索剧集，扫描全部集数到剧集表")
        else:
            self.kw_hint.configure(text="直接链接模式：地址栏填 mp4/m3u8 直链")
            self.mode_hint.configure(text="③无需浏览器，粘贴直链直接下载")

    def _on_mode_change(self):
        mode = self.mode_var.get()
        if mode == '1':
            self.kw_entry.configure(state='normal')
            self.load_table_btn.configure(text="加载剧集表")
        elif mode == '2':
            self.kw_entry.configure(state='disabled')
            self.load_table_btn.configure(text="加载剧集表")
        else:
            self.kw_entry.configure(state='disabled')
            self.load_table_btn.configure(text="加载为单集")
        self._refresh_hints()

    def copy_addr(self):
        val = self.addr_var.get().strip()
        if val:
            self.root.clipboard_clear()
            self.root.clipboard_append(val)
            self.append_status(f"已复制: {val[:80]}")

    # ==================== 站点管理 ====================
    def refresh_site_list(self):
        try:
            refresh_sites()
            names = get_all_site_names()
        except Exception:
            names = get_all_site_names()
        self.site_combo['values'] = names
        if names:
            cur = self.site_var.get()
            if cur not in names:
                if DEFAULT_SITE in names:
                    self.site_var.set(DEFAULT_SITE)
                else:
                    self.site_var.set(names[0])
        self.site_count_label.configure(text=f"已加载 {len(names)} 个站点")
        self.append_status(f"检测到 {len(names)} 个可用站点/模式")

    def open_sites_dir(self):
        data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sites_data')
        os.makedirs(data_dir, exist_ok=True)
        os.startfile(data_dir)

    def browse_path(self):
        path = filedialog.askdirectory(initialdir=self.path_var.get() or None)
        if path:
            self.path_var.set(path)

    # ==================== 日志 ====================
    def _write_log(self, text):
        """追加写入本地日志文件（界面日志框不可见时也能排查）"""
        try:
            with open(self._log_path, 'a', encoding='utf-8') as f:
                f.write(time.strftime('%H:%M:%S') + '  ' + text + '\n')
        except Exception:
            pass

    def append_status(self, text):
        self._last_activity = time.time()
        self._write_log(text)
        self.status_text.configure(state='normal')
        self.status_text.insert(tk.END, text + '\n')
        self.status_text.see(tk.END)
        self.status_text.configure(state='disabled')

    def clear_status(self):
        self.status_text.configure(state='normal')
        self.status_text.delete('1.0', tk.END)
        self.status_text.configure(state='disabled')
        self._set_url_progress(0, 0)
        self._set_dl_progress(0, 0, '0 KB/s')
        self._reset_seg()
        self.table_info_var.set("未加载。填地址/关键词点“加载剧集表”；或选择已添加的站点后只填剧名直接加载")

    # ==================== 抖音精选：随机抓取（人控持续抓取） ====================
    def random_grab(self):
        """双向按钮：未抓取时点击=开始持续抓取（按钮变「停止」）；抓取中点=停止并填充已抓视频"""
        if getattr(self, '_random_collecting', False):
            # 正在抓取 → 点停止
            self._random_stop = True
            self.append_status("已发出停止指令，正在收尾并填充已抓到的视频...")
            self.random_btn.configure(text="正在停止...", state='disabled')
            return
        site = self._real_site()
        if site != '抖音精选':
            messagebox.showwarning("提示", "「\U0001F3B2 随机抓取」仅适用于「抖音精选」站点")
            return
        if self.running:
            return
        self.running = True
        self.load_table_btn.configure(state='disabled')
        self.start_btn.configure(state='disabled')
        self._random_stop = False
        self._random_collecting = True
        self.random_btn.configure(text="停止", state='normal')
        self.table_info_var.set("随机抓取进行中：想抓多少就等多久，随时点「停止」结束并填入剧集表")
        self.append_status("开始持续随机抓取: 站点=抖音精选（抓取时间与数量由你控制，随时点「停止」）")
        self.url_progress_label.configure(text="已抓到: 0 个视频（抓取中...）")
        self.url_progress_bar['value'] = 0
        self._ep_count = 0
        opts = {
            'site': '抖音精选',
            'browser_path': BROWSER_PATHS.get(self.browser_var.get(), BROWSER_PATHS['edge']),
            'headless': self.headless_var.get(),
            'cookie': self.cookie_var.get().strip() or None,
            'start': 1,
        }

        def _progress(n):
            self.msg_queue.put((self._random_progress, (n,)))

        def _worker():
            crawler = None
            try:
                crawler = VideoCrawler('抖音精选', opts['browser_path'],
                                       opts['headless'], opts['cookie'])
                crawler.log_callback = lambda s: self.msg_queue.put((self.append_status, (s,)))
                crawler.search_series('')
                eps = crawler.collect_random(
                    crawler.tab,
                    stop_flag_func=lambda: getattr(self, '_random_stop', False),
                    progress_callback=_progress,
                    max_items=500)
                self.msg_queue.put((self._random_done, (eps, None)))
            except Exception as e:
                self.msg_queue.put((self._random_done, ([], str(e))))
            finally:
                try:
                    if crawler is not None:
                        crawler.close()
                except Exception:
                    pass

        self.random_worker = threading.Thread(target=_worker, daemon=True)
        self.random_worker.start()

    def _random_progress(self, n):
        """抓取中实时更新已抓数量"""
        if self._random_collecting:
            self.url_progress_label.configure(text=f"已抓到: {n} 个视频（抓取中...）")
            self.url_progress_bar['value'] = min(n, 100)

    def _random_done(self, eps, error):
        """停止/结束后：复原按钮并填充剧集表"""
        self._random_collecting = False
        self.running = False
        self.load_table_btn.configure(state='normal')
        self.start_btn.configure(state='normal')
        self.random_btn.configure(text="\U0001F3B2 随机抓取", state='normal')
        if error:
            self.append_status(f"随机抓取失败: {error}")
            self.table_info_var.set(f"随机抓取失败: {error}")
            messagebox.showwarning("提示", f"随机抓取失败:\n{error}")
            return
        if not eps:
            self.append_status("随机抓取未获取到视频（可能风控/网络，稍后再试）")
            self.table_info_var.set("未抓到视频（可能抖音风控，等1-2分钟再试）")
            return
        self._set_table(eps, '抖音精选-随机')
        self.append_status(f"随机抓取完成，共 {len(eps)} 个视频已填入剧集表，可勾选后选速度开始下载")
        self.table_info_var.set(f"已随机抓到 {len(eps)} 个视频（勾选后点「开始下载」）")

    # ==================== 剧集表 ====================
    def load_episode_table(self):
        """只扫描/收集剧集地址到剧集表（不下载），供勾选后下载"""
        if self.running:
            return
        mode = self.mode_var.get()
        addr = self.addr_var.get().strip()
        kw = self.kw_var.get().strip()
        site = self._real_site()

        if mode == '3' and not addr:
            messagebox.showwarning("提示", "请输入视频链接")
            return
        if mode == '2' and not addr:
            messagebox.showwarning("提示", "请输入要搜索的剧集名称")
            return
        if mode == '1' and not addr:
            if site and kw:
                pass  # 已选站点 + 填了剧名 → 直接用站点搜索，无需地址
            elif site and not kw:
                # 站点支持主页扫描（如 华丽装饰）：关键词留空 = 扫描主页全部视频名称
                pass
            else:
                messagebox.showwarning(
                    "提示", "请输入地址；或先选择已添加的站点（如 哔哩哔哩），再只填剧名直接加载剧集表")
                return
        if mode == '2' and not site:
            messagebox.showwarning("提示", "请选择站点")
            return

        self.running = True
        self.load_table_btn.configure(state='disabled')
        self.start_btn.configure(state='disabled')
        self.table_info_var.set("正在扫描剧集列表，请稍候...")
        self.append_status(f"开始扫描: 方式{ {'1': '自动扫描', '2': '站内搜索', '3': '直接链接'}[mode] } "
                           + (f"站点={site} " if site else "") + f"输入={addr or kw}")
        self.url_progress_label.configure(text="进度: 0/0 个剧集")
        self.url_progress_bar['value'] = 0
        self._ep_count = 0

        # 主线程快照界面值，后台线程只读快照（避免跨线程操作Tk变量）
        opts = {
            'site': site,
            'browser_path': BROWSER_PATHS.get(self.browser_var.get(), BROWSER_PATHS['edge']),
            'headless': self.headless_var.get(),
            'cookie': self.cookie_var.get().strip() or None,
            'start': 1,
        }
        try:
            opts['start'] = int(self.start_var.get() or '1') if hasattr(self, 'start_var') else 1
        except ValueError:
            pass

        self.worker = threading.Thread(target=self._load_worker,
                                       args=(mode, addr, kw, opts), daemon=True)
        self.worker.start()

    def _load_worker(self, mode, addr, kw, opts):
        def post(fn, *args):
            self.msg_queue.put((fn, args))

        def log(t):
            post(self.append_status, t)

        def url_bump():
            post(self._url_bump)

        try:
            eps = []
            series_name = ''
            site = opts.get('site')
            if mode == 'random':
                # 抖音精选随机抓取：独立流程，不受搜索方式/地址/关键词约束
                crawler = VideoCrawler('抖音精选', opts['browser_path'],
                                       opts['headless'], opts['cookie'])
                crawler.log_callback = lambda s: post(self.append_status, s)
                self._collecting = True
                self._collect_t0 = time.time()
                try:
                    result = run_site_download(crawler, '随机精选', episode_start=1,
                                               episode_end=0, download_path=None,
                                               log=log, url_progress_callback=url_bump,
                                               collect_only=True)
                    series_name = '抖音精选-随机'
                finally:
                    self._collecting = False
                    self._close_crawler(crawler, log)
            elif mode == '3':
                ep = {'episode_num': 1, 'title': 'video', 'video_url': addr,
                      'video_type': 'auto', 'referer': None}
                eps = [ep]
                series_name = _derive_series_name(addr)
                log("直接链接模式：已加入单集")
            else:
                crawler_site = (opts.get('site') or '自动扫描') if (mode == '2' or (mode == '1' and not addr)) else '自动扫描'
                crawler = VideoCrawler(crawler_site, opts['browser_path'],
                                       opts['headless'], opts['cookie'])
                crawler.log_callback = lambda s: post(self.append_status, s)
                self._collecting = True
                self._collect_t0 = time.time()
                try:
                    if mode == '1' and not addr and site and kw:
                        # 已选站点 + 只填剧名 → 直接用站点站内搜索（无需地址）
                        log(f"使用站点「{site}」直接搜索: {kw}")
                        result = run_site_download(crawler, kw, episode_start=1,
                                                   episode_end=0, download_path=None,
                                                   log=log, url_progress_callback=url_bump,
                                                   collect_only=True)
                        series_name = kw
                    elif mode == '1' and not addr and site and not kw and hasattr(crawler, 'scan_homepage'):
                        # 站点支持主页扫描：列出主页全部视频名称，供勾选后逐个下载
                        log(f"使用站点「{site}」扫描主页全部视频...")
                        home_items = crawler.scan_homepage() or []
                        eps = []
                        for _i, (_nm, _u) in enumerate(home_items):
                            eps.append({'episode_num': _i + 1, 'title': _nm,
                                        'video_url': _u, 'video_type': 'series',
                                        'referer': None, 'series': True})
                        series_name = f"{site}-主页视频"
                        log(f"主页扫描完成: {len(eps)} 部视频（勾选后点“开始下载”，自动分析集数并下载）")
                    elif mode == '1':
                        result = run_auto_scan(crawler, addr, keyword=kw,
                                               download_path=None, log=log,
                                               url_progress_callback=url_bump,
                                               collect_only=True)
                        series_name = kw or _derive_series_name(addr)
                    else:
                        result = run_site_download(crawler, addr, episode_start=opts.get('start', 1),
                                                   episode_end=0, download_path=None,
                                                   log=log, url_progress_callback=url_bump,
                                                   collect_only=True)
                        series_name = addr
                    eps = result.get('eps') or []
                finally:
                    self._collecting = False
                    self._close_crawler(crawler, log)
            post(self._set_table, eps, series_name)
        except Exception as e:
            import traceback
            log(f"扫描失败: {e}")
            log(traceback.format_exc())
            post(self._table_load_done, f"扫描失败: {e}")
        finally:
            post(self._table_load_done, '')

    def _set_table(self, eps, series_name):
        """主线程：清空旧表并填入剧集行"""
        for w in self.ep_list_frame.winfo_children():
            w.destroy()
        self._episode_vars.clear()
        self._episodes = eps
        self._series_name = series_name

        if not eps:
            self.table_info_var.set("未扫描到剧集（可能需登录/无视频）")
            self._set_url_progress(0, 0)
            return
        for i, ep in enumerate(eps):
            var = tk.BooleanVar(value=True)
            self._episode_vars[i] = var
            num = ep.get('episode_num', i + 1)
            title = ep.get('title') or ''
            url = (ep.get('video_url') or '')[:70]
            if ep.get('series'):
                url = ''  # 主页视频行：只显示名称
            row = ttk.Frame(self.ep_list_frame)
            row.pack(fill=tk.X, padx=4, pady=1)
            cb = ttk.Checkbutton(row, variable=var)
            cb.pack(side=tk.LEFT)
            ttk.Label(row, text=f"{num:0>3} {title}".strip() + ("  " + url if url else ''),
                      font=("微软雅黑", 9)).pack(side=tk.LEFT, padx=4)
        self._set_url_progress(len(eps), len(eps))
        hint = "已勾选全部剧集" if self._any_checked() else "未勾选任何剧集"
        self.table_info_var.set(f"已加载 {len(eps)} 集（{hint}，可全选/全不选后下载）")
        self.append_status(f"剧集表已加载: 共 {len(eps)} 集，下载目录名: {series_name or '(自动)'}")

    def _table_load_done(self, err):
        self.running = False
        self.load_table_btn.configure(state='normal')
        self.start_btn.configure(state='normal')
        if err:
            self.table_info_var.set(err)

    def _any_checked(self):
        return any(v.get() for v in self._episode_vars.values())

    def select_all(self):
        for v in self._episode_vars.values():
            v.set(True)
        if self._episodes:
            self.table_info_var.set(f"已全选 {len(self._episodes)} 集，可点“开始下载”")

    def select_none(self):
        for v in self._episode_vars.values():
            v.set(False)
        if self._episodes:
            self.table_info_var.set("已全不选。可点“全选”或手动勾选后下载")

    # ==================== 下载任务 ====================
    def start_download(self):
        if self.running:
            return
        mode = self.mode_var.get()

        # 剧集表已加载 → 下载勾选的剧集
        if self._episodes:
            selected = [ep for i, ep in enumerate(self._episodes)
                        if self._episode_vars.get(i, tk.BooleanVar(value=False)).get()]
            if not selected:
                messagebox.showwarning("提示", "请先在剧集表中勾选要下载的剧集")
                return
            self.running = True
            self.start_btn.configure(state='disabled')
            self.load_table_btn.configure(state='disabled')
            if any(ep.get('series') for ep in selected):
                self.append_status(f"按勾选下载 {len(selected)}/{len(self._episodes)} 部视频...")
            else:
                self.append_status(f"按勾选下载 {len(selected)}/{len(self._episodes)} 集...")
            self._set_dl_progress(0, len(selected), '0 KB/s')
            opts = {'path': self.path_var.get().strip() or None,
                    'workers': self._speed_to_workers()}
            self.worker = threading.Thread(
                target=self._worker_selected, args=(selected, opts), daemon=True)
            self.worker.start()
            return

        # 未加载剧集表 → 传统一键流程（扫描并下载）
        addr = self.addr_var.get().strip()
        kw = self.kw_var.get().strip()
        site = self._real_site()
        if mode == '1' and not addr and not (site and kw):
            messagebox.showwarning("提示", "请输入要扫描的地址；或选择站点后只填剧名直接下载")
            return
        if mode == '2' and not addr:
            messagebox.showwarning("提示", "请输入要搜索的剧集名称")
            return
        if mode == '3' and not addr:
            messagebox.showwarning("提示", "请输入视频链接")
            return
        if mode == '2' and not site:
            messagebox.showwarning("提示", "请选择站点")
            return

        self.running = True
        self.start_btn.configure(state='disabled')
        self.load_table_btn.configure(state='disabled')
        opts = {
            'path': self.path_var.get().strip() or None,
            'cookie': self.cookie_var.get().strip() or None,
            'workers': self._speed_to_workers(),
            'browser_path': BROWSER_PATHS.get(self.browser_var.get(), BROWSER_PATHS['edge']),
            'headless': self.headless_var.get(),
            'site': self._real_site(),
        }
        try:
            opts['start'] = int(self.start_var.get() or '1') if hasattr(self, 'start_var') else 1
        except ValueError:
            opts['start'] = 1
        try:
            opts['end'] = int(self.end_var.get() or '0') if hasattr(self, 'end_var') else 0
        except ValueError:
            opts['end'] = 0
        self.worker = threading.Thread(target=self._worker_legacy,
                                       args=(mode, addr, kw, opts), daemon=True)
        self.worker.start()

    def _worker_selected(self, selected, opts):
        """后台线程：下载剧集表中勾选的剧集"""
        def post(fn, *args):
            self.msg_queue.put((fn, args))

        def log(t):
            post(self.append_status, t)

        # 主页扫描模式：勾选的是“视频”而非“集”，逐个视频自动分析集数并下载全部集
        if any(ep.get('series') for ep in selected):
            self._worker_home_series(selected, opts, post, log)
            return

        try:
            download_path = opts.get('path')
            max_workers = opts.get('workers', 6)
            total = len(selected)
            log(f"开始下载 {total} 集（目录: {download_path or '当前目录'}，速度档线程数={max_workers}）")
            for ep in selected:
                log(f"  -> 第{ep.get('episode_num')}集 {ep.get('title', '')[:40]} "
                    f"[{'DASH合成' if ep.get('audio_url') else ep.get('video_type') or 'auto'}]")
            def dl_cb(done, t):
                post(self._dl_byte_progress, done, t)
                post(self._dl_seg_progress, done, t)

            def ep_cb(d, t):
                post(self._set_dl_progress, d, t, None)
                post(self._reset_seg)

            result = _download_episodes(
                selected, download_path, self._series_name or '视频', log=log,
                progress_callback=dl_cb,
                max_workers=max_workers, use_ffmpeg=True, timeout=20,
                episode_callback=ep_cb)
            post(self._set_dl_progress, total, total, None)
            if result.get('failed'):
                log(f"✗ {len(result['failed'])}/{total} 集下载失败")
                post(self._notify_failed, result['failed'])
            else:
                log(f"✓ 勾选下载完成: {total} 集")
        except Exception as e:
            import traceback
            log(f"错误: {e}")
            log(traceback.format_exc())
        finally:
            post(self._finish)

    def _worker_home_series(self, selected, opts, post, log):
        """主页扫描模式：对每个勾选的视频，自动分析集数并下载全部集"""
        def dl_cb(done, t):
            post(self._dl_byte_progress, done, t)
            post(self._dl_seg_progress, done, t)

        def ep_cb(d, t):
            post(self._set_dl_progress, d, t, None)
            post(self._reset_seg)

        download_path = opts.get('path')
        max_workers = opts.get('workers', 6)
        total = len(selected)
        fail_list = []
        try:
            crawler = VideoCrawler(self._real_site(),
                                   BROWSER_PATHS.get(self.browser_var.get(), BROWSER_PATHS['edge']),
                                   self.headless_var.get(),
                                   self.cookie_var.get().strip() or None)
            crawler.log_callback = lambda s: post(self.append_status, s)
            for i, ep in enumerate(selected):
                name = (ep.get('title') or '').strip()
                post(self._set_dl_progress, 0, 1, '0 KB/s')
                post(self._reset_seg)
                log(f"[{i + 1}/{total}] 正在处理视频「{name}」...")
                try:
                    result = run_site_download(crawler, name, episode_start=1, episode_end=0,
                                               download_path=download_path, log=log,
                                               progress_callback=dl_cb, episode_callback=ep_cb,
                                               max_workers=max_workers, use_ffmpeg=True, timeout=20)
                    if result.get('failed'):
                        fail_list.extend(result['failed'])
                        log(f"  ✗「{name}」有 {len(result['failed'])} 集下载失败")
                    else:
                        log(f"  ✓「{name}」全部集下载完成")
                except Exception as e:
                    fail_list.append({'title': name, 'info': str(e)})
                    log(f"  ✗「{name}」失败: {str(e)[:120]}")
            self._close_crawler(crawler, log)
            if fail_list:
                post(self._notify_failed, fail_list)
            else:
                log(f"✓ 全部 {total} 部视频下载完成")
                post(messagebox.showinfo, "完成", f"已下载全部 {total} 部视频")
        except Exception as e:
            log(f"主页视频下载异常: {e}")
            import traceback
            log(traceback.format_exc())
        finally:
            self._finish()

    def _worker_legacy(self, mode, addr, kw, opts):
        """后台线程：未加载剧集表时的一键扫描+下载"""
        def post(fn, *args):
            self.msg_queue.put((fn, args))

        def log(t):
            post(self.append_status, t)

        def progress_cb(done, total):
            percent = 0
            if total:
                percent = min(100.0, done * 100.0 / total)
            post(self._update_progress, percent, f"{done}/{total}")
            post(self._dl_seg_progress, done, total)

        try:
            download_path = opts.get('path')
            cookie_str = opts.get('cookie')
            max_workers = opts.get('workers', 6)

            if mode == '3':
                post(self._update_progress, 0, "直接下载")
                result = run_direct_link(addr, download_path, log=log,
                                         progress_callback=progress_cb,
                                         max_workers=max_workers)
                if result['success']:
                    log(f"✓ 下载完成: {result['final_path']}")
                else:
                    log(f"✗ 下载失败: {result['info']}")
                post(self._update_progress, 100, "完成")
            else:
                browser_path = opts.get('browser_path')
                headless = opts.get('headless')
                if mode == '1' and not addr and opts.get('site') and kw:
                    # 已选站点 + 只填剧名 → 直接用站点站内搜索并下载（无需地址）
                    crawler = VideoCrawler(opts['site'], browser_path, headless, cookie_str)
                    crawler.log_callback = lambda s: post(self.append_status, s)
                    try:
                        result = run_site_download(
                            crawler, kw, episode_start=1, episode_end=0,
                            download_path=download_path, log=log,
                            url_progress_callback=lambda: post(self._bump_progress),
                            progress_callback=progress_cb, max_workers=max_workers)
                    finally:
                        self._close_crawler(crawler, log)
                elif mode == '1':
                    crawler = VideoCrawler('自动扫描', browser_path, headless, cookie_str)
                    crawler.log_callback = lambda s: post(self.append_status, s)
                    try:
                        result = run_auto_scan(
                            crawler, addr, keyword=kw, download_path=download_path, log=log,
                            url_progress_callback=lambda: post(self._bump_progress),
                            progress_callback=progress_cb, max_workers=max_workers)
                    finally:
                        self._close_crawler(crawler, log)
                else:
                    try:
                        start = int(self.start_var.get() or '1') if hasattr(self, 'start_var') else 1
                    except ValueError:
                        start = 1
                    try:
                        end = int(self.end_var.get() or '0') if hasattr(self, 'end_var') else 0
                    except ValueError:
                        end = 0
                    crawler = VideoCrawler(opts.get('site') or '自动扫描', browser_path, headless, cookie_str)
                    crawler.log_callback = lambda s: post(self.append_status, s)
                    try:
                        result = run_site_download(
                            crawler, addr, episode_start=start, episode_end=end,
                            download_path=download_path, log=log,
                            url_progress_callback=lambda: post(self._bump_progress),
                            progress_callback=progress_cb, max_workers=max_workers,
                            pre_collect_hook=lambda n: post(self._reset_episode_progress, n))
                    finally:
                        self._close_crawler(crawler, log)

                failed = result.get('failed') or []
                log(f"完成: 成功 {result['done']}/{result['total']}, 失败 {len(failed)}")
                if failed:
                    log("失败项: " + ", ".join(f"第{e.get('episode_num')}集" for e in failed))
                log(f"保存目录: {result.get('series_dir')}")
                post(self._update_progress, 100, "完成")
        except Exception as e:
            log(f"错误: {e}")
            import traceback
            log(traceback.format_exc())
            post(self._update_progress, 0, "失败")
        finally:
            post(self._finish)

    def _speed_to_workers(self):
        return {'极速(32线程)': 32, '高速(16线程)': 16,
                '标准(6线程)': 6, '平稳(3线程)': 3}.get(self.speed_var.get(), 6)

    def _close_crawler(self, crawler, log):
        try:
            if getattr(crawler, 'page', None) is not None:
                crawler.page.close()
                log("浏览器已关闭")
        except Exception:
            pass

    def stop_download(self):
        self.running = False
        self.append_status("正在停止（当前任务完成后退出）...")

    # ==================== UI 更新（主线程） ====================
    def _poll_queue(self):
        try:
            while True:
                fn, args = self.msg_queue.get_nowait()
                try:
                    fn(*args)
                except Exception:
                    pass
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _set_url_progress(self, done, total):
        self.url_progress_label.configure(text=f"进度: {done}/{total} 个剧集")
        self.url_progress_bar['maximum'] = max(total, 1)
        self.url_progress_bar['value'] = done

    def _url_bump(self):
        cur = self._ep_count
        self._ep_count = cur + 1
        n = self._ep_count
        self.url_progress_label.configure(text=f"进度: 已找到 {n} 个视频...")

    def _set_dl_progress(self, done, total, speed_text):
        self.progress_label.configure(text=f"进度: {done}/{total} 个视频")
        self.progress_bar['maximum'] = max(total, 1)
        self.progress_bar['value'] = done
        if speed_text:
            self.speed_label.configure(text=f"网速: {speed_text}")

    def _dl_byte_progress(self, done, total):
        """按字节统计网速（下载引擎回调传字节数）"""
        now = time.time()
        self._last_activity = now
        delta = done - self._speed_prev_done
        if delta < 0:
            delta = done  # 新文件重新计数
        self._speed_prev_done = done
        self._speed_win.append((now, delta))
        # 只保留最近 2 秒的窗口
        cutoff = now - 2.0
        self._speed_win = [(t, b) for t, b in self._speed_win if t >= cutoff]
        total_b = sum(b for _, b in self._speed_win)
        span = now - self._speed_win[0][0] if self._speed_win else 0
        if span >= 0.5:
            speed = total_b / span
            if speed >= 1024 * 1024:
                txt = f"{speed / 1024 / 1024:.1f} MB/s"
            else:
                txt = f"{speed / 1024:.0f} KB/s"
            self.speed_label.configure(text=f"网速: {txt}")

    def _update_progress(self, percent, text):
        self.progress_bar['value'] = percent
        self.progress_label.configure(text=f"进度: {text} 个视频")

    def _dl_seg_progress(self, done, total):
        """当前集下载进度条：HLS 显示分片 x/y，直链显示百分比"""
        try:
            total = int(total)
            done = int(done)
        except Exception:
            return
        self.seg_bar['maximum'] = max(total, 1)
        self.seg_bar['value'] = min(done, total)
        if total > 10000:  # 直链：按字节百分比
            pct = done * 100.0 / total if total else 0
            self.seg_label.configure(text=f"当前集: {pct:.0f}%")
        else:  # HLS：分片数
            self.seg_label.configure(text=f"当前集: {done}/{total} 分片")

    def _reset_seg(self):
        self.seg_bar['value'] = 0
        self.seg_label.configure(text="当前集: 0/0 分片")

    def _bump_progress(self):
        cur = self.progress_bar['value']
        self.progress_bar['value'] = min(100, cur + 1)

    def _reset_episode_progress(self, n):
        self.progress_bar['value'] = 0
        self.progress_label.configure(text=f"进度: 0/{n} 个视频")

    def _finish(self):
        self.running = False
        self._stall_warned = False
        self.start_btn.configure(state='normal')
        self.load_table_btn.configure(state='normal')

    def _notify_failed(self, failed):
        """下载失败时弹窗汇总原因（避免用户只看到进度不动）"""
        lines = []
        for ep in failed[:5]:
            title = (ep.get('title') or '')[:30]
            info = (ep.get('info') or '未知原因')[:120]
            lines.append(f"第{ep.get('episode_num')}集 {title}: {info}")
        more = f"\n……共 {len(failed)} 集失败" if len(failed) > 5 else ''
        self.append_status("失败详情（已弹窗提示）: " + " | ".join(lines) + more)
        messagebox.showwarning(
            "下载失败",
            "以下剧集下载失败：\n\n" + "\n".join(lines) + more +
            "\n\n常见处理：视频地址过期/需登录 → 重新点“加载剧集表”并把浏览器 Cookie 填入设置页；"
            "网络被墙 → 开启代理软件或检查系统代理。完整日志见程序目录 下载日志.txt")

    def _watchdog(self):
        """每1秒检查：任务运行中且长时间无任何进展 → 弹窗提示（防“卡住”无感知）"""
        try:
            # 收集阶段：持续心跳刷新看门狗 + 更新"请稍候"为实时秒数，防误报
            if self._collecting:
                self._last_activity = time.time()
                if self._collect_t0 and self.table_info_var.get().startswith('正在扫描剧集列表'):
                    run = int(time.time() - self._collect_t0)
                    self.table_info_var.set(f"正在解析剧集列表，请稍候...（已运行 {run} 秒）")
            elif self.running and self.worker and self.worker.is_alive():
                idle = time.time() - self._last_activity
                if idle > 90 and not self._stall_warned:
                    self._stall_warned = True
                    self.append_status(
                        "⚠ 下载已 90 秒无进展（可能网络/防盗链问题）。完整日志已写入: "
                        + self._log_path)
                    messagebox.showwarning(
                        "下载无进展",
                        "下载已 90 秒无进展（进度/网速/日志均无变化）。\n\n"
                        "可能原因与处理：\n"
                        "1. 视频地址已过期或需登录 → 重新点“加载剧集表”，并把浏览器 Cookie 填入设置页后重试；\n"
                        "2. 网络不通 → 检查代理软件是否已开启，或到 Windows 设置关闭系统代理；\n"
                        "3. 若始终如此，请把程序目录下的 下载日志.txt 内容发我排查。")
                elif idle <= 90:
                    self._stall_warned = False
        except Exception:
            pass
        self.root.after(1000, self._watchdog)


def main():
    root = tk.Tk()
    VideoDownloaderGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
