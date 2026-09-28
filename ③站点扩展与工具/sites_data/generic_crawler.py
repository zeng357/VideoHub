# 自动扫描爬虫 - 内置通用模式，无需站点适配即可扫描任意网页中的视频
#
# 工作方式（"地址" 与 "搜索方法" 解耦）：
#   给定任意入口地址（视频页 / 详情页 / 列表页 / 搜索页）：
#   1. 打开页面先滚动到底，触发懒加载，确保列表/剧集链接完整渲染
#   2. 当前页直接提取视频（m3u8脚本地址 → video/source标签 → iframe嵌套 → 媒体直链）
#   3. 当前页无视频 → 视为列表页，自动收集候选链接（同域名、非静态资源）
#   4. 逐条深入候选页（递归最多4层、总页面上限80页），继续按2、3提取
#   5. 详情页若带视频预览且含"第X集"链接 → 记录预览后继续扫全集（视频按URL去重）
#   6. 可传关键词，在第一层列表仅保留名称/地址含关键词的条目
#   7. 扫描到的所有视频统一返回，由下载流程自动保存
import json
import re
import time
from urllib.parse import quote, urljoin, urlparse

from utils import is_normal_url

_M3U8_RE = re.compile(r'https?://[^\s"\'<>\\]+?\.m3u8[^\s"\'<>\\]*', re.I)
# 播放器脚本常把URL转义写入（https:\/\/... 或 \u002F），需还原后再用
_M3U8_ESCAPED_RE = re.compile(
    r'https?:(?:\\{1,2}/){2}[^"\s<>]+?\.m3u8[^"\s<>]*'
    r'|https?:(?:\\u002F){2}[^"\s<>]+?\.m3u8[^"\s<>]*', re.I)
_MEDIA_EXT_RE = re.compile(r'https?://[^\s"\'<>\\]+?\.(mp4|webm|mkv|mov|flv|avi|m4v)(\?[^\s"\'<>\\]*)?', re.I)
_EPISODE_TITLE_RE = re.compile(
    r'第\s*\d+\s*[集话話期]|第\s*[一二三四五六七八九十百\d]+\s*[集話话]|'
    r'EP\s*\d+|EPISODE\s*\d+|^\d{1,3}\s*[集话話期]', re.I)

MAX_SCAN_DEPTH = 4       # 递归深入层数
MAX_SCAN_PAGES = 80      # 总扫描页面上限
MAX_LINKS_PER_PAGE = 120
MAX_TRAVERSE_LINKS = 40  # 每层最多深入链接数（防导航/推荐链接带跑）
MAX_A_ELEMS = 2000
PLAYER_WAIT = 8          # 打开页面后等待播放器加载出m3u8的最大秒数

_EXPAND_JS = """
var done=false;
document.querySelectorAll('a,button,span,div,p').forEach(function(e){
  if(done) return;
  var t=(e.textContent||'').trim().replace(/\\s+/g,' ');
  var ok = t.indexOf('查看全部')>-1||t.indexOf('展开全部')>-1||t.indexOf('加载更多')>-1
        ||t.indexOf('查看完整')>-1||t.indexOf('展开')>-1;
  if(ok && t.length<12){ try{ e.click(); done=true; }catch(err){} }
});
return done;
"""


def js_click_expand(tab):
    """执行JS点击"查看全部/展开"按钮，返回是否有点击动作"""
    try:
        return bool(tab.run_js(_EXPAND_JS))
    except Exception:
        return False

def _normalize_escaped_url(u):
    """还原播放器脚本里的转义URL（\u002F、\\/、\/ 三种写法）"""
    u = u.replace('\\u002F', '/')
    u = u.replace('\\\\/', '/')
    u = u.replace('\\/', '/')
    return u


# 已知站点的站内搜索地址（关键词将被URL编码替换 {q}）
_SEARCH_URLS = [
    ('acfun.cn', 'https://www.acfun.cn/search?keyword={q}'),
    ('bilibili.com', 'https://search.bilibili.com/all?keyword={q}'),
    ('youku.com', 'https://so.youku.com/search_video/q_{q}'),
    ('iqiyi.com', 'https://so.iqiyi.com/so/q_{q}'),
    ('mgtv.com', 'https://www.mgtv.com/s?k={q}'),
    ('qq.com', 'https://v.qq.com/x/search/?q={q}'),
    ('sohu.com', 'https://search.sohu.com/?keyword={q}'),
    ('youtube.com', 'https://www.youtube.com/results?search_query={q}'),
]


class GenericVideoCrawler:
    """自动扫描 (通用网页/列表页)"""

    SITE_NAME = '自动扫描'
    SITE_URL = ''
    REQUIRES_LOGIN = False
    NEEDS_BROWSER = True

    CONFIG = {
        'site_url': '',
        'locators': {},
        'image_attr': 'src',
    }

    def __init__(self, crawler):
        self.crawler = crawler

    # ---- 契约方法 ----
    def search_series(self, name, series_id=None):
        """name 参数即入口地址（页面/列表/视频链接）"""
        url = name.strip()
        if not is_normal_url(url):
            raise ValueError("请输入有效的 http/https 地址")
        self.crawler.tab.get(url)
        self._log(f"已打开地址: {url}")
        time.sleep(3)
        return self.crawler.tab

    def get_episode_count(self, target_tab):
        return 1

    def get_episode_list(self, target_tab):
        return None

    def collect_episode_videos(self, target_tab, episode_start=1, episode_end=0,
                               max_threads=5, progress_callback=None, keyword=None):
        """自动扫描：从入口页递归查找全部视频

        Returns:
            list[dict]: [{episode_num, title, video_url, video_type, referer}]
        """
        results = []
        visited = set()
        video_seen = set()
        budget = {'n': MAX_SCAN_PAGES}

        try:
            entry_url = target_tab.url or ''
        except Exception:
            entry_url = ''

        # 搜索优先：有关键词时先搜索定位目标页面，再在目标页扫描全集
        if keyword and keyword.strip():
            keyword = keyword.strip()
            found = self._search_locate(target_tab, keyword, entry_url)
            if found:
                self._log(f"已通过搜索定位到目标页: {found}")
                try:
                    target_tab.get(found)
                    time.sleep(3)
                except Exception as e:
                    self._log(f"打开目标页失败: {e}")
                keyword = None  # 已定位到目标页，无需再按关键词过滤
            else:
                self._log("搜索未能定位目标页，改为在入口页按关键词过滤扫描")

        self._log(f"自动扫描: 入口={target_tab.url}, 关键词={keyword or '无'}")
        self._scan_recursive(target_tab, keyword, depth=1,
                             visited=visited, budget=budget, results=results,
                             video_seen=video_seen)

        # 按集数范围过滤
        if episode_end > 0:
            results = [r for r in results if episode_start <= r['episode_num'] <= episode_end]
        else:
            results = [r for r in results if r['episode_num'] >= episode_start]
        for i, r in enumerate(results):
            r['episode_num'] = i + 1
        self._log(f"自动扫描完成: 共找到 {len(results)} 个视频")
        if progress_callback:
            progress_callback()
        return results

    # ---- 递归扫描 ----
    def _scan_recursive(self, tab, keyword, depth, visited, budget, results, video_seen):
        if budget['n'] <= 0 or depth > MAX_SCAN_DEPTH:
            return

        try:
            cur_url = tab.url
        except Exception:
            cur_url = ''
        if cur_url in visited:
            return
        visited.add(cur_url)
        budget['n'] -= 1

        # 滚动触发懒加载，确保列表/剧集链接完整渲染
        self._scroll_to_load(tab)
        # 点击"查看全部/展开全部"，确保折叠的剧集列表全部展开
        self._expand_episode_list(tab)

        # 收集候选链接（第一层用关键词过滤）
        links = self._collect_candidate_links(tab, keyword if depth == 1 else None)

        # 当前页直接提取视频
        vurl, vtype = self._find_video_in_page(tab)
        audio_url = None
        if not vurl:
            # 播放器内嵌JSON（B站 __playinfo__ 等）：视频流+音频流分离
            pj = self._parse_player_json(tab)
            if pj:
                vurl = pj.get('video_url')
                audio_url = pj.get('audio_url')
                vtype = 'dash'
                self._log(f"  解析到播放器音视频流: {str(vurl)[:80]}")
        if vurl:
            key = vurl.split('?')[0]
            # 入口层且有候选链接 → 很可能是"列表页带推荐/预览播放器"：
            #   有关键词时页面自带视频多为推荐内容，不纳入结果，只扫列表；
            #   无关键词时记录自身视频并继续扫列表（自动扫描全部）。
            # 非入口层 → 正常记录（详情页预览或剧集正片）。
            if depth == 1 and links:
                if not keyword and key not in video_seen:
                    video_seen.add(key)
                    results.append({
                        'episode_num': len(results) + 1,
                        'title': self._page_title(tab),
                        'video_url': vurl,
                        'audio_url': audio_url,
                        'video_type': vtype,
                        'referer': cur_url or None,
                    })
                    self._log(f"  发现视频: {vurl[:80]}")
            elif key not in video_seen:
                video_seen.add(key)
                results.append({
                    'episode_num': len(results) + 1,
                    'title': self._page_title(tab),
                    'video_url': vurl,
                    'audio_url': audio_url,
                    'video_type': vtype,
                    'referer': cur_url or None,
                })
                self._log(f"  发现视频: {vurl[:80]}")

            # 是否继续深入：
            #   - 入口层有候选链接 → 必继续（列表页）
            #   - 非入口层 → 仅当疑似剧集列表（详情页带预览 + 多个集数链接）才继续
            if not links:
                return
            if depth == 1:
                self._log(f"  第{depth}层(入口): 收集到 {len(links)} 个候选链接，继续深入...")
            elif self._looks_like_episode_list(links):
                self._log(f"  第{depth}层: 疑似剧集列表({len(links)}个链接)，继续深入...")
                # 剧集列表页只跟进"像剧集"的链接，避免被导航/推荐链接带跑
                ep_links = [l for l in links if self._is_episode_link(l)]
                if len(ep_links) >= 2:
                    links = ep_links
                    self._log(f"  第{depth}层: 筛出 {len(links)} 个剧集链接深入")
            else:
                return
        else:
            if not links:
                return
            self._log(f"  第{depth}层: 收集到 {len(links)} 个候选链接，继续深入...")

        # 每层深入上限，防止无意义的导航/推荐链接消耗预算
        if len(links) > MAX_TRAVERSE_LINKS:
            links = links[:MAX_TRAVERSE_LINKS]

        for link in links:
            if budget['n'] <= 0:
                break
            try:
                tab.get(link['url'])
                time.sleep(2)
            except Exception as e:
                self._log(f"  打开候选页失败: {e}")
                continue
            self._scan_recursive(tab, keyword, depth + 1, visited, budget, results, video_seen)

    # ---- 搜索定位 ----
    def _search_locate(self, tab, keyword, entry_url):
        """通过站内搜索（未知站点用Bing兜底）定位目标页面，返回最优链接或 None"""
        domain = ''
        try:
            domain = urlparse(entry_url).netloc.lower().replace('www.', '')
        except Exception:
            pass
        q = quote(keyword)
        search_url = None
        if domain:
            for dom, tpl in _SEARCH_URLS:
                if dom in domain:
                    search_url = tpl.format(q=q)
                    break
        if not search_url:
            search_url = f'https://cn.bing.com/search?q={q}'
        self._log(f"正在搜索定位: {search_url}")
        try:
            tab.get(search_url)
            time.sleep(3)
        except Exception as e:
            self._log(f"搜索页打开失败: {e}")
            return None

        links = self._collect_search_result_links(tab)
        if not links:
            return None
        kw = keyword.lower()
        search_host = urlparse(search_url).netloc.lower().replace('www.', '')
        best, best_score = None, 0
        for l in links:
            u, t = l['url'], (l['title'] or '').lower()
            score = 0
            if kw in t:
                score += 3
            if kw in u.lower():
                score += 3
            if domain and domain in u.lower():
                score += 2
            # 番剧/详情页优先（搜索页常同时混有用户视频与番剧条目）
            if re.search(r'/(bangumi)/', u.lower()):
                score += 3
            elif re.search(r'/(video|play|watch|detail|show|vod|episode|item|post)/', u.lower()):
                score += 2
            try:
                host = urlparse(u).netloc.lower().replace('www.', '')
                if host and host != search_host and 'bing.com' not in host:
                    score += 1
            except Exception:
                pass
            if score > best_score:
                best_score, best = score, u
        if best_score >= 3 and best:
            return best
        return None

    def _collect_search_result_links(self, tab, max_links=30):
        """收集搜索结果页中的外部链接（跨域保留，用于搜索定位）"""
        links = []
        seen = set()
        try:
            page_url = tab.url or ''
            a_eles = tab.eles('tag:a', timeout=5)
        except Exception:
            page_url, a_eles = '', []
        for ele in a_eles[:800]:
            try:
                href = ele.attr('href')
                text = (ele.text or '').strip()
            except Exception:
                continue
            if not href:
                continue
            full = urljoin(page_url, href)
            if not is_normal_url(full):
                continue
            if full.startswith(('javascript:', 'mailto:', 'tel:', 'data:')):
                continue
            if re.search(r'\.(css|js|png|jpe?g|gif|webp|svg|ico|woff2?|ttf|eot)(\?|$)', full.lower()):
                continue
            if len(text) < 2:
                continue
            if full in seen:
                continue
            seen.add(full)
            links.append({'url': full, 'title': text[:80]})
            if len(links) >= max_links:
                break
        return links

    def _log(self, msg):
        """日志输出：GUI注入log_callback时走GUI日志，否则打印到控制台"""
        cb = getattr(getattr(self, 'crawler', None), 'log_callback', None)
        if callable(cb):
            try:
                cb(msg)
                return
            except Exception:
                pass
        print(msg)

    def _scroll_to_load(self, tab, rounds=6):
        """滚动页面到底部触发懒加载（剧集列表/图片等懒加载内容）"""
        for _ in range(rounds):
            try:
                if hasattr(tab, 'scroll') and callable(getattr(tab.scroll, 'to_bottom', None)):
                    tab.scroll.to_bottom()
                elif hasattr(tab, 'run_js'):
                    tab.run_js('window.scrollTo(0, document.body.scrollHeight)')
            except Exception:
                pass
            time.sleep(0.8)
        try:
            if hasattr(tab, 'scroll') and callable(getattr(tab.scroll, 'to_top', None)):
                tab.scroll.to_top()
        except Exception:
            pass

    def _expand_episode_list(self, tab, max_rounds=4):
        """点击"查看全部/展开全部/加载更多"等按钮，展开折叠的剧集列表"""
        try:
            for _ in range(max_rounds):
                clicked = js_click_expand(tab)
                if not clicked:
                    break
                time.sleep(1.0)
        except Exception:
            pass

    def _page_title(self, tab):
        try:
            t = tab.title
            return (t or '').strip()[:60] or '视频'
        except Exception:
            return '视频'

    @staticmethod
    def _looks_like_episode_list(links):
        """≥2个链接文本含"第X集/话/期"或纯数字集数 → 疑似剧集列表，应继续深入"""
        hits = 0
        for l in links:
            t = (l.get('title') or '').strip()
            if _EPISODE_TITLE_RE.search(t):
                hits += 1
            elif t.isdigit() and len(t) <= 8:  # 纯数字集数（1、2、3 或 01、02）
                hits += 1
            if hits >= 2:
                return True
        return False

    @staticmethod
    def _is_episode_link(link):
        """判断链接是否指向单集（标题含集数标记/纯数字，或URL带剧集特征）"""
        t = (link.get('title') or '').strip()
        if _EPISODE_TITLE_RE.search(t):
            return True
        if t.isdigit() and len(t) <= 8:
            return True
        u = (link.get('url') or '').lower()
        return bool(re.search(r'/(play|watch|episode|vod)/|\w{2}\d{5,}_\d+', u))

    # ---- 候选链接 ----
    def _collect_candidate_links(self, tab, keyword=None):
        """收集当前页中的候选链接（同域名、非静态资源、有文本/似详情页）"""
        links = []
        seen = set()
        try:
            page_url = tab.url or ''
            a_eles = tab.eles('tag:a', timeout=5)
        except Exception:
            page_url, a_eles = '', []

        for ele in a_eles[:MAX_A_ELEMS]:
            try:
                href = ele.attr('href')
                text = (ele.text or '').strip()
            except Exception:
                continue
            if not href:
                continue
            full = urljoin(page_url, href)
            if not is_normal_url(full):
                continue
            if not self._is_candidate(full, text):
                continue
            if page_url and not self._same_domain(page_url, full):
                continue
            if full in seen:
                continue
            seen.add(full)
            # 关键词过滤（仅第一层）
            if keyword:
                kw = keyword.lower()
                if kw not in full.lower() and kw not in text.lower():
                    continue
            links.append({'url': full, 'title': text[:60] or '条目'})
            if len(links) >= MAX_LINKS_PER_PAGE:
                break
        return links

    @staticmethod
    def _is_candidate(url, text):
        u = url.lower()
        if u.startswith(('javascript:', 'mailto:', 'tel:', 'data:')):
            return False
        if re.search(r'\.(css|js|png|jpe?g|gif|webp|svg|ico|woff2?|ttf|eot|pdf|zip|rar|7z)(\?|$)', u):
            return False
        if '#' in u and u.split('#')[1] and not u.split('?')[0].endswith('/'):
            return False
        if len(text) < 2 and not re.search(r'\d', u) and u.count('/') < 4:
            return False
        return True

    @staticmethod
    def _same_domain(a, b):
        try:
            da = urlparse(a).netloc.lower().replace('www.', '')
            db = urlparse(b).netloc.lower().replace('www.', '')
            return da == db and bool(da)
        except Exception:
            return False

    # ---- 单页视频提取 ----
    def _find_video_in_page(self, tab, depth=0, max_depth=2, poll_seconds=None):
        """在标签页中查找视频地址，返回 (url, type) 或 (None, None)

        很多站点（如AcFun/B站）的m3u8由播放器JS异步请求后才写入页面，
        因此对"html含m3u8/video标签"进行最多 poll_seconds 秒轮询等待。
        """
        poll_seconds = PLAYER_WAIT if poll_seconds is None else poll_seconds
        page_url = None
        try:
            page_url = tab.url
        except Exception:
            pass

        deadline = time.time() + poll_seconds
        waited_log = False
        while True:
            # 1) 页面HTML里的m3u8（含播放器异步写入后的状态）
            try:
                page_text = tab.html
            except Exception:
                page_text = ''
            if page_text:
                m = _M3U8_RE.search(page_text)
                if m:
                    if waited_log:
                        self._log(f"  等待播放器后捕获到 m3u8 播放地址")
                    else:
                        self._log("  发现 m3u8 播放地址")
                    return m.group(0), 'hls'
                m2 = _M3U8_ESCAPED_RE.search(page_text)
                if m2:
                    u = _normalize_escaped_url(m2.group(0))
                    if waited_log:
                        self._log(f"  等待播放器后捕获到转义 m3u8 播放地址")
                    else:
                        self._log("  发现转义 m3u8 播放地址")
                    return u, 'hls'

            # 2) video/source 标签（src/data-src/currentSrc）
            video_url, vtype = self._find_video_tag(tab, page_url)
            if video_url:
                return video_url, vtype

            if time.time() >= deadline:
                break
            if not waited_log:
                self._log(f"  页面暂无m3u8，等待播放器加载({int(deadline - time.time())}s)...")
                waited_log = True
            time.sleep(1.5)

        # 3) iframe 嵌套
        if depth < max_depth:
            try:
                iframes = tab.eles('tag:iframe', timeout=3)
                for frame_ele in iframes[:5]:
                    try:
                        frame = frame_ele.get_frame()
                        if frame is None:
                            continue
                        inner_url, inner_type = self._find_video_in_page(frame, depth + 1, max_depth, poll_seconds=3)
                        if inner_url:
                            return inner_url, inner_type
                    except Exception:
                        continue
            except Exception:
                pass

        # 4) 页面HTML里的直链（mp4等）
        if page_text:
            m = _MEDIA_EXT_RE.search(page_text)
            if m:
                return m.group(0), 'direct'

        return None, None

    def _find_video_tag(self, tab, page_url):
        try:
            videos = tab.eles('tag:video', timeout=3)
        except Exception:
            videos = []

        for v in videos[:10]:
            for attr in ('src', 'data-src', 'data-url', 'data-hls', 'currentSrc'):
                try:
                    src = v.attr(attr)
                    if src and is_normal_url(src):
                        return urljoin(page_url, src), ('hls' if '.m3u8' in src.lower() else 'direct')
                except Exception:
                    continue
            try:
                sources = v.eles('tag:source', timeout=2)
            except Exception:
                sources = []
            for s in sources[:10]:
                try:
                    src = s.attr('src')
                    if src and is_normal_url(src):
                        return urljoin(page_url, src), ('hls' if '.m3u8' in src.lower() else 'direct')
                except Exception:
                    continue
        return None, None

    # ---- 播放器内嵌JSON（B站 __playinfo__ 等） ----
    _PLAYINFO_RE = re.compile(r'window\.__playinfo__=(\{.*?\})</script>', re.S)

    def _parse_player_json(self, tab):
        """解析播放器内嵌JSON，返回 {'video_url':..., 'audio_url':...} 或 None"""
        html = ''
        for _ in range(3):
            try:
                html = tab.html or ''
            except Exception:
                html = ''
            m = self._PLAYINFO_RE.search(html)
            if m:
                break
            time.sleep(2)  # 播放器初始化需要时间
        if not m:
            return None
        try:
            data = json.loads(m.group(1))
        except Exception:
            return None
        d = (data or {}).get('data') or {}
        dash = d.get('dash') or {}
        v = self._pick_dash(dash.get('video'))
        a = self._pick_dash(dash.get('audio'))
        if v and a:
            return {'video_url': v, 'audio_url': a}
        if v:
            return {'video_url': v, 'audio_url': None}
        durl = d.get('durl') or []
        if durl and durl[0].get('url'):
            return {'video_url': durl[0]['url'], 'audio_url': None}
        return None

    @staticmethod
    def _pick_dash(items):
        """从DASH流列表挑选清晰度最高的可用地址"""
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
