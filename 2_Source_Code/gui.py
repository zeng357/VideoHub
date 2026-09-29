# 通用视频下载器 - GUI（仿漫画下载器界面：左侧导航 + 站点工具栏 + 剧集表勾选下载 + 双进度 + 网速）
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from utils import ensure_console_safe, app_base
ensure_console_safe()

from config import BROWSER_PATHS, DEFAULT_SITE, DEFAULT_COOKIES_DIR, detect_ffmpeg_path
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
        self._stop_ev = threading.Event()   # 收集过程停止标志（弹窗内停止爬取按钮/三选一停止）
        self._stop_requested = False         # 已点停止：开始按钮保持锁定，直到重新加载/清空
        self._range_win = None               # 选择爬取方式弹窗引用（爬取中常驻显示）
        self.ffmpeg_path = detect_ffmpeg_path()

        # 日志落盘：界面日志框被挤出屏幕时也能从 下载日志.txt 看到完整记录
        self._log_path = os.path.join(app_base(), '下载日志.txt')
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
        self.nav_password_btn = tk.Button(self.sidebar, text="🔒 密码管理", font=("微软雅黑", 11),
                                          relief="flat", bd=0, anchor="w", padx=14,
                                          bg=SIDEBAR_BG, fg=NAV_TEXT, activebackground=NAV_ACTIVE_BG,
                                          activeforeground=NAV_ACTIVE_TEXT,
                                          command=lambda: self._switch_page('password'))
        self.nav_password_btn.pack(fill=tk.X, padx=6, pady=2)
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
        ttk.Button(site_row, text="登录站点", width=9,
                   command=self.open_login_window).pack(side=tk.LEFT, padx=2)
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
        self.path_var = tk.StringVar(value=app_base())
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

        # ---------- 密码管理页面（侧边栏「密码管理」，独立一页） ----------
        self.page_password = ttk.Frame(self.main_frame)
        self.page_password.pack_forget()
        pw_lf1 = ttk.LabelFrame(self.page_password, text="登录状态（登录过的站点下次自动登录）",
                                style="Card.TLabelframe")
        pw_lf1.pack(fill=tk.X, pady=(4, 6))
        self.pw_status_text = tk.StringVar(value="检测中...")
        ttk.Label(pw_lf1, textvariable=self.pw_status_text, font=("微软雅黑", 9),
                  foreground="#334155").pack(anchor='w', padx=12, pady=8)
        pw_lf2 = ttk.LabelFrame(self.page_password, text="容器密码（仅登录数据加密，程序本身不锁）",
                                style="Card.TLabelframe")
        pw_lf2.pack(fill=tk.X, pady=6)
        if not hasattr(self, 'pw_state_text'):
            self.pw_state_text = tk.StringVar(value="检测中...")
        ttk.Label(pw_lf2, textvariable=self.pw_state_text, font=("微软雅黑", 9),
                  foreground="#334155").pack(anchor='w', padx=12, pady=8)
        pw_btns = ttk.Frame(self.page_password)
        pw_btns.pack(fill=tk.X, padx=6, pady=10)
        ttk.Button(pw_btns, text="设置密码", width=14,
                   command=self._set_container_password).pack(side=tk.LEFT, padx=6)
        ttk.Button(pw_btns, text="修改密码", width=14,
                   command=self._change_container_password).pack(side=tk.LEFT, padx=6)
        ttk.Button(pw_btns, text="清除密码", width=12,
                   command=self._clear_container_password).pack(side=tk.LEFT, padx=6)
        ttk.Button(pw_btns, text="重置容器", width=10,
                   command=self._reset_container_data).pack(side=tk.LEFT, padx=6)
        tk.Label(self.page_password,
                 text="说明：\n· 程序自身浏览器加载登录数据：无需密码（本机自动识别）；\n"
                      "· 其他方式访问密码库、或程序被复制到其他电脑：需密码验证，错误 4 次自动销毁；\n"
                      "· 修改/清除密码需先回答创建时设置的安全问题；\n"
                      "· 程序复制给他人时，对方点「重置容器」即可清除原密码，设置自己的密码。",
                 font=("微软雅黑", 8), foreground="#94a3b8", justify=tk.LEFT).pack(anchor='w', padx=12)
        self._refresh_password_page()

        self._switch_page('main')
        self._on_mode_change()

    def _switch_page(self, page):
        if page == 'main':
            self.page_settings.pack_forget()
            if hasattr(self, 'page_password'):
                self.page_password.pack_forget()
            self.page_main.pack(fill=tk.BOTH, expand=True)
            self._update_nav_highlight('main')
        elif page == 'password':
            self.page_main.pack_forget()
            self.page_settings.pack_forget()
            self.page_password.pack(fill=tk.BOTH, expand=True)
            self._update_nav_highlight('password')
            self._refresh_password_page()
        else:
            self.page_main.pack_forget()
            if hasattr(self, 'page_password'):
                self.page_password.pack_forget()
            self.page_settings.pack(fill=tk.BOTH, expand=True)
            self._update_nav_highlight('settings')

    def _update_nav_highlight(self, page):
        for btn, name, active in ((self.nav_main_btn, 'main', page == 'main'),
                                  (self.nav_settings_btn, 'settings', page == 'settings'),
                                  (self.nav_password_btn, 'password', page == 'password')):
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
        self.refresh_login_status()

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
        self.refresh_login_status()

    def open_sites_dir(self):
        data_dir = os.path.join(app_base(), 'sites_data')
        os.makedirs(data_dir, exist_ok=True)
        os.startfile(data_dir)

    # ==================== 站点登录小窗口 ====================
    def refresh_login_status(self):
        """刷新登录状态容器：列出各站点是否已保存登录（已保存=下次自动登录）"""
        try:
            base = os.path.join(app_base(), DEFAULT_COOKIES_DIR)
            logged = set()
            if os.path.isdir(base):
                for fn in os.listdir(base):
                    if fn.endswith('_cookie_str.txt'):
                        logged.add(fn.replace('_cookie_str.txt', ''))
                    elif fn.endswith('_cookies.json'):
                        logged.add(fn.replace('_cookies.json', ''))
            names = []
            try:
                names = get_all_site_names()
            except Exception:
                names = []
            parts = []
            for s in names:
                if s and s != '自动扫描':
                    parts.append(f"✓ {s}" if s in logged else f"· {s}")
            if parts:
                self.login_status_var.set(
                    "　".join(parts) + "　（✓=已登录，下次自动登录，无需重复操作）")
            else:
                self.login_status_var.set("暂无站点（可在「登录站点」完成一次登录后自动记住）")
            if hasattr(self, 'login_status_lbl'):
                self.login_status_lbl.configure(
                    foreground="#16a34a" if logged else "#64748b")
            # 容器密码状态：扫描任一 .enc 容器是否含密码层
            try:
                import cookie_guard as cg
                root = os.path.join(app_base(),
                                    DEFAULT_COOKIES_DIR)
                has_pw = False
                if os.path.isdir(root):
                    for fn in os.listdir(root):
                        if fn.endswith('.enc'):
                            try:
                                import json as _json
                                with open(os.path.join(root, fn),
                                          'r', encoding='utf-8') as _f:
                                    cj = _json.loads(_f.read())
                                if cj.get('has_pw'):
                                    has_pw = True
                                    break
                            except Exception:
                                pass
                if hasattr(self, 'container_pw_state'):
                    self.container_pw_state.set(
                        "已设置（外部电脑打开需密码）" if has_pw
                        else "未设置（仅本电脑可打开）")
            except Exception:
                pass
        except Exception:
            try:
                self.login_status_var.set("登录状态读取失败")
            except Exception:
                pass

    def _ask_password_dialog(self, title, prompt, confirm=False):
        """通用密码输入弹窗；confirm=True 时需两次输入一致。返回密码或 None（取消）"""
        import cookie_guard as cg
        result = {'pw': None}
        win = tk.Toplevel(self.root)
        win.title(title)
        win.geometry("380x240" if confirm else "380x200")
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()
        tk.Label(win, text=prompt, font=("微软雅黑", 9), wraplength=340,
                 justify=tk.LEFT).pack(padx=14, pady=(12, 6))
        e1 = ttk.Entry(win, show="*", font=("微软雅黑", 11))
        e1.pack(fill=tk.X, padx=14, pady=4)
        e2 = None
        if confirm:
            tk.Label(win, text="再次输入确认", font=("微软雅黑", 9)).pack(anchor='w', padx=14)
            e2 = ttk.Entry(win, show="*", font=("微软雅黑", 11))
            e2.pack(fill=tk.X, padx=14, pady=4)
        err = tk.StringVar()
        tk.Label(win, textvariable=err, fg="#dc2626",
                 font=("微软雅黑", 8)).pack(anchor='w', padx=14)
        def _ok():
            pw1 = e1.get()
            if not pw1:
                err.set("密码不能为空")
                return
            if confirm:
                if pw1 != e2.get():
                    err.set("两次输入不一致，请重新输入")
                    return
                # 强密码校验：必须同时包含 数字 + 符号 + 字母
                has_digit = any(c.isdigit() for c in pw1)
                has_alpha = any(c.isalpha() for c in pw1)
                has_symbol = any(not c.isalnum() for c in pw1)
                if not (has_digit and has_alpha and has_symbol):
                    err.set("密码必须同时包含 数字+符号+字母（如 Abc@123）")
                    return
                if len(pw1) < 6:
                    err.set("密码长度至少 6 位")
                    return
            result['pw'] = pw1
            win.destroy()
        def _cancel():
            win.destroy()
        btns = ttk.Frame(win)
        btns.pack(pady=(4, 10))
        ttk.Button(btns, text="确定", width=10, command=_ok).pack(side=tk.LEFT, padx=8)
        ttk.Button(btns, text="取消", width=8, command=_cancel).pack(side=tk.LEFT, padx=8)
        e1.bind("<Return>", lambda ev: _ok())
        win.protocol("WM_DELETE_WINDOW", _cancel)
        win.after(100, e1.focus_set)
        self.wait_window(win)
        return result['pw']

    def _refresh_password_page(self):
        """刷新密码管理页：登录状态 + 容器密码状态"""
        try:
            import cookie_guard as cg
            base = app_base()
            logged = set()
            root = os.path.join(base, DEFAULT_COOKIES_DIR)
            if os.path.isdir(root):
                for fn in os.listdir(root):
                    for suf in ('_cookie_str.txt.enc', '_cookies.json.enc'):
                        if fn.endswith(suf):
                            logged.add(fn.replace(suf, ''))
                            break
            names = []
            try:
                names = get_all_site_names()
            except Exception:
                names = []
            parts = [f"✓ {s}" if s in logged else f"· {s}"
                     for s in names if s and s != '自动扫描']
            if hasattr(self, 'pw_status_text'):
                self.pw_status_text.set("　".join(parts) if parts else "暂无站点")
            has_pw = False
            if os.path.isdir(root):
                for fn in os.listdir(root):
                    if fn.endswith('.enc'):
                        try:
                            import json as _json
                            with open(os.path.join(root, fn), 'r', encoding='utf-8') as _f:
                                cj = _json.loads(_f.read())
                            if cj.get('has_pw'):
                                has_pw = True
                                break
                        except Exception:
                            pass
            n_q = len(cg.load_security_questions(base))
            pw_state = "已设置（外部电脑打开需密码）" if has_pw else "未设置（仅本电脑可打开）"
            if hasattr(self, 'pw_state_text'):
                self.pw_state_text.set(f"密码状态：{pw_state}　·　安全问题：{n_q} 个")
        except Exception:
            pass

    def _set_container_password(self):
        """设置容器密码：密码(≥6位，数字+符号+字母) + 自定义安全问题（问题/答案不限长度）"""
        import cookie_guard as cg
        base = app_base()
        # 已设置过 → 自动进入修改密码流程（需回答安全问题）
        if cg.has_security_questions(base):
            try:
                messagebox.showinfo("提示", "已设置过密码，正在进入「修改密码」（需先回答安全问题）")
            except Exception:
                pass
            self._change_container_password()
            return
        result = {'pw': None, 'qs': []}
        win = tk.Toplevel(self.root)
        win.title("设置容器密码")
        win.geometry("460x420")
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()
        win.lift()
        win.attributes('-topmost', True)
        tk.Label(win, text="密码（至少6位，必须同时包含 数字+符号+字母）：",
                 font=("微软雅黑", 9)).pack(anchor='w', padx=14, pady=(12, 2))
        e1 = ttk.Entry(win, show="*", font=("微软雅黑", 11))
        e1.pack(fill=tk.X, padx=14, pady=3)
        tk.Label(win, text="再次输入确认：", font=("微软雅黑", 9)).pack(anchor='w', padx=14)
        e2 = ttk.Entry(win, show="*", font=("微软雅黑", 11))
        e2.pack(fill=tk.X, padx=14, pady=3)

        tk.Label(win, text="安全问题（1~3个，可自定义，问题/答案不限长度）：",
                 font=("微软雅黑", 9)).pack(anchor='w', padx=14, pady=(10, 2))
        q_entries = []
        for i in range(3):
            row = ttk.Frame(win)
            row.pack(fill=tk.X, padx=14, pady=2)
            qe = ttk.Entry(row, font=("微软雅黑", 9))
            qe.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
            ae = ttk.Entry(row, font=("微软雅黑", 9))
            ae.pack(side=tk.LEFT, width=14)
            if i == 0:
                qe.insert(0, "问题1（如：我的爱好是？）")
            q_entries.append((qe, ae))
        err = tk.StringVar()
        tk.Label(win, textvariable=err, fg="#dc2626",
                 font=("微软雅黑", 8)).pack(anchor='w', padx=14)

        def _ok():
            pw1 = e1.get()
            if not pw1:
                err.set("密码不能为空")
                return
            if len(pw1) < 6:
                err.set("密码长度至少 6 位")
                return
            has_d = any(c.isdigit() for c in pw1)
            has_a = any(c.isalpha() for c in pw1)
            has_s = any(not c.isalnum() for c in pw1)
            if not (has_d and has_a and has_s):
                err.set("密码必须同时包含 数字+符号+字母（如 Abc@123）")
                return
            if pw1 != e2.get():
                err.set("两次输入不一致")
                return
            qs = []
            for qe, ae in q_entries:
                q = qe.get().strip()
                a = ae.get().strip()
                if q or a:
                    if not q or not a:
                        err.set("安全问题与答案需成对填写（或都留空）")
                        return
                    qs.append((q, a))
            if not qs:
                err.set("请至少设置 1 个安全问题（改密码时需回答）")
                return
            result['pw'] = pw1
            result['qs'] = qs
            win.destroy()

        def _cancel():
            win.destroy()
        btns = ttk.Frame(win)
        btns.pack(pady=8)
        ttk.Button(btns, text="保存", width=10, command=_ok).pack(side=tk.LEFT, padx=8)
        ttk.Button(btns, text="取消", width=8, command=_cancel).pack(side=tk.LEFT, padx=8)
        win.protocol("WM_DELETE_WINDOW", _cancel)
        self.wait_window(win)
        if not result['pw']:
            return
        pw, qs = result['pw'], result['qs']
        try:
            cg.set_password_cache(pw)
            cg.save_security_questions(qs, base_dir=base)
            ok, fail = cg.set_container_password(base, pw)
            try:
                messagebox.showinfo(
                    "设置成功",
                    f"容器密码与安全问题已保存（更新 {ok} 个容器）。\n"
                    "本机照常自动登录；外部电脑打开时需输入密码。\n"
                    "修改/清除密码需回答安全问题，请牢记问题和答案。")
            except Exception:
                pass
        except Exception as e:
            try:
                messagebox.showwarning("设置失败", f"保存容器密码失败: {e}")
            except Exception:
                pass
        try:
            self._refresh_password_page()
        except Exception:
            pass

    def _ask_security_questions(self, base, title):
        """弹窗逐题回答安全问题；全部答对返回 True，否则 False"""
        import cookie_guard as cg
        qs = cg.load_security_questions(base)
        if not qs:
            return False
        result = {'ok': False}
        win = tk.Toplevel(self.root)
        win.title(title)
        win.geometry("420x260")
        win.resizable(False, False)
        win.transient(self.root)
        tk.Label(win, text="请回答创建密码时设置的安全问题：",
                 font=("微软雅黑", 9)).pack(anchor='w', padx=14, pady=(12, 4))
        entries = {}
        for q in qs:
            tk.Label(win, text=f"Q: {q}", font=("微软雅黑", 9)).pack(
                anchor='w', padx=14, pady=(4, 0))
            e = ttk.Entry(win, font=("微软雅黑", 10))
            e.pack(fill=tk.X, padx=14, pady=2)
            entries[q] = e
        err = tk.StringVar()
        tk.Label(win, textvariable=err, fg="#dc2626",
                 font=("微软雅黑", 8)).pack(anchor='w', padx=14)

        def _ok():
            answers = {}
            for q, e in entries.items():
                answers[q] = e.get()
            if cg.verify_security_answers(answers, base_dir=base):
                result['ok'] = True
                win.destroy()
            else:
                err.set("答案不正确，请重试")

        def _cancel():
            win.destroy()
        btns = ttk.Frame(win)
        btns.pack(pady=8)
        ttk.Button(btns, text="验证", width=10, command=_ok).pack(side=tk.LEFT, padx=8)
        ttk.Button(btns, text="取消", width=8, command=_cancel).pack(side=tk.LEFT, padx=8)
        win.protocol("WM_DELETE_WINDOW", _cancel)
        self.wait_window(win)
        return result['ok']

    def _change_container_password(self):
        """修改容器密码：先回答安全问题，答对后才能修改"""
        import cookie_guard as cg
        base = app_base()
        if not cg.has_security_questions(base):
            try:
                messagebox.showinfo("提示", "尚未设置密码/安全问题，请先「设置密码」")
            except Exception:
                pass
            return
        if not self._ask_security_questions(base, "安全验证"):
            return
        pw = self._ask_password_dialog(
            "修改容器密码",
            "安全验证通过。请输入新密码（至少6位，必须包含 数字+符号+字母）：\n"
            "修改后：外部电脑打开需新密码；错误 4 次仍会销毁数据。",
            confirm=True)
        if pw is None:
            return
        try:
            cg.set_password_cache(pw)
            ok, fail = cg.set_container_password(base, pw)
            try:
                messagebox.showinfo("修改成功",
                                    f"容器密码已更新（更新 {ok} 个容器"
                                    + (f"，{fail} 个失败" if fail else "") + "）。")
            except Exception:
                pass
        except Exception as e:
            try:
                messagebox.showwarning("修改失败", f"修改容器密码失败: {e}")
            except Exception:
                pass
        try:
            self._refresh_password_page()
        except Exception:
            pass

    def _clear_container_password(self):
        """清除容器密码：需先回答安全问题；清除后仅本机可打开"""
        import cookie_guard as cg
        base = app_base()
        if not cg.has_security_questions(base):
            try:
                messagebox.showinfo("提示", "尚未设置密码，无需清除")
            except Exception:
                pass
            return
        if not self._ask_security_questions(base, "安全验证"):
            return
        try:
            if messagebox.askyesno("清除容器密码",
                                   "安全验证通过。确定清除容器密码？\n"
                                   "清除后仅本电脑可打开容器，外部电脑将无法打开。"):
                cg.set_password_cache(None)
                ok, fail = cg.set_container_password(base, None)
                messagebox.showinfo("已清除", f"已清除容器密码（更新 {ok} 个容器）")
                try:
                    self._refresh_password_page()
                except Exception:
                    pass
        except Exception as e:
            try:
                messagebox.showwarning("操作失败", f"清除密码失败: {e}")
            except Exception:
                pass

    def _reset_container_data(self):
        """重置容器：清除全部密码与登录数据（复制给他人时对方重新设置）"""
        import cookie_guard as cg
        base = app_base()
        try:
            if not messagebox.askyesno(
                    "重置容器",
                    "将清除容器内全部密码与登录数据（包括所有站点的登录状态）。\n"
                    "重置后可以重新设置你自己的密码。\n\n确定重置吗？"):
                return
            if cg.reset_all(base):
                cg.clear_password_cache()
                try:
                    self._refresh_password_page()
                except Exception:
                    pass
                messagebox.showinfo(
                    "已重置",
                    "容器已重置，回到全新状态。\n"
                    "请点「设置密码」设置你自己的密码，再重新登录需要的站点。")
        except Exception as e:
            try:
                messagebox.showwarning("重置失败", f"重置容器失败: {e}")
            except Exception:
                pass

    def _handle_container_unlock(self, st, crawler):
        """处理加密容器解锁：外部机器需密码；密码错 4 次销毁"""
        import cookie_guard as cg
        base = app_base()
        if st == 'destroyed':
            self.refresh_login_status()
            try:
                messagebox.showwarning(
                    "容器已销毁",
                    "容器密码错误累计超过 4 次，容器内全部登录数据已自动销毁。\n"
                    "请在「登录站点」中重新登录需要的站点。")
            except Exception:
                pass
            return
        if st in ('need_password', 'wrong_password', 'need_verify'):
            # 程序被改动且未设密码容器：需本人确认（无密码可验证）
            if st == 'need_verify' and not cg.container_has_password(base):
                try:
                    mine = messagebox.askyesno(
                        "程序完整性检测",
                        "检测到程序文件被改动。\n\n"
                        "· 如果这是你本人更新/修改的程序，请点「是」，确认后继续；\n"
                        "· 否则请点「否」，拒绝访问密码库。")
                    if mine:
                        cg.confirm_program_change(base)
                        s = crawler.load_cookie_str()
                        if s:
                            crawler.cookie_str = s
                            try:
                                crawler.set_cookie()
                            except Exception as e:
                                print(f"确认后注入Cookie失败: {e}")
                            self.msg_queue.put((self.refresh_login_status, ()))
                            self.msg_queue.put((self._refresh_password_page, ()))
                            self.msg_queue.put((self.append_status, "✓ 已确认程序改动，恢复访问"))
                            return
                    try:
                        messagebox.showwarning(
                            "拒绝访问",
                            "程序被改动且未获本人确认，已拒绝访问密码库。\n"
                            "如是你本人操作，请重新打开程序后再试。")
                    except Exception:
                        pass
                except Exception:
                    pass
                return
            # 密码验证（need_password / need_verify / wrong_password）
            if st == 'need_verify':
                tip = "检测到程序文件被改动，需要重新输入容器密码验证。\n"
            else:
                tip = "访问保存的登录密码需要验证，请输入容器密码。\n"
            n = cg.read_attempts(base)
            left = max(0, cg.MAX_ATTEMPTS - n)
            pw = self._ask_password_dialog(
                "验证密码库访问",
                tip
                + "· 浏览器和正常下载不需要密码；\n"
                + f"· 密码错误 {cg.MAX_ATTEMPTS} 次将自动销毁容器内全部数据。"
                + (f"\n（当前已错 {n} 次，剩余 {left} 次）" if n > 0 else ""))
            if pw is None:
                try:
                    messagebox.showinfo(
                        "未解锁",
                        "未解锁密码库。\n"
                        "若是别人复制给你的程序，可在「密码管理」页点「重置容器」，"
                        "清除原数据后重新设置你自己的密码。")
                except Exception:
                    pass
                return
            cg.set_password_cache(pw)
            s = crawler.load_cookie_str()
            if s:
                crawler.cookie_str = s
                try:
                    crawler.set_cookie()
                except Exception as e:
                    print(f"解锁后注入Cookie失败: {e}")
                self.msg_queue.put((self.refresh_login_status, ()))
                self.msg_queue.put((self._refresh_password_page, ()))
                self.msg_queue.put((self.append_status, "✓ 容器解锁成功，已恢复自动登录"))
            else:
                st2 = getattr(crawler, '_container_unlock', None)
                if st2 == 'destroyed':
                    self.refresh_login_status()
                    try:
                        messagebox.showwarning(
                            "容器已销毁",
                            "密码错误累计超过 4 次，容器内全部登录数据已自动销毁。")
                    except Exception:
                        pass
                elif st2 == 'wrong_password':
                    n2 = cg.read_attempts(base)
                    left2 = max(0, cg.MAX_ATTEMPTS - n2)
                    try:
                        messagebox.showwarning(
                            "密码错误",
                            f"密码不正确（剩余 {left2} 次机会，超过将自动销毁全部数据）。")
                    except Exception:
                        pass
                    self.msg_queue.put((self._handle_container_unlock, ('wrong_password', crawler)))
                elif st2 == 'no_password_configured':
                    try:
                        messagebox.showinfo(
                            "无法解锁",
                            "该容器未设置密码，仅原电脑可打开。\n"
                            "如需在外部电脑使用，请先在原电脑上「设置/修改密码」。")
                    except Exception:
                        pass
        elif st == 'no_password_configured':
            try:
                messagebox.showinfo(
                    "无法打开",
                    "该容器未设置密码，仅本电脑可打开。\n"
                    "如需外部电脑打开，请先在原电脑上设置容器密码。")
            except Exception:
                pass

    def open_login_window(self):
        """弹出站点登录小窗口：打开登录页 → 用户在浏览器完成登录 → 点“登录完成”保存"""
        site = self._real_site()
        if not site:
            messagebox.showwarning("提示", "请先在顶部选择要登录的站点")
            return

        # 已登录状态初判（文本cookie 或 登录窗口保存的 json cookie 任一存在）
        base = os.path.join(app_base(), DEFAULT_COOKIES_DIR)
        txt_path = os.path.join(base, f"{site}_cookie_str.txt")
        js_path = os.path.join(base, f"{site}_cookies.json")
        logged = os.path.exists(txt_path) or os.path.exists(js_path)

        win = tk.Toplevel(self.root)
        win.title(f"登录 - {site}")
        win.geometry("520x360")
        win.resizable(False, False)
        try:
            win.attributes('-topmost', True)
        except Exception:
            pass

        state_var = tk.StringVar(
            value="已登录（将使用线路1）" if logged else "未登录（将从线路2开始寻找可用线路）")

        tk.Label(win, text=f"站点：{site}", font=("微软雅黑", 12, "bold")).pack(anchor='w', padx=16, pady=(14, 4))
        tk.Label(win, textvariable=state_var, fg=("#16a34a" if logged else "#b45309"),
                 font=("微软雅黑", 10, "bold")).pack(anchor='w', padx=16, pady=(0, 6))

        tips = tk.Label(win, justify='left', font=("微软雅黑", 9), anchor='w',
                        text="操作步骤：\n"
                             "1. 点击【打开登录页】，程序会弹出浏览器并打开该站点首页\n"
                             "2. 在浏览器中完成登录（扫码 / 账号密码），确认已进入个人中心\n"
                             "3. 回到本窗口点击【登录完成】，登录状态即保存到本地\n\n"
                             "线路规则：\n"
                             "· 已登录：从线路1开始向后寻找可用线路（VIP线路）\n"
                             "· 未登录：线路1为VIP专用，从线路2开始向后寻找可用线路")
        tips.pack(anchor='w', padx=16, pady=(0, 10))

        btns = ttk.Frame(win)
        btns.pack(fill=tk.X, padx=16, pady=8)
        self._login_crawler = None

        def _set_state(txt, color="#0f172a"):
            state_var.set(txt)
            for lbl in win.winfo_children():
                if isinstance(lbl, tk.Label) and lbl['textvariable'] == state_var:
                    lbl.configure(fg=color)

        def _open_login_page():
            def work():
                try:
                    c = VideoCrawler(site, BROWSER_PATHS.get(self.browser_var.get(), BROWSER_PATHS['edge']),
                                     False, None)  # 登录必须显示浏览器窗口
                    self._login_crawler = c
                    c.open_login_page()
                    self.msg_queue.put((_set_state, ("浏览器已打开，请完成登录后点击【登录完成】", "#2563eb")))
                except Exception as e:
                    self.msg_queue.put((_set_state, (f"打开登录页失败: {e}", "#dc2626")))
            threading.Thread(target=work, daemon=True).start()
            _set_state("正在打开浏览器...", "#2563eb")

        def _login_done():
            c = self._login_crawler
            if c is None:
                _set_state("请先点击【打开登录页】", "#dc2626")
                return
            def work():
                try:
                    c.complete_login()
                    self.msg_queue.put((_set_state, ("登录成功，Cookie 已保存（将使用线路1）", "#16a34a")))
                    self.msg_queue.put((self.append_status, f"[登录] {site} 登录成功，Cookie 已保存"))
                    self.msg_queue.put((self.refresh_login_status, ()))
                except Exception as e:
                    self.msg_queue.put((_set_state, (f"保存登录状态失败: {e}", "#dc2626")))
            threading.Thread(target=work, daemon=True).start()

        def _on_close():
            c = self._login_crawler
            if c is not None and getattr(c, 'page', None) is not None:
                try:
                    c.page.close()
                except Exception:
                    pass
            win.destroy()
            self.refresh_login_status()

        ttk.Button(btns, text="打开登录页", width=12, command=_open_login_page).pack(side=tk.LEFT, padx=3)
        ttk.Button(btns, text="登录完成", width=12, command=_login_done).pack(side=tk.LEFT, padx=3)
        ttk.Button(btns, text="关闭", width=8, command=_on_close).pack(side=tk.RIGHT, padx=3)
        win.protocol("WM_DELETE_WINDOW", _on_close)
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
        self._stop_requested = False
        self.start_btn.configure(state='normal')
        self.load_table_btn.configure(state='normal')
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
            messagebox.showinfo("提示", "上一个任务仍在进行中（按钮已锁定）。\n\n若确认没有任务在跑，请重启程序后再试。")
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
        self._stop_ev.clear()
        self._stop_requested = False
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
                crawler.choose_candidate = lambda cands: self._choose_candidate_threadsafe(cands)
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
                crawler.choose_candidate = lambda cands: self._choose_candidate_threadsafe(cands)
                crawler.stop_collect = lambda: self._stop_ev.is_set()
                _ul = getattr(crawler, '_container_unlock', None)
                if _ul:
                    post(self._handle_container_unlock, _ul, crawler)
                self._collecting = True
                self._collect_t0 = time.time()
                try:
                    if mode == '1' and not addr and site and kw:
                        # 已选站点 + 只填剧名 → 直接用站点站内搜索（无需地址）
                        log(f"使用站点「{site}」直接搜索: {kw}")
                        result = run_site_download(crawler, kw, episode_start=1,
                                                   episode_end=0, download_path=None,
                                                   log=log, url_progress_callback=url_bump,
                                                   collect_only=True,
                                                   range_selector=self._range_selector_threadsafe)
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
                                                   collect_only=True,
                                                   range_selector=self._range_selector_threadsafe)
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
        # 关闭可能还开着的「选择爬取方式」弹窗
        if self._range_win is not None:
            try:
                self._range_win.destroy()
            except Exception:
                pass
            self._range_win = None
        # 无论是否停止，开始按钮都恢复：已收集的集数可直接勾选下载
        self.start_btn.configure(state='normal')
        if self._stop_requested:
            self._stop_requested = False
            self.table_info_var.set("已停止爬取（保留已收集的集数）。可勾选后点「开始下载」下载已收集部分，或重新「加载剧集表」继续爬")
            self.append_status("已停止：保留已收集的集数，可直接勾选后点「开始下载」")
        if err:
            self.table_info_var.set(err)

    # ============ 超过50集：停止/选择范围/全部 三选一 ============
    def stop_collect_click(self):
        """停止爬取（由「选择爬取方式」弹窗内按钮调用）：设置停止标志，
        收集线程保留已完成的集数；停止后开始按钮保持锁定，直到重新加载/清空"""
        self._stop_ev.set()
        self._stop_requested = True
        self.append_status("已请求停止爬取：保留已完成的集数；开始下载按钮保持锁定，可重新「加载剧集表」或「清空状态」后再次使用")

    def _range_selector_threadsafe(self, total):
        """后台线程调用：投递主线程弹三选一（停止/选择范围/全部）；返回 (start,end) 或 None=停止"""
        ev = threading.Event()
        box = {}
        def show():
            try:
                self._range_select_window(total, ev, box)
            except Exception:
                ev.set()
        self.msg_queue.put((show, ()))
        ev.wait(timeout=65)
        c = box.get('choice')
        if c == 'range':
            return (box.get('start', 1), box.get('end', total))
        if c == 'all':
            return (1, total)
        return None  # stop / 超时未操作

    def _range_select_window(self, total, ev, box):
        """主线程：共N集（>50）选择窗口。
        先选择方式（全部/选择范围/停止），再点「开始爬取」执行；
        开始后窗口不关闭，切换为「爬取中 + 停止爬取」状态，随时可点停止（保留已完成）"""
        try:
            win = tk.Toplevel(self.root)
            self._range_win = win
            win.title("选择爬取方式")
            win.geometry("520x330")
            win.attributes('-topmost', True)
            win.transient(self.root)
            try:
                win.grab_set()
            except Exception:
                pass

            title = tk.Label(win, text="共 %d 集（超过50集），请先选择爬取方式：" % total,
                             font=('Microsoft YaHei UI', 11))
            title.pack(pady=12)
            hint = tk.Label(win, text="选择方式后，点下方「开始爬取」按钮执行；全部爬取耗时较长",
                            font=('Microsoft YaHei UI', 9), foreground="gray")
            hint.pack()

            # ---- 方式选择区（grid 布局，便于整体隐藏） ----
            cfg_row = tk.Frame(win)
            cfg_row.pack(pady=10)
            var = tk.StringVar(value='all')
            tk.Radiobutton(cfg_row, text="全部爬取（%d集）" % total, variable=var, value='all',
                           font=('Microsoft YaHei UI', 10)).grid(row=0, column=0, columnspan=5, sticky='w', padx=30, pady=3)
            tk.Radiobutton(cfg_row, text="选择范围爬取", variable=var, value='range',
                           font=('Microsoft YaHei UI', 10)).grid(row=1, column=0, sticky='w', padx=30)
            tk.Label(cfg_row, text="起始:", font=('Microsoft YaHei UI', 10)).grid(row=1, column=1, padx=2)
            sp_start = tk.Spinbox(cfg_row, from_=1, to=total, width=5,
                                  font=('Microsoft YaHei UI', 10))
            sp_start.delete(0, 'end'); sp_start.insert(0, '1')
            sp_start.grid(row=1, column=2, padx=2)
            tk.Label(cfg_row, text="结束:", font=('Microsoft YaHei UI', 10)).grid(row=1, column=3, padx=2)
            sp_end = tk.Spinbox(cfg_row, from_=1, to=total, width=5,
                                font=('Microsoft YaHei UI', 10))
            sp_end.delete(0, 'end'); sp_end.insert(0, str(total))
            sp_end.grid(row=1, column=4, padx=2)
            tk.Radiobutton(cfg_row, text="停止（不爬取）", variable=var, value='stop',
                           font=('Microsoft YaHei UI', 10)).grid(row=2, column=0, columnspan=5, sticky='w', padx=30, pady=3)

            # ---- 爬取中状态（初始隐藏） ----
            status_var = tk.StringVar(value="")
            status_lbl = tk.Label(win, textvariable=status_var,
                                  font=('Microsoft YaHei UI', 10), foreground="#1a66ff")
            stop_btn = tk.Button(win, text="停止爬取（保留已完成）", width=22,
                                 command=self.stop_collect_click, bg="#fdecea", fg="#c0392b")

            timer = [None]
            def switch_collecting(msg):
                if timer[0] is not None:
                    try:
                        win.after_cancel(timer[0])
                    except Exception:
                        pass
                    timer[0] = None
                for w in (title, hint, cfg_row, btns):
                    try:
                        w.pack_forget()
                    except Exception:
                        pass
                status_var.set(msg)
                status_lbl.pack(pady=16)
                stop_btn.pack(pady=8)

            def do_stop():
                box['choice'] = 'stop'; ev.set()
                try:
                    win.destroy()
                except Exception:
                    pass
            def do_start():
                v = var.get()
                if v == 'stop':
                    do_stop()
                    return
                if v == 'range':
                    try:
                        s = int(sp_start.get()); e = int(sp_end.get())
                        if s < 1: s = 1
                        if e > total: e = total
                        if e < s: e = s
                    except ValueError:
                        s, e = 1, total
                    box['choice'] = 'range'; box['start'] = s; box['end'] = e
                    ev.set()
                    switch_collecting("正在爬取第 %d-%d 集，请稍候（点下方「停止爬取」可提前结束，保留已完成）..." % (s, e))
                else:
                    box['choice'] = 'all'; ev.set()
                    switch_collecting("正在爬取全部 %d 集，请稍候（点下方「停止爬取」可提前结束，保留已完成）..." % total)

            btns = tk.Frame(win)
            btns.pack(pady=14)
            tk.Button(btns, text="开始爬取", width=14, command=do_start,
                      bg="#e8f0fe", fg="#1a66ff", font=('Microsoft YaHei UI', 10, 'bold')).pack(side=tk.LEFT, padx=8)
            tk.Button(btns, text="取消", width=10, command=do_stop).pack(side=tk.LEFT, padx=8)

            win.protocol("WM_DELETE_WINDOW", do_stop)
            timer[0] = win.after(60000, do_stop)  # 60秒未操作默认停止
        except Exception:
            box['choice'] = 'stop'
            ev.set()


    def _choose_candidate_threadsafe(self, candidates):
        """后台线程调用：投递到主线程弹候选选择窗；返回用户选中URL；未选/超时返回None（取第一个）"""
        ev = threading.Event()
        box = {}
        def show():
            try:
                self._candidate_window(candidates, ev, box)
            except Exception:
                ev.set()
        self.msg_queue.put((show, ()))
        ev.wait(timeout=60)
        return box.get('url')

    def _candidate_window(self, candidates, ev, box):
        """主线程：选择窗口（抢焦点可见，但不阻塞日志/进度刷新），列出全部篇章/版本供挑选"""
        try:
            win = tk.Toplevel(self.root)
            win.title("选择要下载的篇章")
            win.geometry("600x460")
            win.attributes('-topmost', True)
            win.transient(self.root)
            try:
                win.grab_set()
            except Exception:
                pass
            tk.Label(win, text="扫描到 %d 个篇章/版本，请选择要下载的一部（双击列表项或点确定）：" % len(candidates),
                     font=('Microsoft YaHei UI', 10)).pack(pady=8)
            frame = tk.Frame(win)
            frame.pack(fill='both', expand=True, padx=10)
            lb = tk.Listbox(frame, font=('Microsoft YaHei UI', 10), selectmode='single')
            sb = tk.Scrollbar(frame, orient='vertical', command=lb.yview)
            lb.configure(yscrollcommand=sb.set)
            lb.pack(side='left', fill='both', expand=True)
            sb.pack(side='right', fill='y')
            for i, (name, url) in enumerate(candidates):
                lb.insert(i, name or '（未命名）')
            lb.selection_set(0)
            lb.activate(0)

            def on_ok():
                sel = lb.curselection()
                if sel:
                    box['url'] = candidates[sel[0]][1]
                ev.set()
                win.destroy()

            def on_cancel():
                ev.set()
                win.destroy()

            btns = tk.Frame(win)
            btns.pack(pady=8)
            tk.Button(btns, text="确定下载这一部", width=16, command=on_ok).pack(side='left', padx=6)
            tk.Button(btns, text="跳过（取第一部）", width=16, command=on_cancel).pack(side='left', padx=6)
            lb.bind('<Double-Button-1>', lambda e: on_ok())
            win.protocol("WM_DELETE_WINDOW", on_cancel)
            win.after(45000, on_cancel)  # 45秒未操作自动跳过，后台任务不卡死
        except Exception:
            ev.set()

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
            messagebox.showinfo("提示", "上一个任务仍在进行中（按钮已锁定）。\n\n若确认没有任务在跑，请重启程序后再试。")
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
            _ul = getattr(crawler, '_container_unlock', None)
            if _ul:
                post(self._handle_container_unlock, _ul, crawler)
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
                    _ul = getattr(crawler, '_container_unlock', None)
                    if _ul:
                        post(self._handle_container_unlock, _ul, crawler)
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
                    _ul = getattr(crawler, '_container_unlock', None)
                    if _ul:
                        post(self._handle_container_unlock, _ul, crawler)
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
                    _ul = getattr(crawler, '_container_unlock', None)
                    if _ul:
                        post(self._handle_container_unlock, _ul, crawler)
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
