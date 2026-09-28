# -*- coding: utf-8 -*-
# 华丽装饰（aa.hualizhuangshi.com 樱花动漫镜像站）专用爬虫
# 继承樱花动漫插件能力，差异点：
#   1) 站点域名/名称
#   2) 详情链接 /vodk/数字.html（已在父类正则中通用化）
#   3) 搜索 URL 用 /vodsearch/-------------.html?wd= 变体
#   4) 新增 scan_homepage()：扫描主页全部视频名称（供“主页扫描→勾选→下载”交互）
#   5) 播放链接格式未知时走宽松兜底（“第N集”文本 + .html 链接）
import re
import time
from urllib.parse import quote, urljoin

from utils import is_normal_url
from donghua_hk_crawler import (
    DonghuaHKCrawler as _BaseCrawler,
    _RE_A_DETAIL, _RE_CAT_NAV, _RE_LOOSE_PLAY,
    _page_episode_num,
)


class HualizhuangshiCrawler(_BaseCrawler):
    SITE_NAME = '华丽装饰'
    SITE_URL = 'http://aa.hualizhuangshi.com/'
    REQUIRES_LOGIN = False
    NEEDS_BROWSER = True

    CONFIG = {
        'site_url': 'http://aa.hualizhuangshi.com/',
        'direct_connect': False,
    }

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
            'http://aa.hualizhuangshi.com/vodsearch/{0}-------------.html'.format(kw),
            'http://aa.hualizhuangshi.com/vodsearch/-------------.html?wd={0}'.format(kw),
            'http://aa.hualizhuangshi.com/video-search/-------------.html?wd={0}'.format(kw),
            'http://aa.hualizhuangshi.com/search/{0}.html'.format(kw),
            'http://aa.hualizhuangshi.com/index.php/vod/search/wd/{0}.html'.format(kw),
            'http://aa.hualizhuangshi.com/so/{0}.html'.format(kw),
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
            is_search_page = ('搜索' in title or '/search/' in url.lower()
                              or '/vodsearch/' in url.lower()
                              or '/video-search/' in url.lower())
            detail = self._wait_detail_link(keyword=name, timeout=4, relax=is_search_page)
            if detail:
                self._log(f'  搜索命中: {detail}')
                self.crawler.tab.get(detail, timeout=10)
                time.sleep(4)
                return self.crawler.tab
            self._log('  该路径无匹配结果')

        # 主页兜底
        self._log('[兜底] 站内搜索未命中，扫描主页...')
        try:
            self.crawler.tab.get('http://aa.hualizhuangshi.com/', timeout=10)
            time.sleep(3)
        except Exception:
            pass
        detail = self._scroll_wait_detail(keyword=name, timeout=10, click_more=True)
        if detail:
            self._log(f'  主页命中: {detail}')
            self.crawler.tab.get(detail, timeout=10)
            time.sleep(4)
            return self.crawler.tab

        # 分类兜底
        try:
            html = self.crawler.tab.html or ''
        except Exception:
            html = ''
        cat_links = []
        for m in _RE_CAT_NAV.finditer(html):
            full = urljoin(self.SITE_URL, m.group(1).replace('&amp;', '&'))
            if full not in cat_links:
                cat_links.append(full)
        for cat_url in cat_links[:6]:
            try:
                self.crawler.tab.get(cat_url, timeout=10)
                time.sleep(2)
            except Exception:
                continue
            detail = self._scroll_wait_detail(keyword=name, timeout=8)
            if detail:
                self._log(f'  分类命中: {detail}')
                self.crawler.tab.get(detail, timeout=10)
                time.sleep(4)
                return self.crawler.tab

        raise ValueError(
            f"未在「{name}」找到剧集。请确认名称，或直接粘贴详情页/播放页链接")

    # ---------- 主页扫描：全部视频名称 ----------
    def scan_homepage(self, max_items=500):
        """扫描主页所有视频：返回 [(名称, 详情URL)]"""
        self._log('正在扫描主页视频列表...')
        try:
            self.crawler.tab.get('http://aa.hualizhuangshi.com/', timeout=10)
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
        # 1) 浏览器 DOM：所有指向 /vodk/ 的详情链接，取渲染后文本（含嵌套标题）
        try:
            els = self.crawler.tab.eles('x://a[contains(@href, "/vodk/")]')
        except Exception:
            els = []
        for el in els[:max_items]:
            try:
                href = el.attr('href') or ''
                text = (el.text or '').strip()
            except Exception:
                continue
            if not href or not text:
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
            if not text:
                continue
            full = urljoin(self.SITE_URL, href)
            if full in seen:
                continue
            seen.add(full)
            items.append((text[:60], full))
        self._log(f'  扫描到 {len(items)} 部视频（正则兜底）')
        return items

    # ---------- 播放链接：优先父类多格式，空则宽松兜底 ----------
    @staticmethod
    def _play_links(html):
        links = _BaseCrawler._play_links(html)
        if links:
            return links
        items = []
        for m in _RE_LOOSE_PLAY.finditer(html):
            href = m.group(1).replace('&amp;', '&')
            text = m.group(2) or ''
            num = _page_episode_num(text)
            if not num:
                nums = re.findall(r'(\d+)\.html$', href.split('?')[0])
                num = int(nums[-1]) if nums else 0
            if num:
                items.append((num, 99, href))
        items.sort(key=lambda x: (x[0], x[1]))
        links, seen = [], set()
        for num, _src, href in items:
            if num in seen:
                continue
            seen.add(num)
            links.append((href, num))
        return links
