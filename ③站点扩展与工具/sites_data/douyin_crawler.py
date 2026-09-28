# -*- coding: utf-8 -*-
"""douyin_crawler.py: 抖音精选（www.douyin.com/jingxuan）随机爬取插件
链路：打开精选页 → 页面内 fetch feed 接口（带Cookie，免签名）→ JSON 含 mp4 无水印直链
注意：抖音风控极严，务必低频请求（每次收集仅 fetch 1-2 次）；直链签名约 6 小时有效，收集后尽快下载
"""
import time
import random


class DouyinCrawler(object):
    SITE_NAME = '抖音精选'
    SITE_URL = 'https://www.douyin.com/jingxuan'
    REQUIRES_LOGIN = False
    NEEDS_BROWSER = True

    CONFIG = {
        'site_url': 'https://www.douyin.com/jingxuan',
        'direct_connect': False,
    }

    FEED_URL = ('https://www.douyin.com/aweme/v2/web/module/feed/'
                '?device_platform=webapp&aid=6383&channel=channel_pc_web'
                '&module_id=3003101&count={count}')

    # 页面内抓精选 feed（同步 XHR + return 前缀：适配 DrissionPage run_js 包装成 function 体执行）
    FETCH_JS = r"""
return (function() {
  try {
    var xhr = new XMLHttpRequest();
    xhr.open('POST', '%FEED_URL%', false);
    xhr.withCredentials = true;
    xhr.setRequestHeader('Content-Type', 'application/x-www-form-urlencoded');
    xhr.send();
    var j = JSON.parse(xhr.responseText);
    var list = j.aweme_list || [];
    var items = [];
    for (var i = 0; i < list.length; i++) {
      var a = list[i];
      if (!a.video || !a.video.play_addr) continue;
      var urls = a.video.play_addr.url_list || [];
      var direct = null;
      for (var k = 0; k < urls.length; k++) {
        if (/douyinvod|v3-dy|v26|v11/.test(urls[k])) { direct = urls[k]; break; }
      }
      if (!direct) direct = urls[0];
      if (!direct) continue;
      var desc = (a.desc || '').replace(/\s+/g, ' ').trim().slice(0, 40);
      var author = a.author && a.author.nickname ? a.author.nickname : '';
      items.push({
        title: (author ? author + '\uff1a' : '') + desc,
        author: author,
        duration: a.duration || 0,
        url: direct.replace(/&amp;/g, '&')
      });
    }
    return JSON.stringify(items);
  } catch(e) { return 'ERR:' + e.message; }
})()
"""

    def __init__(self, crawler=None, **kwargs):
        self.crawler = crawler

    # ---------- 工具 ----------
    def _log(self, msg):
        try:
            cb = getattr(self.crawler, 'log_callback', None)
            if cb:
                cb(msg)
        except Exception:
            pass

    @staticmethod
    def _human_wait(base=3.0, spread=2.5):
        time.sleep(base + random.uniform(0, spread))

    def _fetch_feed(self, tab, count=20):
        """页面内 fetch 精选 feed，返回视频列表"""
        js = self.FETCH_JS.replace('%FEED_URL%', self.FEED_URL.format(count=count))
        try:
            out = tab.run_js(js) if hasattr(tab, 'run_js') else tab.js(js)
        except Exception as e:
            self._log(f'  feed 抓取失败: {str(e)[:60]}')
            return []
        if not out or str(out).startswith('ERR:'):
            self._log(f'  feed 返回异常: {str(out)[:80]}')
            return []
        if isinstance(out, str):
            import json
            try:
                return json.loads(out)
            except Exception:
                return []
        return out

    # ---------- 搜索：打开精选页 ----------
    def search_series(self, name, series_id=None):
        self._log('打开抖音精选页（随机视频流）...')
        try:
            self.crawler.tab.get(self.SITE_URL, timeout=15)
        except Exception as e:
            raise ValueError(f'打开抖音精选失败: {str(e)[:60]}')
        self._human_wait(4, 3)
        return self.crawler.tab

    # ---------- 主页扫描：抓一批随机视频 ----------
    def scan_homepage(self, max_items=60):
        """抓精选流视频列表：[(标题, 占位URL)]，供主页扫描模式勾选下载"""
        tab = getattr(self.crawler, 'tab', None)
        if tab is None:
            try:
                tab = self.search_series('')
            except Exception:
                return []
        items = self._fetch_feed(tab, count=min(max(max_items, 20), 50))
        self._log(f'  精选流抓到 {len(items)} 个视频')
        out = []
        for i, it in enumerate(items):
            out.append((it.get('title', f'视频{i + 1}')[:60],
                        f'douyin://random/{i + 1}'))
        random.shuffle(out)  # 随机顺序
        return out

    # ---------- 收集：随机取视频直链 ----------
    def collect_episode_videos(self, target_tab, episode_start=1, episode_end=0,
                               max_threads=5, progress_callback=None, keyword=None):
        """精选流随机抓取：fetch 一批 → 随机取若干集 → 返回 mp4 直链"""
        want = 10  # 单次随机抓取数量（用户可全选下载）
        self._log(f'[收集] 正在随机抓取精选视频（低频单次请求）...')
        items = self._fetch_feed(target_tab, count=30)
        if not items:
            raise RuntimeError('未从抖音精选获取到视频（可能需登录/风控，稍后再试）')
        random.shuffle(items)
        eps = []
        for i, it in enumerate(items[:want]):
            eps.append({
                'episode_num': i + 1,
                'title': it.get('title', f'抖音视频{i + 1}')[:60],
                'video_url': it['url'],
                'audio_url': None,
                'video_type': 'direct',
                'referer': 'https://www.douyin.com/',
            })
            if progress_callback:
                progress_callback()
        self._log(f'[收集] 完成: 随机抓到 {len(eps)} 个视频直链（签名约6小时有效，请尽快下载）')
        return eps

    # ---------- 持续随机抓取（人控停止，抓取时间与数量由用户决定） ----------
    def collect_random(self, target_tab, stop_flag_func=None,
                       progress_callback=None, max_items=500, per_batch=30):
        """循环抓取精选流：每批去重累积，直到 stop_flag_func() 为 True 或达到 max_items
        progress_callback(已抓取数量) 每次新增时回调；低频请求（3-5秒/批）避免风控
        """
        seen = set()
        eps = []
        empty_count = 0
        while True:
            if stop_flag_func is not None and stop_flag_func():
                self._log(f'[收集] 用户停止，共抓到 {len(eps)} 个视频')
                break
            if len(eps) >= max_items:
                self._log(f'[收集] 已达上限 {max_items} 个，停止抓取')
                break
            items = self._fetch_feed(target_tab, count=per_batch)
            if not items:
                empty_count += 1
                self._log(f'  本批无新视频（第{empty_count}次），等待重试...')
                if empty_count >= 5:
                    self._log('[收集] 连续5批无数据，可能风控，请稍后再试')
                    break
                time.sleep(6 + random.uniform(1, 4))
                continue
            empty_count = 0
            got = 0
            for it in items:
                u = it.get('url')
                if not u or u in seen:
                    continue
                seen.add(u)
                eps.append({
                    'episode_num': len(eps) + 1,
                    'title': it.get('title', f'抖音视频{len(eps) + 1}')[:60],
                    'video_url': u,
                    'audio_url': None,
                    'video_type': 'direct',
                    'referer': 'https://www.douyin.com/',
                })
                got += 1
                if progress_callback:
                    progress_callback(len(eps))
            self._log(f'  已随机抓到 {len(eps)} 个视频（本批新增 {got} 个，继续中...）')
            time.sleep(3 + random.uniform(1, 3))
        return eps

    # ---------- 下载时的单集解析（收集已完成，无需） ----------
    def get_episode_count(self, target_tab):
        return 1
