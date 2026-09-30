# -*- coding: utf-8 -*-
"""agedm_crawler.py: AGE动漫（www.agedm.io）专用爬虫
站点模板：自有模板（详情 /detail/{数字id}，播放 /play/{id}/{sid}/{nid}，搜索 /search?query=）
播放器：iframe → jx.wuzhoupai.com 云播放器 → <video> 直链（复用父类 iframe 深入）
"""
import re
import time
from urllib.parse import quote, urljoin

from utils import is_normal_url
from donghua_hk_crawler import (
    DonghuaHKCrawler as _BaseCrawler,
    _RE_A_DETAIL, _RE_CAT_NAV, _RE_LOOSE_PLAY,
    _page_episode_num,
)


def _age_episode_num(text):
    """AGE 用“第01话/第156话”而非“第N集”，兼容两种写法"""
    m = re.search(r'第\s*(\d+(?:\.\d+)?)\s*(?:话|集|话(?:\s*第)?)', str(text or ''))
    if m:
        try:
            return int(float(m.group(1)))
        except Exception:
            return 0
    return _page_episode_num(text)


class AgeDMCrawler(_BaseCrawler):
    SITE_NAME = 'AGE动漫'
    SITE_URL = 'https://www.agedm.io/'
    REQUIRES_LOGIN = False
    NEEDS_BROWSER = True

    CONFIG = {
        'site_url': 'https://www.agedm.io/',
        'direct_connect': False,
    }

    # 弱站保护（风控）：并发1、每请求间隔1.5s+随机，最大限度模拟人手，防止服务器风控
    COLLECT_WORKERS = 1
    COLLECT_GAP = 1.5

    # ---------- 搜索 ----------
    def search_series(self, name, series_id=None):
        name = name.strip()
        if is_normal_url(name):
            self._log(f'打开链接: {name}')
            self.crawler.tab.get(name, timeout=10)
            time.sleep(4)
            return self.crawler.tab

        kw = quote(name)
        candidates = [
            'https://www.agedm.io/search?query={}'.format(kw),
            'https://www.agedm.io/search?wd={}'.format(kw),
            'https://www.agedm.io/search/{}.html'.format(kw),
            'https://www.agedm.io/so/{}.html'.format(kw),
        ]
        for url in candidates:
            self._log(f'[搜索] 尝试: {url}')
            try:
                self.crawler.tab.get(url, timeout=10)
                self._log('  页面已打开，等待匹配结果...')
            except Exception as e:
                self._log(f'  打开失败: {str(e)[:60]}')
                continue
            title = self._page_title(self.crawler.tab)
            self._log(f'  页面标题: {title}')
            is_search_page = ('搜索' in title or '/search' in url.lower())
            # AGE 搜索页用 query 参数；命中判定优先精确匹配剧名文本，
            # 只有当页面标题包含剧名（确认搜索词已生效）时才允许 relax 取第一个结果，
            # 避免 wd 参数失效时把"全部记录"里的无关番剧误当命中。
            name_in_title = any(k in title for k in (name[:4], name[:6])) if len(name) >= 2 else False
            detail = self._wait_detail_link(keyword=name, timeout=6,
                                            relax=is_search_page and name_in_title)
            if detail:
                # 多结果选择：搜索页有多个分季/分篇/版本时让用户在GUI选择
                try:
                    html_now = self.crawler.tab.html or ''
                except Exception:
                    html_now = ''
                cands = self._collect_candidates(html_now, name)
                if cands and not any(c[1] == detail for c in cands):
                    cands.insert(0, ('', detail))
                detail = self._resolve_candidates(cands) or detail
                self._log(f'  搜索命中: {detail}')
                self.crawler.tab.get(detail, timeout=10)
                time.sleep(4)
                return self.crawler.tab
            self._log('  该路径无匹配结果')

        # 主页兜底（最近更新/热门列表滚动匹配）
        self._log('[兜底] 站内搜索未命中，扫描主页...')
        try:
            self.crawler.tab.get('https://www.agedm.io/', timeout=10)
            time.sleep(3)
        except Exception:
            pass
        detail = self._scroll_wait_detail(keyword=name, timeout=10, click_more=True)
        if detail:
            self._log(f'  主页命中: {detail}')
            self.crawler.tab.get(detail, timeout=10)
            time.sleep(4)
            return self.crawler.tab

        raise ValueError(
            f"未在AGE动漫找到「{name}」的剧集。请确认名称，或直接粘贴详情页/播放页链接")

    # ---------- 剧集列表（AGE 专用：限定视频详情页的选集容器） ----------
    def _is_logged_in(self):
        """AGE 登录态判断：有 Cookie（文本字符串 或 登录窗口保存的 json）即视为已登录"""
        try:
            c = self.crawler
            return bool(getattr(c, 'has_cookie_str', lambda: False)()
                        or getattr(c, 'has_saved_cookies', lambda: False)())
        except Exception:
            return False

    def _episode_ul_links(self, tab):
        """读取 AGE 详情页的选集列表，返回 [(播放URL, 集数), ...]
        顺序与父类 _play_links 一致（link 在前）。
        AGE 详情页有多个播放源 tab（每个都是 ul.video_detail_episode，线路1~N）：
        - 未登录：线路1为VIP专用，从线路2开始向后探测，取第一个有剧集列表的容器；
          仅当线路2之后全为空时才回退线路1。
        - 已登录：从线路1开始向后探测，取第一个有剧集列表的容器。
        """
        uls = []
        # 等待线路容器加载（页面JS渲染慢时轮询重试，避免误判为空走全页正则数出错误集数）
        for _ in range(12):
            try:
                uls = tab.eles('css:ul.video_detail_episode')
            except Exception:
                uls = []
            if uls:
                break
            try:
                tab.wait(0.5)
            except Exception:
                time.sleep(0.5)
        start = 0 if self._is_logged_in() else 1  # 未登录跳过线路1(VIP)
        picked = []
        # 从起始线路向后找第一个非空容器
        for i in range(start, len(uls)):
            try:
                els = uls[i].eles('css:a[href*="/play/"]') or []
            except Exception:
                els = []
            if els:
                picked = els
                break
        # 起始线路之后全空：回退到线路1之前的容器（如只有线路1的冷门番）
        if not picked:
            for i in range(0, start):
                try:
                    els = uls[i].eles('css:a[href*="/play/"]') or []
                except Exception:
                    els = []
                if els:
                    picked = els
                    break
        items = []
        seen = set()
        for el in picked:
            try:
                href = el.attr('href') or ''
                text = (el.text or '').strip()
            except Exception:
                continue
            if not href:
                continue
            full = urljoin(self.SITE_URL, href)
            if full in seen:
                continue
            seen.add(full)
            num = _age_episode_num(text)
            items.append((full, num))
        if items:
            items.sort(key=lambda x: x[1])
            return items
        # 兜底：全页正则（含相关动画时会去重+按详情页主 id 过滤）
        try:
            html = tab.html or ''
        except Exception:
            html = ''
        main_id = ''
        m = re.search(r'/play/(\d+)/1/\d+', html)
        if m:
            main_id = m.group(1)
        raw = _BaseCrawler._line_map(html)
        items = []
        seen = set()
        for num in sorted(raw):
            for _sid, _href in raw[num]:
                full = urljoin(self.SITE_URL, _href)
                if main_id and '/play/{}/'.format(main_id) not in full:
                    continue  # 相关动画的播放链接，排除
                if full in seen:
                    continue
                seen.add(full)
                items.append((full, num))
                break
        return items

    def _line_map(self, html):
        """AGE 专用线路表：按登录态决定线路尝试顺序
        - 未登录：剔除线路1（VIP专用），从线路2开始向后找
        - 已登录：保留全部线路，从线路1开始
        某集剔除后为空（只有线路1）时回退该集全部线路。
        """
        raw = _BaseCrawler._line_map(html)
        start_sid = 1 if self._is_logged_in() else 2
        for num in raw:
            filtered = [x for x in raw[num] if x[0] >= start_sid]
            if filtered:
                raw[num] = filtered
        return raw

    def get_episode_count(self, target_tab):
        links = self._episode_ul_links(target_tab)
        if links:
            return len(links)
        # 兜底：全页正则但按当前详情页主番剧ID过滤（排除页面底部相关推荐）
        try:
            html = target_tab.html or ''
        except Exception:
            html = ''
        play_links = self._filtered_play_links(html)
        if play_links:
            return len(play_links)
        return 1

    def get_episode_list(self, target_tab):
        return None

    def _play_links(self, html):
        """AGE 专用：优先用剧集容器内链接（避免相关动画混入）；无容器时退回主番剧过滤正则"""
        try:
            links = self._episode_ul_links(self.crawler.tab)
            if links:
                return links
        except Exception:
            pass
        return self._filtered_play_links(html)

    def _filtered_play_links(self, html):
        """全页正则兜底：只保留当前详情页主番剧ID的播放链接（排除相关推荐/排行榜混入）"""
        try:
            m = re.search(r'/play/(\d+)/1/\d+', html)
            main_id = m.group(1) if m else ''
        except Exception:
            main_id = ''
        links = super()._play_links(html)
        if main_id:
            links = [x for x in links if '/play/{}/'.format(main_id) in x[0]]
        return links

    # ---------- 主页扫描：全部视频名称 ----------
    def scan_homepage(self, max_items=500):
        """扫描主页所有视频：返回 [(名称, 详情URL)]"""
        self._log('正在扫描主页视频列表...')
        try:
            self.crawler.tab.get('https://www.agedm.io/', timeout=10)
            time.sleep(3)
        except Exception as e:
            self._log(f'  打开主页失败: {str(e)[:60]}')
            return []
        for _ in range(3):
            try:
                self.crawler.tab.run_js('window.scrollTo(0, document.body.scrollHeight);')
            except Exception:
                pass
            time.sleep(1)

        items, seen = [], set()
        # 1) 浏览器 DOM：所有指向 /detail/ 的详情链接，取渲染后文本
        try:
            els = self.crawler.tab.eles('x://a[contains(@href, "/detail/")]')
        except Exception:
            els = []
        for el in els[:max_items]:
            try:
                href = el.attr('href') or ''
                text = (el.text or '').strip()
            except Exception:
                continue
            if not href or not text or len(text) > 60:
                continue
            full = urljoin(self.SITE_URL, href)
            if full in seen:
                continue
            seen.add(full)
            items.append((text[:60], full))
        if items:
            self._log(f'  扫描到 {len(items)} 部视频（DOM）')
            return items

        # 2) 兜底：html 正则
        try:
            html = self.crawler.tab.html or ''
        except Exception:
            html = ''
        for m in _RE_A_DETAIL.finditer(html):
            href = m.group(1).replace('&amp;', '&')
            text = m.group(2).strip()
            if not text or len(text) > 60:
                continue
            full = urljoin(self.SITE_URL, href)
            if full in seen:
                continue
            seen.add(full)
            items.append((text[:60], full))
        self._log(f'  扫描到 {len(items)} 部视频（正则兜底）')
        return items
