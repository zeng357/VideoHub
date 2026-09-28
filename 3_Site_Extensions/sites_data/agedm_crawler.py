# -*- coding: utf-8 -*-
"""agedm_crawler.py: AGE动漫（www.agedm.io）专用爬虫
站点模板：自有模板（详情 /detail/{数字id}，播放 /play/{id}/{sid}/{nid}，搜索 /search?wd=）
播放器：iframe → jx.wuzhoupai.com 云播放器 → <video> 直链（复用父类 iframe 深入）
"""
import time
from urllib.parse import quote, urljoin

from utils import is_normal_url
from donghua_hk_crawler import (
    DonghuaHKCrawler as _BaseCrawler,
    _RE_A_DETAIL, _RE_CAT_NAV, _RE_LOOSE_PLAY,
    _page_episode_num,
)


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
            'https://www.agedm.io/search?wd={}'.format(kw),
            'https://www.agedm.io/search/{}.html'.format(kw),
            'https://www.agedm.io/so/{}.html'.format(kw),
        ]
        for url in candidates:
            self._log(f'[搜索] 尝试: {url}')
            try:
                self.crawler.tab.get(url, timeout=10)
            except Exception as e:
                self._log(f'  打开失败: {str(e)[:60]}')
                continue
            title = self._page_title(self.crawler.tab)
            self._log(f'  页面标题: {title}')
            is_search_page = ('搜索' in title or '/search' in url.lower())
            detail = self._wait_detail_link(keyword=name, timeout=6, relax=is_search_page)
            if detail:
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
