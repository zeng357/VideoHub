# 通用视频下载器 - yt-dlp 通用下载页（单开一页）
# 功能：粘贴任意视频链接 -> yt-dlp 解析（标题/时长/清晰度）-> 选择格式下载（自动合并 mp4）
# 依赖：程序同级目录下的 yt-dlp.exe 与 ffmpeg.exe（外置，不打包进主程序）
import os
import re
import json
import queue
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

from utils import ensure_console_safe, app_base
ensure_console_safe()

ACCENT = "#3b82f6"


class YtdlpPage:
    """yt-dlp 通用下载页组件：解析 -> 选格式 -> 下载（含进度）"""

    def __init__(self, parent, gui=None):
        self.parent = parent
        self.gui = gui  # 主窗口引用（用于 log / 切页等）
        self._parse_thread = None
        self._dl_thread = None
        self._dl_proc = None
        self._stop_flag = False
        self._auto_extract = True   # 解析失败后自动用内置浏览器提取真实地址（快手等）
        self._formats = []        # [{id,height,ext,size,note,fsize}...]
        self.ytdlp_path = self._find_ytdlp()
        self.ffmpeg_path = self._find_ffmpeg()
        self._build_ui()

    # ---------------- 工具查找 ----------------
    def _tool_candidates(self, name):
        base = app_base()
        return [
            os.path.join(base, name),
            os.path.join(base, '_internal', name),  # 程序核心目录（onedir）
            os.path.join(os.path.dirname(os.path.abspath(__file__)), name),
            os.path.join(os.getcwd(), name),
        ]

    def _find_ytdlp(self):
        for p in self._tool_candidates('yt-dlp.exe'):
            if os.path.exists(p):
                return p
        return 'yt-dlp.exe'  # 兜底：PATH

    def _find_ffmpeg(self):
        for p in self._tool_candidates('ffmpeg.exe'):
            if os.path.exists(p):
                return p
        return ''

    # ---------------- UI ----------------
    def _build_ui(self):
        # 顶部状态条
        top = ttk.Frame(self.parent)
        top.pack(fill=tk.X, pady=(0, 4))
        yt_ok = os.path.exists(self.ytdlp_path) if self.ytdlp_path != 'yt-dlp.exe' else True
        ff_ok = os.path.exists(self.ffmpeg_path) if self.ffmpeg_path else False
        self.tool_state = tk.StringVar(
            value=("引擎就绪：yt-dlp ✓  ffmpeg ✓  （支持 1000+ 网站，自动合并 mp4）"
                   if yt_ok and ff_ok else
                   "缺少引擎：请在程序目录放置 yt-dlp.exe 与 ffmpeg.exe（可从发行包 tools/ 获取）"))
        ttk.Label(top, textvariable=self.tool_state, font=("微软雅黑", 9),
                  foreground="#16a34a" if (yt_ok and ff_ok) else "#dc2626").pack(anchor='w')

        # 链接输入
        url_lf = ttk.LabelFrame(self.parent, text="视频链接（支持任意网站：粘贴后点解析）",
                                style="Card.TLabelframe")
        url_lf.pack(fill=tk.X, pady=2)
        row = ttk.Frame(url_lf)
        row.pack(fill=tk.X, pady=3)
        self.url_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.url_var, font=("微软雅黑", 10)).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 4))
        ttk.Button(row, text="粘贴", width=6, command=self._paste_url).pack(side=tk.LEFT, padx=2)
        self.parse_btn = ttk.Button(row, text="解析视频", width=10,
                                    style="Accent.TButton", command=self.parse)
        self.parse_btn.pack(side=tk.LEFT, padx=2)

        # 解析结果信息
        info_lf = ttk.LabelFrame(self.parent, text="视频信息", style="Card.TLabelframe")
        info_lf.pack(fill=tk.X, pady=2)
        self.info_var = tk.StringVar(value="未解析。粘贴链接 → 点「解析视频」")
        ttk.Label(info_lf, textvariable=self.info_var, font=("微软雅黑", 9),
                  foreground="#334155", wraplength=820, justify=tk.LEFT).pack(
            anchor='w', padx=10, pady=6)

        # 视频设置（保存位置，与主界面一致）
        path_frame = ttk.LabelFrame(self.parent, text="视频设置", style="Card.TLabelframe")
        path_frame.pack(fill=tk.X, pady=2)
        path_row = ttk.Frame(path_frame)
        path_row.pack(fill=tk.X, pady=2)
        ttk.Label(path_row, text="下载路径:", width=9).pack(side=tk.LEFT)
        self.path_var = tk.StringVar(value=os.path.join(app_base(), 'downloads'))
        ttk.Entry(path_row, textvariable=self.path_var, font=("微软雅黑", 10)).pack(
            side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(path_row, text="浏览", width=6,
                   command=self._browse_path).pack(side=tk.LEFT, padx=4)

        # 清晰度选择 + 下载
        dl_lf = ttk.LabelFrame(self.parent, text="下载设置（先选清晰度，再点下载）",
                               style="Card.TLabelframe")
        dl_lf.pack(fill=tk.X, pady=2)
        dr = ttk.Frame(dl_lf)
        dr.pack(fill=tk.X, pady=3)
        ttk.Label(dr, text="清晰度:").pack(side=tk.LEFT, padx=(4, 4))
        self.fmt_var = tk.StringVar()
        self.fmt_combo = ttk.Combobox(dr, textvariable=self.fmt_var, state='readonly',
                                      width=44)
        self.fmt_combo.pack(side=tk.LEFT, padx=4)
        self.fmt_combo.set("（请先解析）")
        ttk.Button(dr, text="下载选中", width=10, style="Accent.TButton",
                   command=self.download).pack(side=tk.LEFT, padx=6)
        ttk.Button(dr, text="通用下载(不解析)", width=14,
                   command=self.download_best).pack(side=tk.LEFT, padx=6)
        self.dl_state = tk.StringVar(value="")
        ttk.Label(dr, textvariable=self.dl_state, font=("微软雅黑", 9),
                  foreground="gray").pack(side=tk.LEFT, padx=8)

        # 进度条
        self.progress = ttk.Progressbar(dl_lf, maximum=100, value=0)
        self.progress.pack(fill=tk.X, padx=6, pady=(0, 4))
        self.prog_var = tk.StringVar(value="")
        ttk.Label(dl_lf, textvariable=self.prog_var, font=("微软雅黑", 9)).pack(anchor='w', padx=8)

        # 日志
        log_lf = ttk.LabelFrame(self.parent, text="下载日志", style="Card.TLabelframe")
        log_lf.pack(fill=tk.BOTH, expand=True, pady=2)
        self.log_text = tk.Text(log_lf, font=("微软雅黑", 9), wrap=tk.WORD,
                                state=tk.DISABLED, height=8)
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        ss = ttk.Scrollbar(self.log_text, command=self.log_text.yview)
        ss.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=ss.set)

        # 说明
        ttk.Label(self.parent,
                  text="说明：\n· 本页适用于番剧/哔哩哔哩/B站等长视频与主流视频网站；\n"
                       "· 短视频类网页媒体请改用「资源嗅探」页，打开页面播放即自动捕获下载；\n"
                       "· 引擎为 yt-dlp（支持 1000+ 网站），视频+音频自动合并为单个 mp4；\n"
                       "· 下载保存到上方所选「下载路径」；如需更高清晰度请登录后使用浏览器 Cookie。",
                  font=("微软雅黑", 8), foreground="#94a3b8", justify=tk.LEFT).pack(anchor='w', padx=8, pady=4)

    def _browse_path(self):
        from tkinter import filedialog
        try:
            d = filedialog.askdirectory(parent=self.parent, title="选择下载保存位置")
            if d:
                self.path_var.set(d)
        except Exception:
            pass

    def _paste_url(self):
        try:
            self.url_var.set(self.parent.clipboard_get())
        except Exception:
            pass

    def _log(self, msg):
        try:
            self.log_text.configure(state=tk.NORMAL)
            self.log_text.insert(tk.END, msg + "\n")
            self.log_text.see(tk.END)
            self.log_text.configure(state=tk.DISABLED)
        except Exception:
            pass

    # ---------------- 解析 ----------------
    def parse(self):
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("提示", "请先粘贴视频链接")
            return
        if not os.path.exists(self.ytdlp_path):
            messagebox.showerror("缺少引擎", "未找到 yt-dlp.exe，请将 yt-dlp.exe 放到程序目录")
            return
        self.parse_btn.configure(state='disabled', text="解析中…")
        self.info_var.set("正在解析…（首次较慢，请稍候）")
        self._log(f"== 解析: {url}")
        self._parse_thread = threading.Thread(target=self._parse_worker, args=(url,), daemon=True)
        self._parse_thread.start()

    def _parse_worker(self, url):
        try:
            args = [
                self.ytdlp_path, '--dump-json', '--no-playlist', '--no-check-certificates',
                '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                '-f', 'bv*+ba/b', url,
            ]
            proc = subprocess.Popen(
                args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            out, err = proc.communicate(timeout=180)
            if proc.returncode != 0:
                err_txt = err.decode('utf-8', 'ignore')[-600:]
                self.parent.after(0, lambda: self._parse_fail("解析失败：" + err_txt.strip() or "未知错误"))
                return
            # 解析 JSON（dump-json 可能输出一行 JSON）
            data = out.decode('utf-8', 'ignore').strip()
            # 有些站点输出多行，取最后完整 JSON
            lines = data.split('\n')
            info = None
            for ln in reversed(lines):
                ln = ln.strip()
                if ln.startswith('{'):
                    try:
                        info = json.loads(ln)
                        break
                    except Exception:
                        continue
            if info is None:
                self.parent.after(0, lambda: self._parse_fail("无法解析返回数据"))
                return
            self._build_formats(info)
            self.parent.after(0, self._show_info, info)
        except subprocess.TimeoutExpired:
            self.parent.after(0, lambda: self._parse_fail("解析超时（180 秒），请检查链接或网络"))
        except Exception as e:
            self.parent.after(0, lambda: self._parse_fail(f"解析异常: {e}"))

    def _build_formats(self, info):
        """从 formats 提取可下载清晰度（去重排序）；始终保留「通用（自动最佳）」兜底选项"""
        # 通用兜底：yt-dlp 自动选择最佳（视频+音频合并/纯视频）
        self._formats = [{'id': 'best', 'label': '通用（自动最佳，推荐）', 'fsize': 0}]
        seen = {}
        for f in info.get('formats', []) or []:
            if f.get('vcodec') in (None, 'none'):
                continue  # 纯音频
            height = f.get('height') or 0
            fid = f.get('format_id', '')
            if not fid:
                continue
            ext = f.get('ext', '?')
            fsize = f.get('filesize') or f.get('filesize_approx') or 0
            note = f.get('format_note') or ''
            label = f"{height or '?'}p {ext} {self._fmt_size(fsize)}{(' ' + note) if note else ''}"
            # 同高度取 filesize 最大的
            if height not in seen or (fsize and fsize > seen[height]['fsize']):
                seen[height] = {'id': fid, 'label': label, 'fsize': fsize}
        heights = sorted([h for h in seen if h], reverse=True)
        self._formats += [seen[h] for h in heights]
        # 若没有可分离视频，退回完整格式（附加到通用之后）
        if len(self._formats) <= 1:
            for f in info.get('formats', []) or []:
                fid = f.get('format_id', '')
                if fid and f.get('vcodec') != 'none':
                    self._formats.append({'id': fid, 'label': f"{f.get('format_note') or fid} {f.get('ext','')}",
                                          'fsize': 0})
        # 音频（追加）
        for f in info.get('formats', []) or []:
            if f.get('vcodec') in (None, 'none') and f.get('acodec') not in (None, 'none'):
                fid = f.get('format_id', '')
                if fid:
                    self._formats.append({'id': fid,
                                          'label': f"仅音频 {f.get('ext','?')} {self._fmt_size(f.get('filesize') or 0)}",
                                          'fsize': 0})
                    break

    @staticmethod
    def _fmt_size(n):
        try:
            n = float(n or 0)
            if n <= 0:
                return ''
            for u in ['B', 'KB', 'MB', 'GB']:
                if n < 1024:
                    return f"{n:.0f}{u}"
                n /= 1024
            return f"{n:.1f}TB"
        except Exception:
            return ''

    def _show_info(self, info):
        url_now = (self.url_var.get() or '').strip()
        extr = (info.get('extractor') or info.get('extractor_key') or '')
        if self._auto_extract and (extr == 'generic' or 'kuaishou' in url_now.lower() or 'douyin' in url_now.lower()):
            self._auto_extract = False
            self._log("识别为 JS 渲染站点（generic/快手/抖音），自动切换内置浏览器提取真实地址…")
            self.info_var.set("正在用内置浏览器提取真实视频地址…")
            threading.Thread(target=self._browser_extract_flow, args=(url_now,), daemon=True).start()
            return
        title = info.get('title') or info.get('fulltitle') or '未知标题'
        dur = info.get('duration')
        dur_s = ''
        if dur:
            dur_s = f"{int(dur // 60)}分{int(dur % 60)}秒"
        uploader = info.get('uploader') or info.get('channel') or ''
        self.info_var.set(f"标题：{title}\n"
                          f"作者：{uploader}   时长：{dur_s or '未知'}"
                          + (f"   来源：{info.get('extractor_key','')}" if info.get('extractor_key') else ''))
        labels = [f["label"] for f in self._formats]
        if labels:
            self.fmt_combo['values'] = labels
            self.fmt_combo.current(0)
        else:
            self.fmt_combo.set("（未找到可用格式）")
        self.parse_btn.configure(state='normal', text="解析视频")
        self._log(f"解析成功：{title}  （{len(self._formats)} 种清晰度）")

    def _parse_fail(self, msg):
        self.parse_btn.configure(state='normal', text="解析视频")
        self.info_var.set(msg + "\n（正在尝试用内置浏览器提取真实地址…）")
        self._log("!! " + msg)
        if not self._formats:
            self._formats = [{'id': 'best', 'label': '通用（自动最佳，推荐）', 'fsize': 0}]
        self.fmt_combo['values'] = [f['label'] for f in self._formats]
        self.fmt_combo.current(0)
        url = self.url_var.get().strip()
        if url and self._auto_extract:
            self._auto_extract = False   # 只自动尝试一次，失败后转人工
            threading.Thread(target=self._browser_extract_flow, args=(url,), daemon=True).start()
        else:
            messagebox.showerror("解析失败", msg + "\n\n处理建议：\n1. 点「通用下载（不解析）」直接尝试；\n2. 若为快手/抖音等 JS 渲染或需登录的网站，请改用「资源嗅探」页：打开该视频页面播放，会自动捕获真实视频地址下载。")

    # ---------------- 浏览器提取兜底（快手等 JS 渲染网站） ----------------
    def _browser_extract_flow(self, url):
        def log(m):
            try:
                self.parent.after(0, self._log, m)
            except Exception:
                pass

        log("yt-dlp 无法解析该链接，尝试用内置浏览器提取真实视频地址…")
        try:
            from browser_extract import extract_video_url
            ok, src = extract_video_url(url, log=log)
        except Exception as e:
            ok, src = False, f"加载提取器失败: {e}"
        if not ok:
            log("浏览器提取失败: " + src)
            self.parent.after(0, lambda: self.info_var.set("提取失败：" + src))
            self.parent.after(0, lambda: messagebox.showerror(
                "提取失败", src + "\n\n处理建议：请改用「资源嗅探」页：打开该视频页面播放，会自动捕获真实视频地址下载。"))
            return
        self.parent.after(0, lambda: self.url_var.set(src))
        log("✓ 提取成功，正在用直链多线程加速下载…")
        self.parent.after(0, lambda: self._download_direct(src, url))

    def _download_direct(self, src, ref_url):
        out_dir = self.path_var.get().strip() or os.path.join(app_base(), 'downloads')
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception:
            pass
        base = os.path.basename(src.split('?')[0])
        safe = re.sub(r'[\\/:*?"<>|]+', '_', base.split('.')[0]) if base else 'video'
        if not safe:
            safe = 'video'
        path = os.path.join(out_dir, safe + '.mp4')
        self.info_var.set("直链下载中…")
        self._log(f"== 直链下载 → {path}")
        try:
            from video_downloader import download_direct_parallel

            def prog(d, t):
                pct = int(d / t * 100) if t else 0
                try:
                    self.parent.after(0, self._set_progress, pct, f"直链下载 {pct}%")
                except Exception:
                    pass

            ok, info = download_direct_parallel(src, path, workers=6, referer=ref_url, progress=prog)
            if ok and os.path.exists(path):
                self.parent.after(0, self._dl_done,
                                  f"直链下载完成: {os.path.getsize(path) // 1024} KB", True)
            else:
                self.parent.after(0, self._dl_done, f"直链下载失败: {info}", False)
        except Exception as e:
            self.parent.after(0, self._dl_done, f"直链下载异常: {e}", False)

    # ---------------- 下载 ----------------
    def download(self):
        if not self._formats or self.fmt_var.get().startswith("（请先解析）"):
            messagebox.showwarning("提示", "请先解析视频并选择清晰度")
            return
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("提示", "请填写链接")
            return
        idx = None
        try:
            idx = list(self.fmt_combo['values']).index(self.fmt_var.get())
        except Exception:
            idx = 0
        fmt = self._formats[idx] if idx < len(self._formats) else self._formats[0]
        if fmt['id'] == 'best':
            fmt_arg = 'bestvideo+bestaudio/best'
        else:
            fmt_arg = f"{fmt['id']}+bestaudio/bestaudio/best"
        out_dir = self.path_var.get().strip() or os.path.join(app_base(), 'downloads')
        os.makedirs(out_dir, exist_ok=True)
        # 标题做文件名（净化非法字符）
        title = self.info_var.get().split('\n')[0].replace('标题：', '').strip() or f"video_{int(time.time())}"
        safe = re.sub(r'[\\/:*?"<>|]', '_', title)[:80]
        self._stop_flag = False
        self.dl_state.set("下载中…（可随时点「停止」）")
        self.progress['value'] = 0
        self.prog_var.set("准备中…")
        self._dl_thread = threading.Thread(target=self._dl_worker,
                                           args=(url, fmt_arg, out_dir, safe), daemon=True)
        self._dl_thread.start()

    def download_best(self):
        """通用下载（不解析）：直接用 yt-dlp 自动最佳格式下载"""
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("提示", "请先粘贴视频链接")
            return
        if not os.path.exists(self.ytdlp_path):
            messagebox.showerror("缺少引擎", "未找到 yt-dlp.exe")
            return
        out_dir = self.path_var.get().strip() or os.path.join(app_base(), 'downloads')
        os.makedirs(out_dir, exist_ok=True)
        title = f"video_{int(time.time())}"
        safe = re.sub(r'[\\/:*?"<>|]', '_', title)[:80]
        self._stop_flag = False
        self.dl_state.set("下载中…（通用自动最佳）")
        self.progress['value'] = 0
        self.prog_var.set("准备中…")
        self._dl_thread = threading.Thread(target=self._dl_worker,
                                           args=(url, 'bestvideo+bestaudio/best', out_dir, safe), daemon=True)
        self._dl_thread.start()

    def _dl_worker(self, url, fmt_id, out_dir, safe):
        args = [
            self.ytdlp_path,
            '-f', fmt_id,
            '-o', os.path.join(out_dir, f"{safe}.%(ext)s"),
            '--newline', '--no-playlist', '--no-check-certificates',
            '--merge-output-format', 'mp4',
            '--no-mtime',
        ]
        if self.ffmpeg_path:
            args += ['--ffmpeg-location', self.ffmpeg_path]
        args += [url]
        self._log("== 开始下载 → " + os.path.join(out_dir, f"{safe}.mp4"))
        try:
            self._dl_proc = subprocess.Popen(
                args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except Exception as e:
            self.parent.after(0, lambda: self._dl_done(f"启动下载失败: {e}"))
            return
        last_pct = -1
        for line in self._dl_proc.stdout:
            if self._stop_flag:
                try:
                    self._dl_proc.kill()
                except Exception:
                    pass
                break
            try:
                line_s = line.decode('utf-8', 'ignore').strip()
            except Exception:
                continue
            if not line_s:
                continue
            # 进度行: [download]  12.3% of 45.6MiB at 3.0MiB/s ETA 00:15
            m = re.search(r'\[download\]\s+([\d.]+)%', line_s)
            if m:
                pct = float(m.group(1))
                if int(pct) != last_pct:
                    last_pct = int(pct)
                    self.parent.after(0, self._set_progress, pct, line_s)
            elif '[download]' not in line_s and 'Merging' not in line_s and 'Deleting' not in line_s:
                self.parent.after(0, self._log, line_s)
        rc = self._dl_proc.wait()
        if self._stop_flag:
            self.parent.after(0, lambda: self._dl_done("已停止下载"))
        elif rc == 0:
            # 确认产物
            ext_final = 'mp4'
            if not os.path.exists(os.path.join(out_dir, f"{safe}.mp4")):
                ext_final = 'webm'
            full = os.path.join(out_dir, f"{safe}.{ext_final}")
            self.parent.after(0, lambda: self._dl_done(
                f"下载完成：{full}", ok=True))
        else:
            url_now = (self.url_var.get() or '').strip()
            if url_now and self._auto_extract:
                self._auto_extract = False
                self.parent.after(0, lambda: self._log("下载失败，尝试用内置浏览器提取真实地址…"))
                threading.Thread(target=self._browser_extract_flow, args=(url_now,), daemon=True).start()
            else:
                self.parent.after(0, lambda: self._dl_done("下载失败（请查看日志）"))

    def _set_progress(self, pct, line):
        self.progress['value'] = pct
        self.prog_var.set(line)

    def _dl_done(self, msg, ok=False):
        self.progress['value'] = 100 if ok else self.progress['value']
        self.dl_state.set("完成" if ok else "已结束")
        self.prog_var.set(msg)
        self._log(("✓ " if ok else "") + msg)
        if ok:
            messagebox.showinfo("下载完成", msg)
