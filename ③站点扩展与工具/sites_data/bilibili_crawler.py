# -*- coding: utf-8 -*-
# 哔哩哔哩 专用爬虫
# 支持两类内容：
#   1) 普通视频 (/video/BV...)：解析 window.__playinfo__ 里的 DASH 音视频流
#   2) 番剧 (/bangumi/play/ss... / ep...)：season API 拿选集 → pgc playurl API 拿 DASH 双流
# 番剧播放地址需要登录，Cookie 自动从浏览器会话透传；未登录时给出明确提示
# 下载时由 download_flow 用 ffmpeg 把 视频流+音频流 合成为单个 mp4
import json
import re
import time
from urllib.parse import quote, urljoin

import requests

from utils import is_normal_url

_PLAYINFO_RE = re.compile(r'window\.__playinfo__=(\{.*?\})</script>', re.S)
_BANGUMI_SS_RE = re.compile(r'/bangumi/play/ss(\d+)')
_BANGUMI_EP_RE = re.compile(r'/bangumi/play/ep(\d+)')
_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
       'Chrome/120.0.0.0 Safari/537.36')


def _clean_title(tab):
    """从页面标题提取视频名（去掉站点后缀）"""
    try:
        t = (tab.title or '').strip()
        for suf in ('_哔哩哔哩_bilibili', ' - 哔哩哔哩_bilibili', ' - bilibili'):
            if t.endswith(suf):
                t = t[:-len(suf)]
        t = re.sub(r'\s{2,}', ' ', t)
        return t[:60] or '视频'
    except Exception:
        return '视频'


def _pick_stream(items):
    """从 DASH 流列表挑清晰度最高的可用地址"""
    if not items:
        return None
    ok = [it for it in items if it.get('baseUrl') or it.get('backupUrl')]
    if not ok:
        return None
    ok.sort(key=lambda x: int(x.get('id') or 0), reverse=True)
    it = ok[0]
    url = it.get('baseUrl') or ''
    if not url and it.get('backupUrl'):
        url = it['backupUrl'][0]
    return url or None


class BilibiliCrawler:
    """哔哩哔哩：搜索→打开视频/番剧页→解析DASH音视频流→ffmpeg合成下载"""

    SITE_NAME = '哔哩哔哩'
    SITE_URL = 'https://www.bilibili.com'
    REQUIRES_LOGIN = False
    NEEDS_BROWSER = True

    CONFIG = {
        'site_url': 'https://www.bilibili.com',
        'direct_connect': False,
    }

    def __init__(self, crawler):
        self.crawler = crawler

    # ---------- 浏览器 Cookie 透传 ----------
    @staticmethod
    def _tab_cookie_str(tab):
        """把浏览器会话 Cookie 拼成 Cookie 请求头（番剧API需要登录态）"""
        try:
            cookies = tab.cookies()
        except Exception:
            return ''
        parts = []
        for c in cookies or []:
            try:
                n = str(c.get('name') or '')
                v = str(c.get('value') or '')
                if n and v and v not in ('-', 'DEL', 'delete'):
                    parts.append(f'{n}={v}')
            except Exception:
                continue
        return '; '.join(parts)

    def _api_get(self, url, cookie='', referer=None, timeout=10):
        """请求 B站 JSON API（直连，不走系统代理，避免代理问题挂起）"""
        headers = {'User-Agent': _UA}
        if cookie:
            headers['Cookie'] = cookie
        headers['Referer'] = referer or 'https://www.bilibili.com/'
        try:
            r = requests.get(url, headers=headers, timeout=(8, timeout), proxies=None)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return {}

    # ---- 搜索 ----
    def search_series(self, name, series_id=None):
        """name = 视频标题/BV链接/av号；返回已打开视频页（或番剧页）的标签"""
        name = name.strip()
        if is_normal_url(name):
            self.crawler.tab.get(name)
            time.sleep(5)
            return self.crawler.tab

        search_url = 'https://search.bilibili.com/all?keyword=' + quote(name)
        self.crawler.tab.get(search_url)
        time.sleep(5)
        kw = name.lower()
        try:
            a_eles = self.crawler.tab.eles('tag:a', timeout=5)
        except Exception:
            a_eles = []

        def _score(full, text):
            # 普通视频优先；番剧正片其次；文本/URL 含关键词 +2
            s = 0
            if '/video/BV' in full:
                s += 3
            elif '/bangumi/play/' in full:
                s += 2
            if kw and (kw in full.lower() or kw in text.lower()):
                s += 2
            return s

        best, best_score = None, 0
        for ele in a_eles[:500]:
            try:
                href = ele.attr('href') or ''
                text = (ele.text or '').strip()
            except Exception:
                continue
            full = urljoin('https://www.bilibili.com', href)
            if '/video/BV' not in full and '/bangumi/play/' not in full:
                continue
            s = _score(full, text)
            if s > best_score:
                best, best_score = full, s
        if not best:
            raise ValueError(f"未在B站搜索结果中找到「{name}」的视频/番剧，请确认名称或直接粘贴链接")
        self.crawler.tab.get(best)
        time.sleep(5)
        return self.crawler.tab

    def get_episode_count(self, target_tab):
        """番剧返回真实集数；普通视频返回 1"""
        try:
            url = target_tab.url or ''
        except Exception:
            url = ''
        m = _BANGUMI_SS_RE.search(url)
        if m:
            n = self._bangumi_ep_count(m.group(1))
            if n:
                return n
        return 1

    def get_episode_list(self, target_tab):
        return None

    # ---------- 番剧：选集 ----------
    def _bangumi_ep_count(self, season_id, cookie=''):
        data = self._api_get(
            'https://api.bilibili.com/pgc/view/web/season?season_id=' + season_id,
            cookie=cookie)
        eps = ((data.get('result') or {}).get('episodes')) or []
        return len(eps)

    def _bangumi_eps_meta(self, season_id, cookie=''):
        """返回 [{ep_id, cid, aid, title}, ...]（整季）"""
        data = self._api_get(
            'https://api.bilibili.com/pgc/view/web/season?season_id=' + season_id,
            cookie=cookie)
        eps = ((data.get('result') or {}).get('episodes')) or []
        meta = []
        for e in eps:
            title = (e.get('long_title') or e.get('title') or '').strip()
            ep_id = e.get('id') or e.get('ep_id')
            if not ep_id:
                continue
            meta.append({'ep_id': ep_id, 'cid': e.get('cid'),
                         'aid': e.get('aid'), 'title': title})
        return meta

    # ---------- 收集 ----------
    def collect_episode_videos(self, target_tab, episode_start=1, episode_end=0,
                               max_threads=5, progress_callback=None, keyword=None):
        """解析当前页面视频流，返回 [{episode_num,title,video_url,audio_url,video_type,referer}]"""
        try:
            url = target_tab.url or ''
        except Exception:
            url = ''
        if '/bangumi/' in url:
            return self._collect_bangumi(target_tab, url, episode_start, episode_end,
                                         progress_callback)
        return self._collect_regular(target_tab, progress_callback)

    def _collect_regular(self, target_tab, progress_callback):
        """普通视频：window.__playinfo__ → DASH 双流"""
        info = self._extract_playinfo(target_tab)
        if not info:
            raise RuntimeError(
                "未能解析到播放数据（__playinfo__）。可能原因：需要登录/大会员才能看该视频、"
                "或页面未加载完成。可在设置区Cookie栏填写浏览器Cookie后重试。")
        data = info.get('data') or {}
        dash = data.get('dash') or {}
        vstream = _pick_stream(dash.get('video'))
        astream = _pick_stream(dash.get('audio'))

        if not vstream:
            durl = data.get('durl') or []
            if durl and durl[0].get('url'):
                eps = [{
                    'episode_num': 1,
                    'title': _clean_title(target_tab),
                    'video_url': durl[0]['url'],
                    'audio_url': None,
                    'video_type': 'direct',
                    'referer': 'https://www.bilibili.com/',
                }]
                if progress_callback:
                    progress_callback()
                return eps
            raise RuntimeError("未解析到视频流（可能需登录/大会员）")
        if not astream:
            raise RuntimeError("未解析到音频流（可能需登录/大会员）")

        ep = {
            'episode_num': 1,
            'title': _clean_title(target_tab),
            'video_url': vstream,
            'audio_url': astream,
            'video_type': 'dash',
            'referer': 'https://www.bilibili.com/',
        }
        if progress_callback:
            progress_callback()
        return [ep]

    def _collect_bangumi(self, target_tab, url, episode_start, episode_end,
                         progress_callback):
        """番剧：season API 拿选集 → pgc playurl API 拿 DASH 双流（需登录）"""
        cookie = self._tab_cookie_str(target_tab)
        referer = url or 'https://www.bilibili.com/bangumi/play/'

        # 1. 定位 season_id
        season_id = None
        m = _BANGUMI_SS_RE.search(url)
        if m:
            season_id = m.group(1)
        if not season_id:
            # ep 页：从 __INITIAL_STATE__ 找 seasonId
            try:
                html = target_tab.html or ''
            except Exception:
                html = ''
            m2 = re.search(r'"seasonId"\s*:\s*(\d+)', html)
            if m2:
                season_id = m2.group(1)
        if not season_id:
            raise RuntimeError("无法识别B站番剧ID，请直接打开番剧播放页后重试")

        # 2. 选集列表
        meta = self._bangumi_eps_meta(season_id, cookie)
        if not meta:
            raise RuntimeError("未获取到番剧选集列表（可能网络异常或页面已改版）")
        total = len(meta)
        start = max(episode_start, 1)
        end = min(episode_end, total) if episode_end > 0 else total

        # 3. 逐集取播放地址
        eps, errs = [], []
        for i, epm in enumerate(meta[start - 1:end], start=start):
            play_url = (f'https://api.bilibili.com/pgc/player/web/playurl?'
                        f'ep_id={epm["ep_id"]}&qn=127&fnval=16&fourk=1')
            data = self._api_get(play_url, cookie, referer)
            code = data.get('code')
            if code != 0:
                errs.append(f'第{i}集: {data.get("message") or f"code={code}"}')
                continue
            dash = ((data.get('result') or {}).get('dash')) or {}
            vstream = _pick_stream(dash.get('video'))
            astream = _pick_stream(dash.get('audio'))
            if vstream and astream:
                eps.append({
                    'episode_num': i,
                    'title': epm['title'] or f'第{i}话',
                    'video_url': vstream,
                    'audio_url': astream,
                    'video_type': 'dash',
                    'referer': 'https://www.bilibili.com/',
                })
                if progress_callback:
                    progress_callback()
                continue
            durl = ((data.get('result') or {}).get('durl')) or []
            if durl and durl[0].get('url'):
                eps.append({
                    'episode_num': i,
                    'title': epm['title'] or f'第{i}话',
                    'video_url': durl[0]['url'],
                    'audio_url': None,
                    'video_type': 'direct',
                    'referer': 'https://www.bilibili.com/',
                })
                if progress_callback:
                    progress_callback()
                continue
            errs.append(f'第{i}集: 未解析到播放流')

        if eps:
            if errs:
                raise RuntimeError(
                    f"获取到 {len(eps)}/{end - start + 1} 集，失败: {'; '.join(errs[:3])}"
                    + ("；如需完整播放请在浏览器窗口登录后重试" if any('登录' in e for e in errs) else ''))
            return eps

        # 全部失败：优先提示登录
        need_login = any(('登录' in e or 'code=-403' in e or 'code=-101' in e or 'code=-400' in e)
                         for e in errs)
        if need_login:
            raise RuntimeError(
                "B站番剧需要登录才能获取播放地址（未登录仅能看低清或无法播放）。\n"
                "操作：保持程序弹出的浏览器窗口可见（设置页“无头模式”勾去掉），"
                "在该窗口里登录 bilibili.com 后，重新点“加载剧集表”。")
        raise RuntimeError("获取番剧播放地址失败: " + '; '.join(errs[:3]))

    @staticmethod
    def _extract_playinfo(tab):
        """从渲染后的页面取 window.__playinfo__，返回 dict 或 None（播放器初始化需等待）"""
        for _ in range(4):
            try:
                html = tab.html or ''
            except Exception:
                html = ''
            m = _PLAYINFO_RE.search(html)
            if m:
                try:
                    return json.loads(m.group(1))
                except Exception:
                    pass
            time.sleep(2)
        return None
