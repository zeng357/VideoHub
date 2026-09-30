# 通用视频下载器 - 通用格式转换页（单开一页，位于「设置」上方）
# 功能：任意通用视频格式无损互转（ffmpeg 流复制 -c copy，不重编码、画质零损失）
#       编码不兼容时可选「兼容模式（重编码）」。
# 依赖：程序核心目录 _internal/ffmpeg.exe
import os
import re
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from utils import ensure_console_safe, app_base
ensure_console_safe()

ACCENT = "#3b82f6"
FMT_EXTS = ['mp4', 'mkv', 'webm', 'avi', 'mov', 'flv', 'ts', 'm4v', 'mpg']


class ConverterPage:
    """通用格式转换页：无损（流复制）转任意通用视频格式"""

    def __init__(self, parent, gui=None):
        self.parent = parent
        self.gui = gui
        self._conv_thread = None
        self._proc = None
        self._stop_flag = False
        self._duration = 0.0
        self._build_ui()

    # ---------------- UI ----------------
    def _build_ui(self):
        top = ttk.Frame(self.parent)
        top.pack(fill=tk.X, pady=(0, 4))
        self.state_var = tk.StringVar(value="通用格式转换：无损互转（不重编码，画质零损失）")
        ttk.Label(top, textvariable=self.state_var, font=("微软雅黑", 9),
                  foreground=ACCENT).pack(anchor='w')

        # 视频设置（保存位置）
        path_frame = ttk.LabelFrame(self.parent, text="视频设置", style="Card.TLabelframe")
        path_frame.pack(fill=tk.X, pady=2)
        path_row = ttk.Frame(path_frame)
        path_row.pack(fill=tk.X, pady=2)
        ttk.Label(path_row, text="输出路径:", width=9).pack(side=tk.LEFT)
        self.path_var = tk.StringVar(value=os.path.join(app_base(), 'downloads', '转换输出'))
        ttk.Entry(path_row, textvariable=self.path_var, font=("微软雅黑", 10)).pack(
            side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(path_row, text="浏览", width=6,
                   command=self._browse_path).pack(side=tk.LEFT, padx=4)

        # 输入文件 + 输出格式
        src_frame = ttk.LabelFrame(self.parent, text="输入视频与输出设置", style="Card.TLabelframe")
        src_frame.pack(fill=tk.X, pady=2)
        src_row = ttk.Frame(src_frame)
        src_row.pack(fill=tk.X, pady=3)
        ttk.Label(src_row, text="输入文件:", width=9).pack(side=tk.LEFT)
        self.inp_var = tk.StringVar()
        ttk.Entry(src_row, textvariable=self.inp_var, font=("微软雅黑", 10)).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ttk.Button(src_row, text="选择视频…", width=10,
                   command=self._browse_input).pack(side=tk.LEFT, padx=2)

        fmt_row = ttk.Frame(src_frame)
        fmt_row.pack(fill=tk.X, pady=(0, 3))
        ttk.Label(fmt_row, text="输出格式:").pack(side=tk.LEFT, padx=(12, 4))
        self.fmt_var = tk.StringVar(value='mp4')
        ttk.Combobox(fmt_row, textvariable=self.fmt_var, state='readonly', width=10,
                     values=FMT_EXTS).pack(side=tk.LEFT, padx=4)
        ttk.Label(fmt_row, text="转换模式:").pack(side=tk.LEFT, padx=(12, 4))
        self.mode_var = tk.StringVar(value='无损（流复制·不重编码）')
        ttk.Combobox(fmt_row, textvariable=self.mode_var, state='readonly', width=22,
                     values=['无损（流复制·不重编码）', '兼容（重编码 H.264+AAC）']).pack(side=tk.LEFT, padx=4)
        ttk.Label(fmt_row, text="无损=最快且画质零损失；若提示编码不兼容请换兼容模式",
                  font=("微软雅黑", 8), foreground="#64748b").pack(side=tk.LEFT, padx=8)

        # 转换进度
        prog_frame = ttk.LabelFrame(self.parent, text="转换进度", style="Card.TLabelframe")
        prog_frame.pack(fill=tk.X, pady=2)
        self.prog_info = ttk.Label(prog_frame, text="进度: 0%", font=("微软雅黑", 10))
        self.prog_info.pack(anchor='w', padx=6, pady=2)
        self.progress_bar = ttk.Progressbar(prog_frame, mode='determinate')
        self.progress_bar.pack(fill=tk.X, padx=6, pady=(0, 4))

        # 按钮
        btn_row = ttk.Frame(self.parent)
        btn_row.pack(fill=tk.X, pady=4)
        ttk.Button(btn_row, text="开始转换", width=12, style="Accent.TButton",
                   command=self._convert).pack(side=tk.LEFT, padx=2)
        self.stop_btn = ttk.Button(btn_row, text="停止", width=8,
                                   command=self._stop).pack(side=tk.LEFT, padx=2)

        # 日志
        log_lf = ttk.LabelFrame(self.parent, text="转换日志", style="Card.TLabelframe")
        log_lf.pack(fill=tk.BOTH, expand=True, pady=2)
        self.log_text = tk.Text(log_lf, font=("Consolas", 9), wrap=tk.WORD,
                                bg="#f8fafc", height=8)
        lsb = ttk.Scrollbar(log_lf, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=lsb.set)
        lsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

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
        try:
            d = filedialog.askdirectory(parent=self.parent, title="选择输出保存位置")
            if d:
                self.path_var.set(d)
        except Exception:
            pass

    def _browse_input(self):
        try:
            f = filedialog.askopenfilename(
                parent=self.parent, title="选择要转换的视频文件",
                filetypes=[("视频文件", "*.mp4 *.mkv *.webm *.avi *.mov *.flv *.ts *.m4v *.mpg *.wmv"),
                           ("所有文件", "*.*")])
            if f:
                self.inp_var.set(f)
        except Exception:
            pass

    # ---------------- 转换 ----------------
    def _find_ffmpeg(self):
        try:
            from video_downloader import _find_ffmpeg
            return _find_ffmpeg()
        except Exception:
            return None

    def _probe_duration(self, ffmpeg, path):
        try:
            r = subprocess.run([ffmpeg, '-i', path], capture_output=True,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            err = r.stderr.decode('utf-8', 'ignore')
            m = re.search(r'Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)', err)
            if m:
                h, mi, s = m.groups()
                return int(h) * 3600 + int(mi) * 60 + float(s)
        except Exception:
            pass
        return 0.0

    def _convert(self):
        inp = self.inp_var.get().strip()
        if not inp or not os.path.isfile(inp):
            messagebox.showwarning("提示", "请先选择要转换的视频文件")
            return
        ffmpeg = self._find_ffmpeg()
        if not ffmpeg or not os.path.isfile(ffmpeg):
            messagebox.showerror("缺少引擎", "未找到 ffmpeg.exe（应位于程序核心目录 _internal\\）")
            return
        if self._conv_thread is not None and self._conv_thread.is_alive():
            messagebox.showinfo("转换中", "已有转换任务进行中，请稍候")
            return
        out_dir = self.path_var.get().strip() or os.path.join(app_base(), 'downloads', '转换输出')
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception:
            pass
        ext = self.fmt_var.get().strip().lstrip('.') or 'mp4'
        base = os.path.splitext(os.path.basename(inp))[0]
        safe = re.sub(r'[\\/:*?"<>|]', '_', base)[:80]
        out_path = os.path.join(out_dir, f"{safe}.{ext}")
        self._log(f"== 转换: {inp}")
        self._log(f"   输出: {out_path}")
        self._log(f"   模式: {self.mode_var.get()}")
        self._stop_flag = False
        self.progress_bar['value'] = 0
        self.prog_info.configure(text="进度: 0%  （正在探测时长…）")
        self._duration = self._probe_duration(ffmpeg, inp)
        self._conv_thread = threading.Thread(target=self._conv_worker,
                                             args=(ffmpeg, inp, out_path), daemon=True)
        self._conv_thread.start()

    def _conv_worker(self, ffmpeg, inp, out_path):
        lossless = '无损' in self.mode_var.get()
        if lossless:
            cmd = [ffmpeg, '-y', '-i', inp, '-c', 'copy',
                   '-progress', 'pipe:1', '-nostats', out_path]
        else:
            cmd = [ffmpeg, '-y', '-i', inp,
                   '-c:v', 'libx264', '-preset', 'fast', '-crf', '18',
                   '-c:a', 'aac', '-b:a', '192k',
                   '-progress', 'pipe:1', '-nostats', out_path]
        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except Exception as e:
            self._log(f"[错误] 启动转换失败: {e}")
            return
        out_time = 0.0
        for line in self._proc.stdout:
            if self._stop_flag:
                try:
                    self._proc.kill()
                except Exception:
                    pass
                break
            try:
                txt = line.decode('utf-8', 'ignore').strip()
            except Exception:
                continue
            if txt.startswith('out_time_us='):
                try:
                    out_time = int(txt.split('=')[1]) / 1e6
                except Exception:
                    continue
                if self._duration > 0:
                    pct = min(99.0, out_time / self._duration * 100)
                    self.parent.after(0, self._set_progress, pct)
            elif '=' not in txt and txt and 'frame=' not in txt:
                self.parent.after(0, self._log, txt)
        rc = self._proc.wait()
        self._proc = None
        if self._stop_flag:
            self.parent.after(0, lambda: self._done("已停止转换", False))
        elif rc == 0 and os.path.exists(out_path):
            sz = os.path.getsize(out_path) / (1024 * 1024)
            self.parent.after(0, lambda: self._done(
                f"转换完成：{out_path}\n大小：{sz:.1f} MB", True))
        else:
            # 无损失败提示（编码不兼容）
            msg = "转换失败。若是「无损」模式，多半是编码与目标格式不兼容"
            if lossless:
                msg += "，请改用「兼容（重编码）」模式重试。"
            else:
                msg += "，请查看日志。"
            self.parent.after(0, lambda: self._done(msg, False))

    def _set_progress(self, pct):
        try:
            self.progress_bar['value'] = pct
            self.prog_info.configure(text=f"进度: {pct:.0f}%")
        except Exception:
            pass

    def _done(self, msg, ok):
        self.progress_bar['value'] = 100 if ok else self.progress_bar['value']
        self.prog_info.configure(text="完成" if ok else "已结束")
        self._log(("✓ " if ok else "! ") + msg)
        if ok:
            messagebox.showinfo("转换完成", msg)
        else:
            messagebox.showerror("转换失败", msg)

    def _stop(self):
        self._stop_flag = True
        try:
            if self._proc:
                self._proc.kill()
        except Exception:
            pass
        self._log("[停止] 正在停止转换…")
