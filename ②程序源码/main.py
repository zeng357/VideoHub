# 主脚本入口 - 通用视频下载器（命令行模式）
from utils import ensure_console_safe
ensure_console_safe()  # 入口加固：防GBK打印崩溃，须在其他导入前执行

from config import BROWSER_PATHS, DEFAULT_SITE
from site_discovery import get_all_site_names
from video_crawler import VideoCrawler
from download_flow import run_direct_link, run_auto_scan, run_site_download


def ask_mode():
    print("\n请选择搜索方法:")
    print("  1. 自动扫描（输入地址，自动扫描页面/列表页并下载）")
    print("  2. 站内搜索下载（按站点搜索剧集）")
    print("  3. 直接链接下载（粘贴 mp4/m3u8 地址）")
    while True:
        try:
            choice = input("请输入模式编号 (1-3, 默认:1): ").strip() or "1"
            if choice in ('1', '2', '3'):
                return choice
        except (EOFError, KeyboardInterrupt):
            return None
        print("无效选择，请重新输入")


def ask_browser():
    print("\n浏览器类型:")
    print("  1. Edge")
    print("  2. Chrome")
    browser_choice = input("请选择浏览器 (1-2, 默认:1): ").strip() or "1"
    browser_type = 'edge' if browser_choice == '1' else 'chrome'
    browser_path = BROWSER_PATHS[browser_type]
    custom_path = input(f"请输入浏览器路径(直接回车使用默认: {browser_path}): ").strip()
    if custom_path:
        browser_path = custom_path
    headless = input("是否使用无头模式(y/n, 默认n): ").strip().lower() == 'y'
    return browser_path, headless


def ask_speed():
    print("\n速度档位:")
    print("  1. 极速(32线程)  2. 高速(16线程)  3. 标准(6线程)  4. 平稳(3线程)")
    speed_map = {'1': 32, '2': 16, '3': 6, '4': 3}
    try:
        choice = input("请选择 (1-4, 默认:3): ").strip() or "3"
        return speed_map.get(choice, 6)
    except (EOFError, KeyboardInterrupt):
        return 6


def main():
    print("=" * 50)
    print("通用视频下载器")
    print("=" * 50)

    mode = ask_mode()
    if mode is None:
        return

    download_path = input("请输入下载目录(直接回车使用当前目录): ").strip() or None
    use_ffmpeg = input("HLS是否用ffmpeg封装为mp4(y/n, 默认y): ").strip().lower() != 'n'
    max_workers = ask_speed()

    # 模式3：直接链接（无需浏览器）
    if mode == '3':
        url = input("请输入视频链接 (mp4/m3u8): ").strip()
        if not url:
            print("链接不能为空")
            return
        result = run_direct_link(url, download_path, log=print, use_ffmpeg=use_ffmpeg,
                                 max_workers=max_workers)
        print(f"\n下载{'成功' if result['success'] else '失败'}: {result.get('final_path')}")
        return

    # 模式1/2 需要浏览器
    browser_path, headless = ask_browser()

    # 模式1：自动扫描
    if mode == '1':
        url = input("请输入要扫描的地址 (页面/列表URL): ").strip()
        if not url:
            print("地址不能为空")
            return
        keyword = input("请输入关键词(可选，直接回车跳过): ").strip() or None
        print("\n正在启动浏览器...")
        crawler = VideoCrawler('自动扫描', browser_path, headless)
        try:
            result = run_auto_scan(crawler, url, keyword=keyword, download_path=download_path,
                                   log=print, use_ffmpeg=use_ffmpeg, max_workers=max_workers)
        finally:
            crawler.page.close()
            print("浏览器已关闭")
        print(f"\n完成: 成功 {result['done']}/{result['total']}，失败 {len(result['failed'])}")
        print(f"保存目录: {result['series_dir']}")
        return

    # 模式2：站内搜索下载
    site_names = get_all_site_names()
    print("\n可用站点:")
    for i, name in enumerate(site_names, 1):
        print(f"  {i}. {name}")
    try:
        default_index = site_names.index(DEFAULT_SITE) + 1 if DEFAULT_SITE in site_names else 1
        site_choice = input(f"\n请选择站点 (1-{len(site_names)}, 默认:{default_index}): ").strip()
        site_name = site_names[int(site_choice) - 1] if site_choice else DEFAULT_SITE
    except (ValueError, IndexError):
        print("无效的选择，使用默认站点")
        site_name = DEFAULT_SITE
    print(f"已选择站点: {site_name}")

    series_name = input("请输入剧集名称: ").strip()
    if not series_name:
        print("剧集名称不能为空")
        return
    try:
        episode_num = int(input("请输入要下载的集数(0表示全部): ").strip() or "0")
    except ValueError:
        print("集数必须是数字")
        return

    print("\n正在启动浏览器...")
    crawler = VideoCrawler(site_name, browser_path, headless)
    try:
        result = run_site_download(crawler, series_name, episode_start=1,
                                   episode_end=episode_num,
                                   download_path=download_path, log=print,
                                   use_ffmpeg=use_ffmpeg, max_workers=max_workers)
    finally:
        crawler.page.close()
        print("浏览器已关闭")

    print(f"\n✓ 下载完成: 成功 {result['done']}/{result['total']}，失败 {len(result['failed'])}")
    if result['failed']:
        for ep in result['failed']:
            print(f"  - 第{ep.get('episode_num')}集: {ep.get('video_url')}")
    print(f"保存目录: {result['series_dir']}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已取消")
    except Exception as e:
        print(f"\n出错: {e}")
        import traceback
        traceback.print_exc()
        input("\n按回车键退出...")
