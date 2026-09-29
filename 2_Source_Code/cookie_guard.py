# -*- coding: utf-8 -*-
"""
cookie_guard.py — Cookie 容器加密守卫
=======================================
- 数据用随机数据密钥加密（Fernet / AES-128-CBC + HMAC-SHA256）
- 数据密钥分两层保护：
    ① 本机密钥：由「机器指纹(MachineGuid) + PBKDF2-SHA256」派生 → 本电脑直接打开，无需密码
    ② 密码密钥：由「用户密码 + PBKDF2-SHA256」派生 → 外部电脑输入正确密码也可打开
- 防暴力破解：外部打开密码错误累计 4 次 → cookies 目录内全部数据自动销毁
"""
import os
import json
import base64
import shutil
import hashlib
import hmac

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

MAX_ATTEMPTS = 4          # 密码错误上限，超过即销毁
COOKIES_DIR = 'cookies'   # 与 video_crawler 的 DEFAULT_COOKIES_DIR 保持同目录
SALT_FILE = '.guard_salt'
ATTEMPTS_FILE = '.guard_attempts'
_EXT = '.enc'             # 加密容器文件后缀
SECURITY_FILE = '.guard_questions'   # 安全问题（加密存储，本机密钥保护）
INTEGRITY_FILE = '.guard_integrity.enc'  # 程序完整性指纹（机器指纹加密）
INTEGRITY_EXCLUDE = ('_t_', '__pycache__', 'cookies', 'downloads', 'logs',
                     'venv', '.git', 'dist', 'build', '.enc', '.pyc', '.log')

# ---------------------------------------------------------------- 机器指纹
def machine_id():
    """本机唯一指纹：注册表 MachineGuid（每台 Windows 唯一，重装系统会变）"""
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                           r'SOFTWARE\Microsoft\Cryptography')
        val, _ = winreg.QueryValueEx(k, 'MachineGuid')
        return str(val).strip() or ''
    except Exception:
        return ''


# ---------------------------------------------------------------- 工具
def _cookies_root(base_dir):
    """cookies 目录绝对路径（base_dir 为程序目录）"""
    return os.path.join(base_dir, COOKIES_DIR)


def _derive(material: bytes, salt: bytes) -> bytes:
    """PBKDF2-SHA256 派生 32 字节 Fernet 密钥（200k 次迭代）"""
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32,
                     salt=salt, iterations=200_000)
    return base64.urlsafe_b64encode(kdf.derive(material))


def _fernet(key_b64: bytes):
    return Fernet(key_b64)


def _salt_path(base_dir):
    return os.path.join(_cookies_root(base_dir), SALT_FILE)


def _load_salt(base_dir):
    """全局盐：首次生成并保存，之后复用（供派生用）"""
    p = _salt_path(base_dir)
    if os.path.exists(p):
        try:
            with open(p, 'rb') as f:
                s = f.read(32)
            if len(s) >= 16:
                return s
        except Exception:
            pass
    os.makedirs(_cookies_root(base_dir), exist_ok=True)
    s = os.urandom(16)
    with open(p, 'wb') as f:
        f.write(s)
    return s


# ---------------------------------------------------------------- 密码缓存
_password_cache = None   # GUI 输入/设置的容器密码（保存在内存，不落盘）
_session_unlocked = False  # 会话解锁标记：验证通过后本会话内免重复输入


def set_password_cache(pw):
    """缓存容器密码（GUI 密码弹窗/设置容器密码时调用）"""
    global _password_cache, _session_unlocked
    _password_cache = pw
    if pw:
        _session_unlocked = True   # 本人刚输入/设置密码 → 视为本会话已验证


def current_password():
    """当前容器密码（保存时写入密码层；外部打开时用于解锁）"""
    return _password_cache


def clear_password_cache():
    """清除密码缓存（如用户退出登录/重置时）"""
    global _password_cache, _session_unlocked
    _password_cache = None
    _session_unlocked = False


def unlock_session():
    """标记本会话已解锁（验证通过后调用），后续访问密码库免重复输入"""
    global _session_unlocked
    _session_unlocked = True


def reset_session():
    """重置会话解锁状态（每次程序启动均为未解锁）"""
    global _session_unlocked
    _session_unlocked = False


# ---------------------------------------------------------------- 失败计数/销毁
def _attempts_path(base_dir):
    return os.path.join(_cookies_root(base_dir), ATTEMPTS_FILE)


def read_attempts(base_dir):
    try:
        with open(_attempts_path(base_dir), 'r') as f:
            return int(f.read().strip() or '0')
    except Exception:
        return 0


def _write_attempts(base_dir, n):
    os.makedirs(_cookies_root(base_dir), exist_ok=True)
    with open(_attempts_path(base_dir), 'w') as f:
        f.write(str(n))


def is_destroyed(base_dir):
    return read_attempts(base_dir) >= MAX_ATTEMPTS


def destroy_all(base_dir):
    """密码错误超限：销毁 cookies 目录内全部数据。
    注意：失败计数文件保留为 MAX（销毁状态不可逆），
    否则计数文件被删会导致守卫失效、数据“复活”。"""
    root = _cookies_root(base_dir)
    try:
        if os.path.isdir(root):
            for fn in os.listdir(root):
                p = os.path.join(root, fn)
                try:
                    if os.path.isfile(p):
                        os.remove(p)
                    elif os.path.isdir(p):
                        shutil.rmtree(p, ignore_errors=True)
                except Exception:
                    pass
    except Exception:
        pass
    # 保留销毁标记：计数写为 MAX，使 is_destroyed 持续为 True
    try:
        os.makedirs(root, exist_ok=True)
        with open(_attempts_path(base_dir), 'w') as f:
            f.write(str(MAX_ATTEMPTS))
    except Exception:
        pass
    # 盐可删（数据已销毁，无保留意义；重新生成不影响销毁状态）
    try:
        if os.path.exists(_salt_path(base_dir)):
            os.remove(_salt_path(base_dir))
    except Exception:
        pass
    return True


# ---------------------------------------------------------------- 程序完整性
def _integrity_path(base_dir):
    return os.path.join(_cookies_root(base_dir), INTEGRITY_FILE)


def _program_files(base_dir):
    """程序目录下核心文件（.py，排除测试/缓存/数据目录）"""
    files = []
    try:
        for root, dirs, names in os.walk(base_dir):
            dirs[:] = [d for d in dirs
                       if not any(x in d for x in INTEGRITY_EXCLUDE)]
            for n in names:
                if n.endswith('.py') and not n.startswith('_t_'):
                    files.append(os.path.join(root, n))
    except Exception:
        pass
    return sorted(files)


def _compute_hashes(base_dir):
    h = {}
    for p in _program_files(base_dir):
        try:
            with open(p, 'rb') as f:
                h[os.path.relpath(p, base_dir).replace('\\', '/')] = hashlib.sha256(f.read()).hexdigest()
        except Exception:
            pass
    return h


def _read_integrity_raw(base_dir):
    """手工解密完整性基线（不经 decrypt_container，避免递归）"""
    path = _integrity_path(base_dir)
    with open(path, 'r', encoding='utf-8') as f:
        data = f.read()
    c = json.loads(data)
    salt = _load_salt(base_dir)
    mid = machine_id()
    data_b = base64.b64decode(c['data'])
    key_m = _fernet(_derive(mid.encode('utf-8'), salt))
    dk = key_m.decrypt(base64.b64decode(c['key_m']))
    return _fernet(dk).decrypt(data_b)


def _save_integrity(base_dir):
    """建立/更新完整性基线（机器指纹加密存储）"""
    try:
        raw = json.dumps({'v': 1, 'hashes': _compute_hashes(base_dir)}).encode('utf-8')
        container = encrypt_container(raw, None, base_dir)
        with open(_integrity_path(base_dir), 'w', encoding='utf-8') as f:
            f.write(container)
        return True
    except Exception:
        return False


def _check_integrity(base_dir):
    """返回 'ok'（程序未改动）/ 'changed'（被改动）/ 'missing'（无基线）"""
    if not os.path.exists(_integrity_path(base_dir)):
        return 'missing'
    try:
        raw = _read_integrity_raw(base_dir)
        rec = json.loads(raw.decode('utf-8'))
        cur = _compute_hashes(base_dir)
        old = rec.get('hashes') or {}
        if old == cur:
            return 'ok'
        return 'changed'
    except Exception:
        return 'changed'   # 基线读不出（被篡改/解密失败）→ 视为改动


def confirm_program_change(base_dir=None):
    """本人确认程序更新/改动 → 重新建立完整性基线（本人验证通过后调用）"""
    if base_dir is None:
        base_dir = os.getcwd()
    return _save_integrity(base_dir)


def container_has_password(base_dir=None):
    """是否已有任意容器设置了密码"""
    if base_dir is None:
        base_dir = os.getcwd()
    root = _cookies_root(base_dir)
    if not os.path.isdir(root):
        return False
    try:
        for n in os.listdir(root):
            if n.endswith(_EXT):
                try:
                    with open(os.path.join(root, n), 'r', encoding='utf-8') as f:
                        c = json.loads(f.read())
                    if c.get('has_pw'):
                        return True
                except Exception:
                    pass
    except Exception:
        pass
    return False


# ---------------------------------------------------------------- 容器加解密
def encrypt_container(raw: bytes, password=None, base_dir=None):
    """加密数据为容器 JSON 字符串。
    password 为空：仅本机可打开；password 非空：外部电脑凭密码可打开。
    """
    if base_dir is None:
        base_dir = os.getcwd()
    salt = _load_salt(base_dir)
    data_key = Fernet.generate_key()
    mid = machine_id()

    f_data = _fernet(data_key)
    blob_data = f_data.encrypt(raw)

    key_m = _fernet(_derive(mid.encode('utf-8'), salt))
    blob_m = key_m.encrypt(data_key)

    blob_p = None
    if password:
        key_p = _fernet(_derive(password.encode('utf-8'), salt))
        blob_p = key_p.encrypt(data_key)

    container = {
        'v': 1,
        'data': base64.b64encode(blob_data).decode('ascii'),
        'key_m': base64.b64encode(blob_m).decode('ascii'),
        'key_p': base64.b64encode(blob_p).decode('ascii') if blob_p else None,
        'has_pw': bool(password),
    }
    return json.dumps(container, ensure_ascii=False)


def decrypt_container(data: str, password=None, base_dir=None, internal=False):
    """解密容器。
    internal=True  : 程序自身浏览器访问（本机机器指纹即通过，无感）；
                     外部电脑（指纹不匹配）才需密码验证。
    internal=False : 其他方式访问密码库（GUI 手动解锁/导出等），
                     设置过密码的容器本机也需密码验证；未设密码仅本机可开。
    返回: (bytes, 'ok') 成功
          (None, 'need_password') 需密码但未提供
          (None, 'wrong_password') 密码错误（已计数）
          (None, 'no_password_configured') 容器未设置密码，本机可开/外部拒绝
          (None, 'destroyed') 已因错误超限销毁
          (None, 'corrupt') 数据损坏
    """
    if base_dir is None:
        base_dir = os.getcwd()
    if is_destroyed(base_dir):
        destroy_all(base_dir)
        return None, 'destroyed'
    try:
        c = json.loads(data)
    except Exception:
        return None, 'corrupt'
    salt = _load_salt(base_dir)
    mid = machine_id()
    data_b = base64.b64decode(c['data'])
    has_pw = bool(c.get('has_pw') and c.get('key_p'))

    # ---- 程序自身（internal=True）：本机指纹即通过（无感），外部走密码 ----
    if internal:
        # 完整性检测：程序文件被改动 → 需重新验证（即使本机）
        try:
            integ = _check_integrity(base_dir)
        except Exception:
            integ = 'changed'
        if integ == 'missing':
            _save_integrity(base_dir)   # 首次运行自动建立基线
            integ = 'ok'
        # 本机指纹打开
        try:
            key_m = _fernet(_derive(mid.encode('utf-8'), salt))
            dk = key_m.decrypt(base64.b64decode(c['key_m']))
            raw = _fernet(dk).decrypt(data_b)
            if integ == 'ok':
                return raw, 'ok'        # 程序未改动 → 本机无感
            # 程序被改动 → 需重新提供密码验证
            if has_pw:
                if _session_unlocked and _password_cache:
                    try:
                        key_p = _fernet(_derive(_password_cache.encode('utf-8'), salt))
                        dk2 = key_p.decrypt(base64.b64decode(c['key_p']))
                        raw2 = _fernet(dk2).decrypt(data_b)
                        _save_integrity(base_dir)   # 本人验证通过 → 重建基线
                        return raw2, 'ok'
                    except Exception:
                        pass
                if not password:
                    return None, 'need_verify'
                try:
                    key_p = _fernet(_derive(password.encode('utf-8'), salt))
                    dk2 = key_p.decrypt(base64.b64decode(c['key_p']))
                    raw2 = _fernet(dk2).decrypt(data_b)
                    set_password_cache(password)
                    _save_integrity(base_dir)
                    return raw2, 'ok'
                except Exception:
                    pass
                n = read_attempts(base_dir) + 1
                _write_attempts(base_dir, n)
                if n >= MAX_ATTEMPTS:
                    destroy_all(base_dir)
                    return None, 'destroyed'
                return None, 'wrong_password'
            # 无密码容器 + 程序被改动 → 需本人确认（GUI 层处理）
            return None, 'need_verify'
        except Exception:
            pass
        # 指纹不匹配 → 其他电脑：密码验证（不涉及完整性基线）
        if not has_pw:
            return None, 'no_password_configured'
        if not password:
            return None, 'need_password'
        try:
            key_p = _fernet(_derive(password.encode('utf-8'), salt))
            dk = key_p.decrypt(base64.b64decode(c['key_p']))
            raw = _fernet(dk).decrypt(data_b)
            set_password_cache(password)
            return raw, 'ok'
        except Exception:
            pass
        n = read_attempts(base_dir) + 1
        _write_attempts(base_dir, n)
        if n >= MAX_ATTEMPTS:
            destroy_all(base_dir)
            return None, 'destroyed'
        return None, 'wrong_password'

    # ---- 其他访问（internal=False）：设置过密码必须验证（本机也不例外） ----
    if has_pw:
        if _session_unlocked and _password_cache:
            try:
                key_p = _fernet(_derive(_password_cache.encode('utf-8'), salt))
                dk = key_p.decrypt(base64.b64decode(c['key_p']))
                raw = _fernet(dk).decrypt(data_b)
                return raw, 'ok'
            except Exception:
                pass
        if not password:
            return None, 'need_password'
        try:
            key_p = _fernet(_derive(password.encode('utf-8'), salt))
            dk = key_p.decrypt(base64.b64decode(c['key_p']))
            raw = _fernet(dk).decrypt(data_b)
            set_password_cache(password)
            return raw, 'ok'
        except Exception:
            pass
        n = read_attempts(base_dir) + 1
        _write_attempts(base_dir, n)
        if n >= MAX_ATTEMPTS:
            destroy_all(base_dir)
            return None, 'destroyed'
        return None, 'wrong_password'

    # 未设置密码 → 仅本机指纹可无感打开；外部拒绝
    try:
        key_m = _fernet(_derive(mid.encode('utf-8'), salt))
        dk = key_m.decrypt(base64.b64decode(c['key_m']))
        raw = _fernet(dk).decrypt(data_b)
        return raw, 'ok'
    except Exception:
        return None, 'no_password_configured'


def encrypt_file(src_path: str, password=None, base_dir=None):
    """把 src_path 内容加密写入 src_path + .enc"""
    if base_dir is None:
        base_dir = os.getcwd()
    with open(src_path, 'rb') as f:
        raw = f.read()
    container = encrypt_container(raw, password, base_dir)
    with open(src_path + _EXT, 'w', encoding='utf-8') as f:
        f.write(container)


def decrypt_file(enc_path: str, password=None, base_dir=None, internal=False):
    """读 .enc 文件并解密；返回 (bytes, status)。internal=True=程序自身访问"""
    if base_dir is None:
        base_dir = os.getcwd()
    try:
        with open(enc_path, 'r', encoding='utf-8') as f:
            data = f.read()
    except Exception:
        return None, 'corrupt'
    return decrypt_container(data, password, base_dir, internal)


def set_container_password(base_dir, new_password):
    """为已保存的全部容器设置/更新密码：逐个用本机密钥解出 data_key，再重写 key_p。
    返回 (成功数, 失败数)"""
    if base_dir is None:
        base_dir = os.getcwd()
    root = _cookies_root(base_dir)
    ok, fail = 0, 0
    if not os.path.isdir(root):
        return 0, 0
    salt = _load_salt(base_dir)
    mid = machine_id()
    key_m = _fernet(_derive(mid.encode('utf-8'), salt))
    for fn in os.listdir(root):
        if not fn.endswith(_EXT):
            continue
        path = os.path.join(root, fn)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                c = json.loads(f.read())
            dk = key_m.decrypt(base64.b64decode(c['key_m']))
            # 重写密码密钥
            key_p = _fernet(_derive(new_password.encode('utf-8'), salt))
            c['key_p'] = base64.b64encode(key_p.encrypt(dk)).decode('ascii')
            c['has_pw'] = bool(new_password)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(json.dumps(c, ensure_ascii=False))
            ok += 1
        except Exception:
            fail += 1
    return ok, fail


def enc_exists(base_dir, site_name):
    """某站点是否存在加密容器（_cookie_str.txt.enc 或 _cookies.json.enc）"""
    root = _cookies_root(base_dir)
    for fn in (f"{site_name}_cookie_str.txt{_EXT}", f"{site_name}_cookies.json{_EXT}"):
        if os.path.exists(os.path.join(root, fn)):
            return True
    return False

# ---------------------------------------------------------------- 重置（分发/换机）
def reset_all(base_dir=None):
    """重置容器：删除 cookies 目录全部数据（含守卫文件），回到未设置状态。
    用途：程序被复制给其他人时，对方清除原主人的密码/登录数据，
    重新设置自己的密码。返回 True 表示已重置。"""
    if base_dir is None:
        base_dir = os.getcwd()
    root = _cookies_root(base_dir)
    try:
        if os.path.isdir(root):
            shutil.rmtree(root, ignore_errors=True)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- 安全问题（改密码验证）
def _q_path(base_dir):
    return os.path.join(_cookies_root(base_dir), SECURITY_FILE)


def _q_key(base_dir):
    return _fernet(_derive(machine_id().encode('utf-8'), _load_salt(base_dir)))


def save_security_questions(questions, base_dir=None):
    """保存安全问题：[(问题, 答案), ...]。答案仅存 PBKDF2 哈希（不存明文）。
    文件用本机密钥加密，仅本机可读。"""
    if base_dir is None:
        base_dir = os.getcwd()
    items = []
    for q, a in questions:
        q = (q or '').strip()
        a = (a or '').strip()
        if not q or not a:
            continue
        a_salt = os.urandom(16)
        a_hash = hashlib.pbkdf2_hmac('sha256', a.encode('utf-8'), a_salt, 100_000)
        items.append({
            'q': q,
            'a': base64.b64encode(a_hash).decode('ascii'),
            's': base64.b64encode(a_salt).decode('ascii'),
        })
    blob = json.dumps({'v': 1, 'items': items}, ensure_ascii=False).encode('utf-8')
    data = _q_key(base_dir).encrypt(blob)
    os.makedirs(_cookies_root(base_dir), exist_ok=True)
    with open(_q_path(base_dir), 'wb') as f:
        f.write(data)
    return True


def load_security_questions(base_dir=None):
    """返回问题文本列表（不含答案）"""
    if base_dir is None:
        base_dir = os.getcwd()
    try:
        p = _q_path(base_dir)
        if not os.path.exists(p):
            return []
        with open(p, 'rb') as f:
            data = f.read()
        obj = json.loads(_q_key(base_dir).decrypt(data).decode('utf-8'))
        return [it['q'] for it in obj.get('items', [])]
    except Exception:
        return []


def has_security_questions(base_dir=None):
    if base_dir is None:
        base_dir = os.getcwd()
    return bool(load_security_questions(base_dir))


def verify_security_answers(answers, base_dir=None):
    """answers: {问题: 答案}。全部问题回答正确返回 True。"""
    if base_dir is None:
        base_dir = os.getcwd()
    try:
        p = _q_path(base_dir)
        if not os.path.exists(p):
            return False
        with open(p, 'rb') as f:
            data = f.read()
        obj = json.loads(_q_key(base_dir).decrypt(data).decode('utf-8'))
        items = obj.get('items', [])
        if not items:
            return False
        for it in items:
            q = it['q']
            ans = (answers.get(q) or '').strip()
            if not ans:
                return False
            h = hashlib.pbkdf2_hmac('sha256', ans.encode('utf-8'),
                                    base64.b64decode(it['s']), 100_000)
            if not hmac.compare_digest(h, base64.b64decode(it['a'])):
                return False
        return True
    except Exception:
        return False
