"""网站发现模块 - 动态加载和管理视频站点爬虫（与漫画下载器同构）

站点只由 sites_data/ 目录下的 .py 文件决定：
- 导入 .py 文件 → 站点出现
- 删除 .py 文件 → 站点消失
"""
import importlib.util
import os
import sys
from utils import app_base


def _get_data_dir():
    """获取数据目录（存放爬虫.py文件）"""
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = app_base()
    data_dir = os.path.join(base_dir, 'sites_data')
    if not os.path.exists(data_dir):
        os.makedirs(data_dir)
    return data_dir


# 缓存: {site_name: (crawler_class, file_path)}
_sites_cache = None


def _load_crawler_from_file(file_path):
    """从文件加载爬虫类：类名以 Crawler 结尾且包含 SITE_NAME 属性"""
    module_name = os.path.splitext(os.path.basename(file_path))[0]
    # 插件可能相互 import（如子类继承父类），把目录加入 sys.path
    _plugin_dir = os.path.dirname(os.path.abspath(file_path))
    if _plugin_dir not in sys.path:
        sys.path.insert(0, _plugin_dir)
    try:
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if spec is None:
            print(f"无法加载模块: {file_path}")
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for attr_name in dir(module):
            if attr_name.endswith('Crawler'):
                crawler_class = getattr(module, attr_name)
                if hasattr(crawler_class, 'SITE_NAME'):
                    return crawler_class
    except Exception as e:
        print(f"加载 {file_path} 失败: {e}")
    return None


def discover_sites():
    """从 sites_data/ 目录扫描所有 *_crawler.py 文件加载站点"""
    global _sites_cache
    if _sites_cache is not None:
        return {name: cls for name, (cls, _) in _sites_cache.items()}

    sites = {}
    _sites_cache = {}
    data_dir = _get_data_dir()

    if os.path.exists(data_dir):
        for filename in sorted(os.listdir(data_dir)):
            if filename.endswith('_crawler.py'):
                file_path = os.path.join(data_dir, filename)
                crawler_class = _load_crawler_from_file(file_path)
                if crawler_class:
                    site_name = crawler_class.SITE_NAME
                    sites[site_name] = crawler_class
                    _sites_cache[site_name] = (crawler_class, file_path)

    return sites


def refresh_sites():
    """清除缓存，重新扫描站点"""
    global _sites_cache
    _sites_cache = None
    return discover_sites()


def get_site_crawler_class(site_name):
    """获取指定站点的爬虫类"""
    sites = discover_sites()
    if site_name not in sites:
        available = ', '.join(sites.keys()) if sites else '无'
        raise ValueError(f"不支持的站点: {site_name}。可用站点: {available}")
    return sites[site_name]


def get_all_site_names():
    """获取所有可用站点名称列表"""
    return list(discover_sites().keys())


def get_sites_requiring_login():
    sites = discover_sites()
    return [name for name, cls in sites.items()
            if getattr(cls, 'REQUIRES_LOGIN', False)]


def _site_supports_cookie_input(cls):
    if hasattr(cls, 'SUPPORTS_COOKIE_INPUT'):
        return bool(cls.SUPPORTS_COOKIE_INPUT)
    return getattr(cls, 'REQUIRES_LOGIN', False)


def get_sites_supporting_cookie():
    sites = discover_sites()
    return [name for name, cls in sites.items()
            if _site_supports_cookie_input(cls)]


def site_supports_cookie(site_name):
    try:
        cls = get_site_crawler_class(site_name)
    except ValueError:
        return False
    return _site_supports_cookie_input(cls)


def get_site_config(site_name):
    crawler_class = get_site_crawler_class(site_name)
    return getattr(crawler_class, 'CONFIG', {})


def get_site_file_path(site_name):
    discover_sites()
    if _sites_cache and site_name in _sites_cache:
        return _sites_cache[site_name][1]
    return None


def get_all_sites_info():
    discover_sites()
    info = []
    if _sites_cache:
        for name, (cls, path) in _sites_cache.items():
            info.append({
                'name': name,
                'file': os.path.basename(path),
                'file_path': path,
                'requires_login': getattr(cls, 'REQUIRES_LOGIN', False),
                'site_url': getattr(cls, 'SITE_URL', ''),
            })
    return info


def _get_unique_filename(data_dir, filename):
    """生成不冲突的文件名"""
    target = os.path.join(data_dir, filename)
    if not os.path.exists(target):
        return filename
    base, ext = os.path.splitext(filename)
    counter = 1
    while True:
        new_filename = f"{base}_{counter}{ext}"
        if not os.path.exists(os.path.join(data_dir, new_filename)):
            return new_filename
        counter += 1


def add_site_file(src_path):
    """添加站点文件，复制到 sites_data/，重复站点自动过滤"""
    if not os.path.isfile(src_path):
        raise ValueError(f"文件不存在: {src_path}")
    if not src_path.endswith('.py'):
        raise ValueError("站点文件必须是.py文件")

    crawler_class = _load_crawler_from_file(src_path)
    if not crawler_class:
        raise ValueError("文件中未找到有效的Crawler类（需要以Crawler结尾的类名且包含SITE_NAME属性）")

    site_name = crawler_class.SITE_NAME
    existing_sites = discover_sites()
    if site_name in existing_sites:
        raise ValueError(f"站点 '{site_name}' 已存在，已自动过滤")

    with open(src_path, 'r', encoding='utf-8') as f:
        code = f.read()

    data_dir = _get_data_dir()
    filename = _get_unique_filename(data_dir, os.path.basename(src_path))
    dst_path = os.path.join(data_dir, filename)

    with open(dst_path, 'w', encoding='utf-8') as f:
        f.write(code)

    refresh_sites()
    return site_name


def add_site_folder(folder_path):
    """从文件夹添加所有站点文件，重复站点自动过滤"""
    if not os.path.isdir(folder_path):
        raise ValueError(f"文件夹不存在: {folder_path}")

    added, errors, skipped = [], [], []
    for file in os.listdir(folder_path):
        if file.endswith('_crawler.py'):
            src_path = os.path.join(folder_path, file)
            try:
                site_name = add_site_file(src_path)
                added.append((file, site_name))
            except ValueError as e:
                err_msg = str(e)
                if '已存在' in err_msg:
                    skipped.append((file, err_msg))
                else:
                    errors.append((file, err_msg))
    return added, errors, skipped


def remove_site(site_name):
    """删除站点（删除.py文件）"""
    discover_sites()
    if site_name not in _sites_cache:
        raise ValueError(f"未找到站点: {site_name}")

    file_path = _sites_cache[site_name][1]
    if os.path.exists(file_path):
        os.remove(file_path)

    pycache_dir = os.path.join(_get_data_dir(), '__pycache__')
    if os.path.exists(pycache_dir):
        base_name = os.path.splitext(os.path.basename(file_path))[0]
        for pyc_file in os.listdir(pycache_dir):
            if pyc_file.startswith(base_name):
                try:
                    os.remove(os.path.join(pycache_dir, pyc_file))
                except Exception:
                    pass

    refresh_sites()
    return True
