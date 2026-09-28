# 视频站点爬虫模板 - 复制本文件并按注释修改即可适配新站点
# 命名规范：
#   - 文件名: {站点简称}_crawler.py（如 mysite_crawler.py）
#   - 类名:   {站点简称}Crawler（如 MysiteCrawler）
# 放入 sites_data/ 目录后重启程序即出现在站点列表。
import time
import threading

from utils import is_normal_url


class MysiteCrawler:
    """示例站点爬虫 (https://example.com/)"""

    # ---- 站点元数据 ----
    SITE_NAME = '示例视频站'            # 显示名称
    SITE_URL = 'https://example.com/'  # 站点主页
    REQUIRES_LOGIN = False             # 是否需要登录（需要时置True并实现Cookie流程）
    NEEDS_BROWSER = True               # 纯requests实现可置False（跳过浏览器）

    # ---- 站点配置 ----
    CONFIG = {
        'site_url': SITE_URL,
        'locators': {
            'search_result': 'xpath:/html/body/...',   # 搜索结果第一个链接
            'episode_item': 'xpath:/html/body/...',    # 剧集列表项
        },
        'image_attr': 'src',
        'direct_connect': False,   # CDN拒绝代理出口时置True（浏览器直连）
        'video_referer': None,     # 视频CDN防盗链时填写站点URL
    }

    def __init__(self, crawler):
        self.crawler = crawler
        self.locators = crawler.site_config['locators']

    def search_series(self, name, series_id=None):
        """搜索剧集并打开详情页，返回详情页标签"""
        search_url = f"{self.SITE_URL}search?keyword={name}"
        self.crawler.tab.get(search_url)
        time.sleep(2)
        result_ele = self.crawler.tab.ele(self.locators['search_result'], timeout=10)
        href = result_ele.attr('href')
        if not href.startswith('http'):
            href = self.SITE_URL.rstrip('/') + href
        target_tab = self.crawler.page.new_tab(href)
        return target_tab

    def get_episode_count(self, target_tab):
        """返回总集数"""
        ep_divs = target_tab.eles(self.locators['episode_item'], timeout=10)
        return len(ep_divs)

    def get_episode_list(self, target_tab):
        """（可选）返回带标题的剧集表 [{'num':1,'title':'第1集 ...'}]"""
        return None

    def collect_episode_videos(self, target_tab, episode_start=1, episode_end=0,
                               max_threads=5, progress_callback=None):
        """收集每集的视频URL

        Returns:
            list[dict]: [{
                'episode_num': 集数,
                'title': 集标题(可空),
                'video_url': 视频地址(mp4直链 或 m3u8),
                'video_type': 'direct'|'hls'|'auto',
                'referer': 防盗链Referer(可空),
            }]
        """
        all_eps = []

        # TODO: 遍历 episode_start..episode_end 的每一集：
        #   1. 打开该集播放页（new_tab）
        #   2. 在播放页中提取视频地址（见 generic_crawler.py 的提取思路：
        #      m3u8正则 / video标签 / iframe递归）
        #   3. 组装 episode dict 加入 all_eps
        #   4. 每处理完一集调用 progress_callback()

        # 示例：假设播放页 video 标签有 src
        # video_ele = chapter_tab.ele('tag:video', timeout=10)
        # vurl = video_ele.attr('src')
        # all_eps.append({
        #     'episode_num': 1,
        #     'title': '第1集',
        #     'video_url': vurl,
        #     'video_type': 'hls' if '.m3u8' in vurl.lower() else 'direct',
        #     'referer': self.CONFIG.get('video_referer'),
        # })

        return all_eps
