# -*- coding: utf-8 -*-
# 樱花动漫（anime.donghua.hk 官方门户）专用爬虫
# 站点模板：苹果CMS 系（/search/{kw}.html 搜索、/show/ 详情、/play/ 播放）
# 自适应多 URL 变体 + 多策略提取视频源（m3u8 / mp4 / <video> / iframe 深入）
import re
import time
from urllib.parse import quote, urljoin, urlparse

import requests

from utils import is_normal_url

# 苹果CMS 系详情/播放链接多格式（新旧模板全覆盖，含 video-detail/video-play slug 格式）
_RE_DETAIL = re.compile(
    r'href="([^"]*(?:/show/|/detail/|/vod/detail/|/video-detail/|/vodk?/|/vod/\d+\.html|'
    r'/index\.php/vod/show/|/index\.php/vod/detail/|/vod-detail-|\?m=vod-detail)[^"]*)"')
_RE_A_DETAIL = re.compile(
    r'<a[^>]+href="([^"]*(?:/show/|/detail/|/vod/detail/|/video-detail/|/vodk?/|/vod/\d+\.html|'
    r'/index\.php/vod/show/|/index\.php/vod/detail/|/vod-detail-|\?m=vod-detail)[^"]*)"[^>]*>([^<]{0,40})</a>',
    re.S)
_RE_A_BLOCK = re.compile(
    r'<a[^>]*href="([^"]*(?:/show/|/detail/|/vod/detail/|/video-detail/|/vodk?/|/vod/\d+\.html|'
    r'/index\.php/vod/show/|/index\.php/vod/detail/|/vod-detail-|\?m=vod-detail)[^"]*)"[^>]*>(.*?)</a>',
    re.S)
_RE_PLAY = re.compile(
    r'href="([^"]*(?:/play/|/vodplay/|/video-play/|/vodk-play/|/playk/|/vodk-/|/vodp/|/vod/play/|'
    r'/index\.php/vod/play/|/vod-play-|\?m=vod-play)[^"]*)"')
# 宽松播放兜底：苹果CMS 播放列表 <a href="...html">第N集</a>
_RE_LOOSE_PLAY = re.compile(
    r'<a[^>]+href="([^"]+\.html)"[^>]*>([^<]{0,40})</a>', re.I | re.S)


# 模拟人手：随机延时（可被 COLLECT_GAP=0 禁用，不影响自测）
_UA_POOL = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
]
_UA_IDX = [0]


def _human_ua():
    """轮换 UA（模拟不同浏览器）"""
    _UA_IDX[0] = (_UA_IDX[0] + 1) % len(_UA_POOL)
    return _UA_POOL[_UA_IDX[0]]


def _human_delay(base, spread=0.0):
    """人类随机延时：base + 随机[0, spread] 秒（模拟阅读/点击间隔）"""
    if base <= 0 and spread <= 0:
        return
    import time, random
    time.sleep(base + random.uniform(0, spread or 0.5))


def _human_scroll(tab):
    """模拟人手滚动页面（播放器/列表页滚动到底部再回中）"""
    try:
        tab.run_js('window.scrollTo(0, document.body.scrollHeight * 0.7);')
        _human_delay(0.6, 0.6)
        tab.run_js('window.scrollTo(0, document.body.scrollHeight);')
        _human_delay(0.6, 0.6)
        tab.run_js('window.scrollTo(0, 0);')
    except Exception:
        pass


def _BROWSER_HEADERS(referer=None):
    """完整浏览器请求头（模拟真实浏览器，降低风控特征）"""
    headers = {
        'User-Agent': _human_ua(),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'same-origin',
        'Sec-Fetch-User': '?1',
        'Sec-Ch-Ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        'Sec-Ch-Ua-Mobile': '?0',
        'Sec-Ch-Ua-Platform': '"Windows"',
    }
    if referer:
        headers['Referer'] = referer
    return headers


def _IS_JS_SHELL(html):
    """检测 JS 动态播放器壳页面：requests 拿不到直链（需浏览器执行JS后取源）"""
    low = html.lower()
    markers = (
        'artplayer', 'flv.min.js', 'hls.min.js', '云播放器', 'cloudplayer',
        'api.php', 'dplayer', 'nplayer', 'jwplayer', 'clappr',
    )
    # 有直链特征 → 不是壳（仍可尝试解析）
    if any(k in low for k in ('.m3u8', '.mp4', '<video')):
        if '.m3u8' in low or '.mp4' in low:
            return False
    return any(k in low for k in markers)


def _AD_LIKE(url):
    """广告/海报/占位视频判定（adposter.mp4 等，误抓会导致下载广告）"""
    low = url.lower()
    bad = ('adposter', 'advert', 'banner', 'poster', 'loading', 'sponsor',
           'promo', 'adplay', 'ad.mp4', 'ad_', '/ads/', 'preview', 'tvc_')
    return any(k in low for k in bad)


re.compile(
    r'<a[^>]+href="([^"]*?\.html[^"]*)"[^>]*>\s*(第\s*[\d.]+\s*集[^<]{0,10})</a>', re.I)
_RE_CAT_NAV = re.compile(
    r'<a[^>]+href="([^"]*(?:/vodtype/|/list/|/type/|/index\.php/vod/type/)[^"]*)"[^>]*>([^<]{0,12})</a>',
    re.S)
_RE_M3U8 = re.compile(r'https?://[^"\'<>\\\s]+?\.m3u8[^"\'<>\\\s]*', re.I)
_RE_MP4 = re.compile(r'https?://[^"\'<>\\\s]+?\.mp4[^"\'<>\\\s]*', re.I)
_RE_ESC_M3U8 = re.compile(r'https?:\\/\\/[^"\'<>\\\s]+?\\.m3u8[^"\'<>\\\s]*', re.I)
_RE_ESC_MP4 = re.compile(r'https?:\\/\\/[^"\'<>\\\s]+?\\.mp4[^"\'<>\\\s]*', re.I)
_RE_VIDEO_SRC = re.compile(r'<video[^>]+src=["\']([^"\']+)["\']', re.I)
_RE_IFRAME = re.compile(r'<iframe[^>]+src=["\']([^"\']+)["\']', re.I)
_RE_PLAYER_JSON = re.compile(r'player_[\w]+["\']?\s*[:=]\s*(\{.*?\})', re.S)


def _deesc(s):
    """还原 JS 转义 URL（\/ → /）"""
    return s.replace('\\/', '/').replace('\\\\/', '/')


def _page_episode_num(text):
    """从“第1集 / 第418-2集 / 第12.5集”等文本提取集数数字"""
    m = re.search(r'第\s*(\d+(?:\.\d+)?)\s*集', str(text or ''))
    if m:
        try:
            return int(float(m.group(1)))
        except Exception:
            return int(float(m.group(1).split('.')[0])) if m.group(1).split('.')[0].isdigit() else 0
    return 0


def _host_of(url):
    try:
        return urlparse(url).netloc
    except Exception:
        return ''


class DonghuaHKCrawler:
    """樱花动漫：搜索 → 详情页收集剧集播放链接 → 逐集解析视频源"""

    SITE_NAME = '樱花动漫'
    SITE_URL = 'https://anime.donghua.hk/'
    REQUIRES_LOGIN = False
    NEEDS_BROWSER = True

    CONFIG = {
        'site_url': 'https://anime.donghua.hk/',
        'direct_connect': False,
    }

    def __init__(self, crawler):
        self.crawler = crawler
        self._site_host = _host_of(self.SITE_URL)

    def _log(self, msg):
        """向 GUI 日志通道输出（无则静默）"""
        try:
            cb = getattr(self.crawler, 'log_callback', None)
            if cb:
                cb(msg)
        except Exception:
            pass

    # 名称中文化：动漫常见英文词汇 → 中文（无法翻译的保留原文）
    _CN_WORD_MAP = [
        ('剧场版', '剧场版'), ('movie', '剧场版'), ('Movie', '剧场版'),
        ('season', '季'), ('Season', '季'), ('S1', '第1季'), ('S2', '第2季'),
        ('S3', '第3季'), ('S4', '第4季'), ('S5', '第5季'),
        ('part', '篇'), ('Part', '篇'),
        ('special', '特别篇'), ('Special', '特别篇'), ('SP', '特别篇'),
        ('ova', '特别篇'), ('Ova', '特别篇'), ('OVA', '特别篇'),
        ('oad', '特别篇'), ('Oad', '特别篇'), ('OAD', '特别篇'),
        ('episode', '集'), ('Episode', '集'), ('EP', '集'),
        ('国语', '国语版'), ('粤语', '粤语版'),
        ('普通话', '普通话版'),
    ]

    @classmethod
    def _clean_cn_name(cls, text):
        """清洗候选名称：去站点后缀、英文常用词转中文、规范化空格"""
        if not text:
            return ''
        s = text.strip()
        # 去掉 "xxx-免费资源 - 免费观看" 等站点尾巴
        s = re.sub(r'[-－]\s*(免费资源|免费观看|在线观看|樱花动漫|AGE动漫).*$', '', s).strip()
        s = re.sub(r'\s*[-－]\s*$', '', s).strip()
        # 常用英文词 → 中文
        for en, cn in cls._CN_WORD_MAP:
            s = re.sub(r'(?<![A-Za-z0-9])' + re.escape(en) + r'(?![A-Za-z0-9])', cn, s)
        # 折叠多余空格
        s = re.sub(r'\s+', ' ', s).strip()
        return s[:60]

    @staticmethod
    def _page_title(tab):
        try:
            t = (tab.title or '').strip()
            return t[:60]
        except Exception:
            return ''

    # ---------- 搜索 ----------
    def search_series(self, name, series_id=None):
        name = name.strip()
        if is_normal_url(name):
            self._log(f'打开链接: {name}')
            self.crawler.tab.get(name, timeout=10)
            time.sleep(4)
            return self.crawler.tab

        # 1) 依次尝试多种搜索 URL 变体（video-search 格式已验证有效，放第一位）
        kw = quote(name)
        candidates = [
            'https://anime.donghua.hk/video-search/-------------.html?wd={}'.format(kw),
            'https://anime.donghua.hk/search/{}.html'.format(kw),
            'https://anime.donghua.hk/index.php/vod/search/wd/{}.html'.format(kw),
            'https://anime.donghua.hk/so/{}.html'.format(kw),
            'https://anime.donghua.hk/?m=vod-search&wd={}'.format(kw),
            'https://anime.donghua.hk/index.php/vod/search.html?wd={}'.format(kw),
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
            # 仅确认是搜索结果页时放宽匹配（避免把主页/404页误判为命中）
            is_search_page = ('搜索' in title
                              or '/search/' in url.lower()
                              or '/video-search/' in url.lower()
                              or '/video-play/' in url.lower()
                              or '在线播放' in title)
            detail = self._wait_detail_link(keyword=name, timeout=4, relax=is_search_page)
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

        # 2) 主页兜底：滚动加载全部热门列表 + 尝试“换一批”后再匹配
        self._log('[兜底] 站内搜索未命中，开始扫描主页...')
        try:
            self.crawler.tab.get('https://anime.donghua.hk/', timeout=10)
            time.sleep(3)
        except Exception:
            pass
        detail = self._scroll_wait_detail(keyword=name, timeout=10, click_more=True)
        if detail:
            self._log(f'  主页命中: {detail}')
            self.crawler.tab.get(detail, timeout=10)
            time.sleep(4)
            return self.crawler.tab
        self._log('  主页未命中')

        # 3) 分类列表页兜底：从主页导航提取分类链接（国产动漫等），滚动匹配
        try:
            html = self.crawler.tab.html or ''
        except Exception:
            html = ''
        cat_links = []
        for m in _RE_CAT_NAV.finditer(html):
            full = urljoin('https://anime.donghua.hk/', m.group(1).replace('&amp;', '&'))
            if full not in cat_links:
                cat_links.append(full)
        self._log(f'[兜底] 发现分类页 {len(cat_links)} 个，逐类扫描...')
        for cat_url in cat_links[:6]:
            self._log(f'  分类: {cat_url}')
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
        self._log('  分类页未命中')

        # 4) 全站列表页兜底（/all.html 等）
        for list_url in ('https://anime.donghua.hk/all.html',
                         'https://anime.donghua.hk/index.php/vod/show/'):
            self._log(f'[兜底] 全站列表: {list_url}')
            try:
                self.crawler.tab.get(list_url, timeout=10)
                time.sleep(2)
            except Exception:
                continue
            detail = self._scroll_wait_detail(keyword=name, timeout=8)
            if detail:
                self._log(f'  列表命中: {detail}')
                self.crawler.tab.get(detail, timeout=10)
                time.sleep(4)
                return self.crawler.tab

        raise ValueError(
            f"未在樱花动漫找到「{name}」的剧集。请确认名称，或直接粘贴详情页/播放页链接")

    def _scroll_wait_detail(self, keyword=None, timeout=15, click_more=False):
        """滚动页面底部触发懒加载 + 可选点击“换一批/加载更多”，期间轮询查找详情链接"""
        deadline = time.time() + timeout
        kw = keyword.lower() if keyword else None
        scrolled = 0
        while time.time() < deadline:
            try:
                html = self.crawler.tab.html or ''
            except Exception:
                html = ''
            hit = self._match_detail(html, kw)
            if hit:
                return hit
            scrolled += 1
            # 滚动到底，触发懒加载
            try:
                self.crawler.tab.scroll.to_bottom()
            except Exception:
                try:
                    self.crawler.tab.run_js('window.scrollTo(0, document.body.scrollHeight)')
                except Exception:
                    pass
            # 每轮点击“换一批/加载更多”类按钮
            if click_more and scrolled % 2 == 0:
                try:
                    btns = self.crawler.tab.eles('tag:button', timeout=2)
                    for b in btns[:5]:
                        try:
                            t = (b.text or '').strip()
                        except Exception:
                            continue
                        if any(k in t for k in ('换一批', '加载更多', '展开', '查看更多')):
                            b.click()
                            break
                except Exception:
                    pass
            time.sleep(1.5)
        return None

    def _match_detail(self, html, kw, relax=False):
        hits = []
        seen_href = set()
        # 块级提取：兼容苹果CMS 卡片式结构（名称在 <img alt> 或块内文本）
        for m in _RE_A_BLOCK.finditer(html):
            href = m.group(1).replace('&amp;', '&')
            full = urljoin(self.SITE_URL, href)
            if full in seen_href:
                continue
            seen_href.add(full)
            block = m.group(2)
            am = re.search(r'alt="([^"]{2,60})"', block)
            if am and am.group(1).strip():
                text = am.group(1).strip()
            else:
                text = re.sub(r'<[^>]+>', ' ', block)
                text = re.sub(r'\s+', ' ', text).strip()[:40]
            hits.append((full, text))
        if not hits:
            # 旧式内联文本结构兜底
            for m in _RE_A_DETAIL.finditer(html):
                href = m.group(1).replace('&amp;', '&')
                text = (m.group(2) or '').strip()
                full = urljoin(self.SITE_URL, href)
                hits.append((full, text))
        if not hits:
            seen = set()
            for m in _RE_DETAIL.finditer(html):
                full = urljoin(self.SITE_URL, m.group(1).replace('&amp;', '&'))
                if full in seen:
                    continue
                seen.add(full)
                hits.append((full, ''))
        if kw:
            for full, text in hits:
                if kw in full.lower() or kw in text.lower():
                    return full
            # 搜索页结果基本都相关：放宽为取第一个详情链接
            if relax and hits:
                return hits[0][0]
        elif hits:
            return hits[0][0]
        return None

    # ---------- 多结果候选选择（通用：分季/篇/多部时让用户在GUI选择） ----------
    def _collect_candidates(self, html, kw, limit=80):
        """收集搜索结果候选 [(名称, 详情URL)]：
        1) 优先限定在搜索结果容器（ul.vodlist/搜索列表）内，避免混入侧边栏推荐；
        2) 同一URL多个链接（封面/名称/状态）聚合后选最佳名称（过滤“更新至第X集/查看详情”等状态文本）；
        3) 关键词命中的候选优先且只弹关键词相关的，无命中时才放宽。
        """
        _BAD_NAME = re.compile(r'^(更新至|连载至|共\d+集|第\d+[集话]|查看详情|立即播放|点击播放|在线播放|免费观看|播放|详情|全集|完结|已完结)')
        _BAD_FULL = re.compile(r'^(大会员|会员|独家|国创|客户端|首页|番剧|直播|游戏中心|漫画|赛事|搜索|综合|影视|专栏|用户|登录|注册|立即观看|资源详情|更多筛选|综合排序|最多播放|最新发布|最多弹幕|最多收藏|下载客户端|全部|选集|选集|追番|关注)')
        scope_htmls = []
        for m in re.finditer(
                r'<ul[^>]*class="[^"]*(?:vodlist|search|module-list|content-list|video-list)[^"]*"[^>]*>(.*?)</ul>',
                html or '', re.S):
            scope_htmls.append(m.group(1))
        if not scope_htmls:
            scope_htmls = [html or '']
        # 按 URL 聚合全部文本
        agg = {}
        for scope in scope_htmls:
            for m in _RE_A_BLOCK.finditer(scope):
                href = m.group(1).replace('&amp;', '&')
                full = urljoin(self.SITE_URL, href)
                block = m.group(2)
                am = re.search(r'alt="([^"]{2,60})"', block)
                if am and am.group(1).strip():
                    text = am.group(1).strip()
                else:
                    text = re.sub(r'<[^>]+>', ' ', block)
                    text = re.sub(r'\s+', ' ', text).strip()[:40]
                text = self._clean_cn_name(text)
                if text:
                    agg.setdefault(full, []).append(text)

        def best_text(texts):
            if not texts:
                return ''
            if kw:
                for tx in texts:
                    if kw in tx and not _BAD_FULL.search(tx):
                        return tx
            good = [tx for tx in texts if not _BAD_NAME.search(tx) and not _BAD_FULL.search(tx)]
            if good:
                return max(good, key=len)[:60]
            meaningful = [tx for tx in texts if not _BAD_FULL.search(tx)]
            if meaningful:
                return max(meaningful, key=len)[:60]
            return ''

        items = [(best_text(texts), full) for full, texts in agg.items()]
        items = [(n or '（未命名）', u) for n, u in items if n]
        if not items:
            return []
        # 关键词命中的候选排前；有命中时只保留命中项（避免弹窗混入无关推荐）
        if kw:
            matched = [(n, u) for n, u in items if kw in n or kw in u.lower()]
            if matched:
                items = matched
        # 名称去重
        dedup, seen_n = [], set()
        for n, u in items:
            if n in seen_n:
                continue
            seen_n.add(n)
            dedup.append((n, u))
        return dedup[:limit]

    def _resolve_candidates(self, candidates, kw=''):
        """候选>1 时调用 choose_candidate 回调（GUI 选择窗）返回选中URL；否则返回第一个"""
        if not candidates:
            return None
        if len(candidates) <= 1:
            return candidates[0][1]
        crawler_obj = getattr(self, 'crawler', None)
        chooser = (getattr(crawler_obj, 'choose_candidate', None)
                   if crawler_obj is not None else None) or getattr(self, 'choose_candidate', None)
        if chooser:
            try:
                picked = chooser(candidates)
                if picked:
                    return picked
            except Exception as e:
                self._log(f'  候选选择异常({str(e)[:40]})，取第一个')
        return candidates[0][1]

    def _wait_detail_link(self, keyword=None, timeout=8, relax=False):
        """轮询等待页面出现详情链接；relax=True 时无关键词命中也取第一个（搜索页场景）"""
        deadline = time.time() + timeout
        kw = keyword.lower() if keyword else None
        while time.time() < deadline:
            try:
                html = self.crawler.tab.html or ''
            except Exception:
                html = ''
            hit = self._match_detail(html, kw, relax=relax)
            if hit:
                return hit
            time.sleep(2)
        return None

    # ---------- 集数 ----------
    def get_episode_count(self, target_tab):
        try:
            html = target_tab.html or ''
        except Exception:
            html = ''
        links = self._play_links(html)
        if links:
            return len(links)
        # 当前可能已是播放页 → 视为 1 集
        return 1

    def get_episode_list(self, target_tab):
        return None

    @staticmethod
    def _line_map(html):
        """解析播放链接 → {集数: [(线路号, href), ...]}（按线路号升序），保留全部线路"
        """
        items = []
        for m in _RE_PLAY.finditer(html):
            full = m.group(1).replace('&amp;', '&')
            if full.split('?')[0].endswith('.html'):
                nums = re.findall(r'-(\d+)-(\d+)\.html$', full.split('?')[0])
                if nums:
                    src, num = int(nums[-1][0]), int(nums[-1][1])
                    items.append((num, src, full))
                    continue
                nums2 = re.findall(r'(\d+)\.html$', full.split('?')[0])
                if nums2:
                    items.append((int(nums2[-1]), 99, full))
            else:
                nums3 = re.findall(r'/(\d+)/(\d+)$', full.split('?')[0])
                if nums3:
                    sid, nid = int(nums3[-1][0]), int(nums3[-1][1])
                    items.append((nid, sid, full))
                    continue
                nums4 = re.findall(r'/(\d+)$', full.split('?')[0])
                if nums4:
                    items.append((int(nums4[-1]), 99, full))
        lines = {}
        for num, sid, full in items:
            lines.setdefault(num, []).append((sid, full))
        for num in lines:
            lines[num].sort(key=lambda x: x[0])  # 线路号小优先
        return lines

    @staticmethod
    def _play_links(html):
        """收集页面里的播放链接：按集数去重，优先保留线路1（苹果CMS多线路页面）"""
        lines = DonghuaHKCrawler._line_map(html)
        links = []
        for num in sorted(lines):
            links.append((lines[num][0][1], num))
        return links

    # ---------- 收集 ----------
    def collect_episode_videos(self, target_tab, episode_start=1, episode_end=0,
                               max_threads=5, progress_callback=None, keyword=None):
        """收集各集视频地址：详情页 → 逐集打开播放页 → 提取视频源"""
        try:
            html = target_tab.html or ''
        except Exception:
            html = ''
        referer = None
        try:
            referer = target_tab.url or self.SITE_URL
        except Exception:
            referer = self.SITE_URL

        # 若当前已是播放页，直接解析单集（宽松播放列表命中也算有剧集列表）
        if not _RE_PLAY.search(html) and not _RE_LOOSE_PLAY.search(html):
            ep = self._extract_one(target_tab, 1, referer)
            if ep:
                if progress_callback:
                    progress_callback()
                return [ep]
            raise RuntimeError("未能解析到该集的视频源（可能播放器为第三方/需登录，或页面未加载完）")

        links = self._play_links(html)
        if not links:
            raise RuntimeError("未找到剧集播放链接")
        total = len(links)
        start = max(episode_start, 1)
        end = min(episode_end, total) if episode_end > 0 else total
        items = links[start - 1:end]
        want = end - start + 1
        # 全线路表：{集数: [(线路, href)...]}，供多线路切换
        try:
            line_map = self._line_map(html)
        except Exception:
            line_map = {}

        # 1) 极速通道：requests 并发批量抓播放页解析（不逐级开浏览器，弱站限流保护）
        workers = getattr(self, 'COLLECT_WORKERS', 4)
        self._log(f'[收集] 共 {want} 集，并发解析中（{workers}线程×多线路切换）...')
        eps = self._bulk_collect(items, referer, progress_callback, line_map=line_map)
        got = {e['episode_num'] for e in eps}
        missing = [(link, num) for link, num in items if num not in got]

        # 2) 失败项回退：浏览器逐页打开（逐线路尝试）
        errs = []
        _stop_fn = None
        try:
            _stop_fn = getattr(self.crawler, 'stop_collect', None)
        except Exception:
            _stop_fn = None
        for link, num in missing:
            if _stop_fn is not None and _stop_fn():
                self._log(f'[收集] 停止回退流程，已保留 {len(eps)} 集...')
                break
            hrefs = line_map.get(num) or [(99, link)]
            ep = None
            used = []
            for _sid, _href in hrefs:
                full = urljoin(self.SITE_URL, _href)
                used.append(_sid)
                opened = False
                for _retry in range(2):  # 弱站偶发空响应：浏览器兜底也重试一次
                    try:
                        _human_delay(3.5, 2.5)   # 模拟人手逐个点击
                        self.crawler.tab.get(full, timeout=10)
                        _human_delay(2.5, 2.0)   # 等待页面加载（人手阅读感）
                        _human_scroll(self.crawler.tab)
                        opened = True
                        break
                    except Exception as e:
                        _human_delay(2.5, 2.5)
                if not opened:
                    continue
                ep = self._extract_one(self.crawler.tab, num, full)
                if ep:
                    if len(hrefs) > 1 and used[0] != _sid:
                        self._log(f'  第{num}集 线路{used[0]}失败→线路{_sid}成功')
                    break
            if ep:
                eps.append(ep)
                if progress_callback:
                    progress_callback()
            else:
                errs.append(f'第{num}集: 未解析到视频源（已试线路 {"、".join(map(str, used))}）')

        if eps:
            eps.sort(key=lambda e: e['episode_num'])  # 并发/回退混合后按集数归位
            if errs:
                # 部分成功：返回成功集，失败的记入日志（GUI 只加载成功集，不再整体报错）
                self._log(f'[收集] 完成: {len(eps)}/{end - start + 1} 集（'
                          f'已跳过: {"; ".join(errs[:5])}）')
            else:
                self._log(f'[收集] 完成: {len(eps)}/{end - start + 1} 集全部解析成功')
            return eps
        raise RuntimeError("全部剧集未解析到视频源: " + '; '.join(errs[:3]))

    # ---------- 并发批量收集（requests 极速通道） ----------
    def _bulk_collect(self, items, referer, progress_callback=None, max_workers=4,
                      line_map=None):
        """并发 requests 抓取播放页并解析视频源；每集多线路依次尝试；弱站限流友好"""
        import concurrent.futures as cf
        import time as _time
        headers = _BROWSER_HEADERS(referer)
        line_map = line_map or {}
        _max = max(1, min(max_workers, getattr(self, 'COLLECT_WORKERS', 4)))  # 弱站保护：并发不超过 4（可覆写更低）
        _gap = getattr(self, 'COLLECT_GAP', 0.0)  # 每请求间隔（降速）

        def one(item):
            link, num = item
            hrefs = line_map.get(num) or [(99, link)]
            last = None
            for _sid, _href in hrefs:
                if _gap:
                    _time.sleep(_gap)
                full = urljoin(self.SITE_URL, _href)
                for attempt in range(2):  # 线路内轻量重试1次（瞬时空响应容错，不滥用请求）
                    try:
                        r = requests.get(full, headers=headers, timeout=8)
                        if r.status_code == 200 and r.text and r.text.strip():
                            ep = self._extract_from_html(r.text, num, full)
                            if ep:
                                if len(hrefs) > 1 and _sid != hrefs[0][0]:
                                    self._log(f'  第{num}集 线路{hrefs[0][0]}失败→线路{_sid}成功')
                                return ep
                        last = 'HTTP %s / 空响应' % r.status_code
                    except Exception as e:
                        last = str(e)[:50]
                    _time.sleep(0.6 * (attempt + 1))
                if len(hrefs) > 1:
                    self._log(f'  第{num}集 线路{_sid}不可用，切换下一线路...')
            self._log(f'  [收集] 第{num}集请求失败({last})，稍后浏览器兜底')
            return None

        eps = []
        stop_fn = None
        try:
            stop_fn = getattr(self.crawler, 'stop_collect', None)
        except Exception:
            stop_fn = None
        # 不用 with 块：停止时 shutdown(wait=False) 立即返回，不等正在跑的worker，
        # 避免「点了停止要等几十秒才生效」的卡顿感（后台worker跑完结果丢弃，无害）
        ex = cf.ThreadPoolExecutor(max_workers=_max)
        try:
            futures = {ex.submit(one, it): it for it in items}
            while futures:
                if stop_fn is not None and stop_fn():
                    self._log(f'[收集] 收到停止指令，保留已完成的 {len(eps)} 集...')
                    for f in list(futures):
                        f.cancel()
                    break
                try:
                    f = next(cf.as_completed(futures, timeout=0.5))
                except StopIteration:
                    break
                except cf.TimeoutError:
                    continue
                futures.pop(f, None)
                ep = f.result()
                if ep:
                    eps.append(ep)
                    if progress_callback:
                        progress_callback()  # 仅成功计一次，避免与浏览器兜底重复计数
        finally:
            try:
                ex.shutdown(wait=False)
            except Exception:
                pass
        return eps

    # ---------- 单集视频源提取 ----------
    def _extract_one(self, tab, num, referer):
        """打开播放页后在渲染后的 DOM 里找视频源（轮询等待播放器）"""
        for _ in range(5):
            try:
                html = tab.html or ''
            except Exception:
                html = ''
            ep = self._extract_from_html(html, num, referer, use_tab=tab)
            if ep:
                return ep
            _human_delay(2.0, 1.5)
        return None

    def _make_ep(self, num, src, referer):
        vtype = 'hls' if '.m3u8' in src.lower() else 'direct'
        return {
            'episode_num': num,
            'title': f'第{num}话',
            'video_url': src,
            'audio_url': None,
            'video_type': vtype,
            'referer': referer or self.SITE_URL,
        }

    def _extract_from_html(self, html, num, referer, use_tab=None):
        """从播放页 HTML 提取视频源；iframe 播放器按有无浏览器分别深入"""
        src = self._parse_video_src(html)
        if src:
            return self._make_ep(num, src, referer)
        iframe = self._find_iframe(html)
        if iframe:
            if use_tab is not None:
                src2 = self._resolve_iframe(iframe, referer)      # 浏览器渲染后深入
            else:
                src2 = self._resolve_iframe_http(iframe, referer)  # requests 直接深入
            if src2:
                return self._make_ep(num, src2, referer)
        return None

    def _resolve_iframe_http(self, iframe_src, referer, depth=0):
        """用 requests 打开 iframe 播放器页再解析（最多嵌套 3 层）"""
        if depth > 3:
            return None
        full = urljoin(referer or self.SITE_URL, iframe_src)
        headers = _BROWSER_HEADERS(referer)
        import time as _t
        for _try in range(2):
            try:
                r = requests.get(full, headers=headers, timeout=5)
                html = r.text or ''
            except Exception:
                html = ''
            if html and len(html) > 300:
                # JS 动态播放器壳（artplayer/flv/hls 加载器/云播放器/Api.php）：
                # 纯 requests 拿不到直链，立即放弃转浏览器兜底，不浪费时间
                if _IS_JS_SHELL(html):
                    return None
                src = self._parse_video_src(html)
                if src:
                    return src
                inner = self._find_iframe(html)
                if inner:
                    return self._resolve_iframe_http(inner, full, depth + 1)
            _t.sleep(0.5 * (_try + 1))
        return None

    @staticmethod
    def _parse_video_src(html):
        """多种策略提取视频 URL"""
        # 1) player_data JSON 里的 url
        pm = _RE_PLAYER_JSON.search(html)
        if pm:
            body = pm.group(1)
            um = re.search(r'["\']url["\']\s*:\s*["\']([^"\']+)["\']', body)
            if um:
                u = _deesc(um.group(1))
                if u.startswith('http') and ('.m3u8' in u.lower() or '.mp4' in u.lower()):
                    return u
            # encrypt=0 且 url 为 http 明文（非 vid）：直接返回
            fm = re.search(r'["\']url["\']\s*:\s*["\'](https?://[^"\']+)["\']', body)
            if fm:
                u = _deesc(fm.group(1))
                if '.m3u8' in u.lower() or '.mp4' in u.lower():
                    return u
        # 2) <video> 标签 src：播放器最终渲染的正片，最可靠（先于明文正则，避免误抓广告）
        vm = _RE_VIDEO_SRC.search(html)
        if vm:
            u = _deesc(vm.group(1))
            if u and not _AD_LIKE(u):
                return u
        # 3) 明文 m3u8 / mp4（过滤广告/海报/统计链接）
        cands = []
        for m in _RE_M3U8.finditer(html):
            cands.append(m.group(0))
        for m in _RE_MP4.finditer(html):
            cands.append(m.group(0))
        for m in _RE_ESC_M3U8.finditer(html):
            cands.append(_deesc(m.group(0)))
        for m in _RE_ESC_MP4.finditer(html):
            cands.append(_deesc(m.group(0)))
        if cands:
            good = [u for u in cands
                    if not any(k in u.lower() for k in ('.js', '.css', '.png', '.jpg', '.gif'))
                    and not _AD_LIKE(u)]
            if good:
                return good[0]
        return None

    @staticmethod
    def _find_iframe(html):
        """选择播放器 iframe：跳过 prestrain/static 占位，优先含 api/player/jx/play 关键字，否则取最后一个"""
        frames = _RE_IFRAME.findall(html)
        if not frames:
            return None
        frames = [f.replace('&amp;', '&') for f in frames]
        # 占位页（预加载/广告/统计）直接排除
        skip_kw = ('prestrain', '/static/', 'beacon', 'statistics', 'ads', 'ad.js')
        cands = [f for f in frames if not any(k in f.lower() for k in skip_kw)]
        if not cands:
            return None
        # 播放器关键字优先
        play_kw = ('api.', 'player', 'jx', '/play', 'yun', 'ifr')
        for f in cands:
            if any(k in f.lower() for k in play_kw):
                return f
        return cands[-1]

    def _resolve_iframe(self, iframe_src, referer):
        """打开 iframe 播放器页再解析视频源（轮询）"""
        full = urljoin(referer or self.SITE_URL, iframe_src)
        _human_delay(2.0, 2.0)  # 模拟人手点击播放器前停顿
        try:
            self.crawler.tab.get(full, timeout=10)
            _human_delay(2.5, 2.0)
            _human_scroll(self.crawler.tab)
        except Exception:
            return None
        for _ in range(8):
            try:
                html = self.crawler.tab.html or ''
            except Exception:
                html = ''
            src = self._parse_video_src(html)
            if src:
                return src
            # iframe 套 iframe（部分播放器多级嵌套）
            inner = self._find_iframe(html)
            if inner:
                return self._resolve_iframe(inner, full)
            _human_delay(2.0, 1.5)
        return None
