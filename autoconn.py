# -*- coding: utf-8 -*-
"""
西安电子科技大学 校园网自动登录工具（新版 srun Portal，2023 年后启用的 portal 版本）

旧版接口 https://w.xidian.edu.cn/srun_portal_pc.php 已经返回 404，现在门户是
https://w.xidian.edu.cn/srun_portal_pc?ac_id=1&theme=pro ，认证流程为：

    1) GET /cgi-bin/get_challenge   -> 取出 challenge(令牌 token) 与 client_ip
    2) GET /cgi-bin/srun_portal     -> action=login 提交认证，其中
         password = '{MD5}' + HMAC-MD5(明文密码, token)
         info     = '{SRBX1}' + 自定义字母表 base64( XXTEA(json用户信息, 密钥=token) )
         chksum   = SHA1(token+用户名+token+hmd5+token+ac_id+token+ip+token+200+token+1+token+info)

用法：
    python autoconn.py            检查网络（必要时先拨号/连 Wi-Fi），未登录则自动登录（开机自启用这个）
    python autoconn.py --check    只检查 Wi-Fi / 网线 / 网络 / 登录状态，不做任何动作
    python autoconn.py --wifi     只执行"连上 WIFI_SSID 指定的无线网"这一步
    python autoconn.py --pppoe    只做"网线拨号（PPPoE）"，条目会自动创建
    python autoconn.py --pppoe-down  断开网线拨号
    python autoconn.py --logout   注销当前登录（用于验证脚本是否真的能上/下线）
    python autoconn.py --verbose  打印请求细节，方便排查问题

三条上网路径（脚本按顺序自动选择，全部失败才放弃）：
    1. 网线直连：有线网卡能拿到正常 IP -> 直接走 Portal 认证
    2. 网线拨号：插着网线但只有 169.254.x.x -> rasdial 拨号（PPPoE，账号密码同上）
    3. 无线：自动查找并连接 WIFI_SSID（默认 stu-xdwlan，开放式），再走 Portal 认证

依赖：requests（pip install requests）
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import uuid

import requests

try:
    from requests.packages.urllib3.exceptions import InsecureRequestWarning
    requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
except Exception:  # pragma: no cover
    pass


# ---------- 网络请求：一律直连，不走系统代理 ----------
# 本机开着 Clash / VPN 等系统代理时，校园网请求必须绕过代理：
# requests 默认会读取系统（注册表）代理，代理一旦关闭或异常，
# Portal 与外网探测就会全部失败（表现为「连不上、一直重试」）。
NO_PROXY = {'http': None, 'https': None, 'ftp': None}


def new_session():
    """新建一个忽略系统代理的 requests.Session（trust_env=False）。"""
    session = requests.Session()
    session.trust_env = False           # 忽略 HTTP_PROXY/系统代理
    session.proxies = dict(NO_PROXY)    # 显式声明该会话不用代理
    return session


# 界面版点「停止」时会把它置为 True，让重试/等待循环尽快退出
STOP_REQUESTED = False


# ---------- 读取界面版保存的私有配置（settings.json，不会被提交到仓库） ----------

_OBF_KEY = b'Auto-xdwlan-2024'      # 与界面版 app.py 一致


def _deobfuscate(text):
    """把界面版保存的 password_enc（异或 + base64）还原成明文。"""
    try:
        mixed = base64.b64decode(text.encode('ascii'))
    except Exception:
        return ''
    raw = bytes(byte ^ _OBF_KEY[index % len(_OBF_KEY)]
                for index, byte in enumerate(mixed))
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return ''


def _load_saved_settings():
    """读取与本脚本同目录的 settings.json（界面版生成的），没有就返回空字典。"""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'settings.json')
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


# ============================== 配置区（按需修改） ==============================

# 校园网账号与密码（西电统一身份认证）
# 公开仓库里留空，避免账号密码被提交；真实账号由界面版保存到 settings.json 后自动读入，
# 也可以直接在这里本地填上（本地改动不要提交）。
USERNAME = ''
PASSWORD = ''

# 运营商后缀：'' = 校园网，'@dx' = 中国电信，'@lt' = 中国联通，'@yd' = 中国移动
DOMAIN = ''

# Portal 地址与认证域（ac_id=1 就是西电的默认认证域）
PORTAL = 'https://w.xidian.edu.cn'
AC_ID = '1'

# 加密常量，与 Portal 前端 Portal.js 中保持一致，一般不需要改
ENCRYPT_TYPE = 1
ENCRYPT_N = 200
ENCRYPT_VER = 'srun_bx1'

# 冒充浏览器（Portal 会对 UA 做基本判断，保持默认即可）
USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0')
DEVICE_OS = 'Windows 10'    # 对应前端 os 参数
DEVICE_NAME = 'Windows'     # 对应前端 name 参数

# 断网重试：最多尝试 MAX_RETRY 次，每次间隔 RETRY_INTERVAL 秒
MAX_RETRY = 20
RETRY_INTERVAL = 15

# ---------- 断开后重连时「清旧会话」的力度 ----------
# 断网（拔网线 / 掉线 / 主动断开）后，账号在 NAS 上残留的在线会话会让 Portal 直接
# 返回 err_code=2「已有在线会话」，本机就再也登不上去。脚本会先查账号的在线设备列表
# （Portal 的「在线设备管理」接口），把挡路的会话注销掉：
#   True  = 先清本机自己的旧会话；外网仍不通，就把账号上其它在线设备也一起注销
#           （等效于在 zfw.xidian.edu.cn 里逐个踢设备），保证本机这次能登上去
#   False = 只清「看起来是本机」的会话（本机网卡 IP 对得上，或 OS / 客户端名对得上）
CLEAR_OTHER_DEVICES = True

# ---------- 无线网络（Wi-Fi）自动连接 ----------
# 脚本发现"Portal 不可达"时会自动去找并连接这个 SSID，然后继续认证
WIFI_SSID = 'stu-xdwlan'   # 西电学生无线网；不想让脚本动 Wi-Fi 就设成 ''
WIFI_AUTO_CONNECT = True
WIFI_CONNECT_WAIT = 30     # 等待关联成功的秒数
WIFI_SCAN_TIMEOUT = 30     # netsh 命令超时

# ---------- 有线拨号（PPPoE，网线） ----------
# 插着网线、但拿不到正常 IP（169.254.x.x）时，脚本会自己创建一个拨号连接并拨号，
# 账号密码默认沿用上面的 USERNAME / PASSWORD。
PPPOE_ENABLE = True
PPPOE_NAME = 'XidianPPPoE'   # 拨号连接名（会出现在 Windows“拨号”里，可手动使用）
PPPOE_USER = ''              # 留空 = 用上面的 USERNAME；运营商线路可写 '学号@dx' 等
PPPOE_PASSWORD = ''          # 留空 = 用上面的 PASSWORD
PPPOE_DIAL_TIMEOUT = 100     # 单次拨号最长等待秒数

# 单次 HTTP 请求超时（秒）
REQUEST_TIMEOUT = 10

# ---------- 从界面版保存的 settings.json 读入账号密码（私有文件，不提交） ----------
_saved_settings = _load_saved_settings()
if not USERNAME:
    USERNAME = str(_saved_settings.get('username') or '').strip()
if not PASSWORD and _saved_settings.get('password_enc'):
    PASSWORD = _deobfuscate(str(_saved_settings['password_enc']))
if not DOMAIN:
    DOMAIN = str(_saved_settings.get('domain') or '').strip()

# 判断"能上外网"用的探测地址：(url, 期望状态码)
# generate_204 类接口正常情况下一定返回 204 且响应体为空；
# 如果返回 200 且带内容，说明请求被 Portal 劫持（等于"没登录"）。
#
# 这里故意放了多个**互相独立**的地址（不同厂商、http / https 各一），只要有一个通就算联网：
#   * 以前只有 miui + 百度两条，而百度的 http 探测早就废了（现在返回 302 跳到 https，
#     脚本不跟跳转 → 永远算失败），等于全靠 miui 一条撑着；
#     偏偏这条一抖（CDN、DNS、校园网拦一下）就被当成"断网"，接着去把好端端的拨号拆了重拨；
#   * https 那条不会被校园网往响应里插东西，http 那条最快，两条都留着更保险。
INTERNET_PROBES = (
    ('http://connect.rom.miui.com/generate_204', 204),
    ('https://connect.rom.miui.com/generate_204', 204),
    ('https://connectivitycheck.platform.hicloud.com/generate_204', 204),
    ('https://detectportal.firefox.com/success.txt', 200),
    ('http://www.baidu.com/', 200),      # 会 302 跳到 https://www.baidu.com/，同站跳转算通
)

# 单个探测地址的最长等待秒数、以及一轮探测的总时间预算（秒）
# 目的是：离线时别在探测上耗太久，同时保证至少能试到两三个地址。
PROBE_TIMEOUT = 5
PROBE_BUDGET = 12

# 日志文件：默认不写文件（None）。日志只输出到控制台，界面版则显示在窗口的「运行日志」里。
# 想留档时自己赋一个路径即可，例如：autoconn.LOG_FILE = r'D:\tmp\autoconn.log'
LOG_FILE = None
LOG_MAX_BYTES = 512 * 1024

HEADERS = {
    'User-Agent': USER_AGENT,
    'Accept': 'application/json, text/javascript, */*; q=0.01',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Connection': 'keep-alive',
}

VERBOSE = False


# ============================== 通用工具 ==============================


def log(message, also_print=True):
    """带时间戳写日志：输出到控制台；只有设置了 LOG_FILE 才写文件（默认不写）。"""
    line = '[%s] %s' % (time.strftime('%Y-%m-%d %H:%M:%S'), message)
    if also_print:
        try:
            print(line)
            sys.stdout.flush()
        except Exception:
            pass
    if not LOG_FILE:
        return
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as handle:
            handle.write(line + '\n')
    except Exception:
        pass


def debug(message):
    if VERBOSE:
        log('    [debug] %s' % message)


def _trim_log():
    """日志文件过大时清空，避免无限增长（没设置 LOG_FILE 时什么都不做）。"""
    if not LOG_FILE:
        return
    try:
        if os.path.getsize(LOG_FILE) > LOG_MAX_BYTES:
            os.remove(LOG_FILE)
    except Exception:
        pass


# ---------- srun 加密相关：HMAC-MD5 / SHA1 / 自定义 base64 / XXTEA ----------

# Portal.js 中 base64.setAlpha(...) 使用的自定义字母表
SRUN_B64_ALPHABET = 'LVoJPiCN2R8G90yg+hmFHuacZ1OWMnrsSTXkYpUq/3dlbfKwv6xztjI7DeBE45QA'
STD_B64_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'
_B64_TABLE = bytes.maketrans(STD_B64_ALPHABET.encode('ascii'),
                             SRUN_B64_ALPHABET.encode('ascii'))

_MASK32 = 0xFFFFFFFF
_DELTA = 0x9E3779B9


def hmac_md5_hex(password, token):
    """对应前端 md5(password, token)：HMAC-MD5(key=token, msg=password) 的小写十六进制。"""
    return hmac.new(token.encode('utf-8'),
                    password.encode('utf-8'),
                    hashlib.md5).hexdigest()


def sha1_hex(text):
    """对应前端 sha1(str)。"""
    return hashlib.sha1(text.encode('utf-8')).hexdigest()


def md5_hex(text):
    """对应前端 md5(str)：普通 MD5 十六进制（取「在线设备」列表时要用 md5(密码)）。"""
    return hashlib.md5(text.encode('utf-8')).hexdigest()


def srun_base64(raw):
    """使用西电 Portal 的自定义字母表做 base64 编码。"""
    return base64.b64encode(raw).translate(_B64_TABLE).decode('ascii')


def _bytes_to_words(data, append_length):
    """每 4 个字节按小端拼成一个 32 位字，可选在末尾追加长度（对应前端函数 s）。"""
    length = len(data)
    words = []
    for index in range(0, length, 4):
        word = 0
        for offset in range(4):
            position = index + offset
            if position < length:
                word |= data[position] << (8 * offset)
        words.append(word & _MASK32)
    if append_length:
        words.append(length & _MASK32)
    return words


def _xxtea_encrypt(words, key):
    """XXTEA 加密（小端、32 位回绕），与 Portal.js 里的 encode() 完全等价。"""
    count = len(words) - 1
    if count < 1:
        return words
    z = words[count] & _MASK32
    y = words[0] & _MASK32
    total = 0
    rounds = 6 + 52 // (count + 1)
    while rounds > 0:
        rounds -= 1
        total = (total + _DELTA) & _MASK32
        e = (total >> 2) & 3
        for p in range(count):
            y = words[p + 1] & _MASK32
            mixed = ((z >> 5) ^ (y << 2)) & _MASK32
            mixed = (mixed + ((y >> 3) ^ (z << 4) ^ (total ^ y))) & _MASK32
            mixed = (mixed + (key[(p & 3) ^ e] ^ z)) & _MASK32
            z = words[p] = (words[p] + mixed) & _MASK32
        y = words[0] & _MASK32
        mixed = ((z >> 5) ^ (y << 2)) & _MASK32
        mixed = (mixed + ((y >> 3) ^ (z << 4) ^ (total ^ y))) & _MASK32
        mixed = (mixed + (key[(count & 3) ^ e] ^ z)) & _MASK32
        z = words[count] = (words[count] + mixed) & _MASK32
    return words


def _words_to_bytes(words):
    """把 32 位字拆回小端字节串（对应前端函数 l）。"""
    raw = bytearray()
    for word in words:
        raw.append(word & 0xFF)
        raw.append((word >> 8) & 0xFF)
        raw.append((word >> 16) & 0xFF)
        raw.append((word >> 24) & 0xFF)
    return bytes(raw)


def encode_info(info, token):
    """生成登录请求里的 info 字段：'{SRBX1}' + 自定义 base64(XXTEA(JSON))。"""
    data = json.dumps(info, separators=(',', ':')).encode('utf-8')
    key = _bytes_to_words(token.encode('utf-8'), False)
    while len(key) < 4:
        key.append(0)
    words = _xxtea_encrypt(_bytes_to_words(data, True), key)
    return '{SRBX1}' + srun_base64(_words_to_bytes(words))


# ============================== Portal 接口调用 ==============================


def _callback_payload(text):
    """把 JSONP 响应（形如 jQuery123({...})）解析成 dict，同时兼容纯 JSON。"""
    text = (text or '').strip()
    if not text:
        return {}
    match = re.match(r'^[\w$\.\[\]]*\s*\(\s*(\{.*\})\s*\)\s*;?\s*$', text, re.S)
    if match:
        text = match.group(1)
    try:
        return json.loads(text)
    except ValueError:
        # rad_user_info 这类接口会直接返回逗号分隔的字符串
        return {'raw': text}


def _api_get(session, path, params, use_ssl=True):
    """带 callback / 时间戳的 GET（Portal 前端用的是 jsonp，这里等价实现）。"""
    query = dict(params)
    query['callback'] = 'jQuery%d' % int(time.time() * 1000)
    query['_'] = str(int(time.time() * 1000))
    base = PORTAL if use_ssl else PORTAL.replace('https://', 'http://')
    url = base + path
    debug('GET %s %s' % (url, json.dumps(query, ensure_ascii=False)))
    try:
        response = session.get(url, params=query, headers=HEADERS,
                               timeout=REQUEST_TIMEOUT, verify=True,
                               proxies=NO_PROXY)
    except requests.exceptions.SSLError:
        debug('HTTPS 证书校验失败，改为不校验证书重试')
        response = session.get(url, params=query, headers=HEADERS,
                               timeout=REQUEST_TIMEOUT, verify=False,
                               proxies=NO_PROXY)
    except requests.exceptions.ConnectionError:
        if use_ssl:
            debug('HTTPS 连不上，换 HTTP 再试一次')
            return _api_get(session, path, params, use_ssl=False)
        raise
    response.raise_for_status()
    body = response.content.decode('utf-8', 'replace')
    debug('响应: %s' % body[:500])
    return _callback_payload(body)


def get_challenge(session, ip=''):
    """取认证令牌 token(challenge)，同时返回 Portal 识别到的客户端 IP。"""
    return _api_get(session, '/cgi-bin/get_challenge',
                    {'username': USERNAME + DOMAIN, 'ip': ip})


def get_online_info(session):
    """查询当前 IP 的在线信息（未登录时 Portal 会返回 not_online_error）。"""
    try:
        return _api_get(session, '/cgi-bin/rad_user_info', {})
    except Exception as error:
        debug('rad_user_info 查询失败: %s' % error)
        return {}


def who_is_online(session):
    """返回已登录的账号名，未登录则返回 None。"""
    info = get_online_info(session)
    error = str(info.get('error', ''))
    if error and error != 'ok':
        return None
    for key in ('user_name', 'username', 'user'):
        if info.get(key):
            return str(info[key])
    raw = str(info.get('raw', ''))
    if raw and not raw.startswith('<'):
        name = raw.split(',')[0].strip()
        return name or None
    return None


def _short_host(url):
    """把探测地址缩成 connect.rom.miui.com(http) 这种短标签，方便写一行日志。"""
    host = re.sub(r'^[a-z]+://', '', str(url), flags=re.I).split('/', 1)[0]
    scheme = 'https' if url.lower().startswith('https') else 'http'
    return '%s(%s)' % (host, scheme)


def _same_site(url, location):
    """跳转目标是不是同一个站点（http→https、带不带 www 都算同一个）。"""
    def host(target):
        target = re.sub(r'^[a-z]+://', '', str(target), flags=re.I)
        target = target.split('/', 1)[0].split(':', 1)[0].strip().lower()
        return target[4:] if target.startswith('www.') else target
    target = host(location)
    return bool(target) and host(url) == target


def _probe_internet(url, expect_status, timeout):
    """探测一个地址：返回 (是否算通, 简短说明)。"""
    response = None
    try:
        response = requests.get(url, headers=HEADERS, timeout=timeout,
                                allow_redirects=False, proxies=NO_PROXY, stream=True)
        status = response.status_code
        location = response.headers.get('Location', '')
        if 'w.xidian.edu.cn' in location:
            return False, '被重定向到 Portal 页'
        if 300 <= status < 400:
            # 204 类探测出现跳转 = 被劫持；200 类探测跳到同站 https 只是站点自己升级
            if expect_status == 204 or not _same_site(url, location):
                return False, '跳转到 %s' % (location[:60] or '未知地址')
            return True, '%s -> %s（跳转到 %s）' % (url, status, location[:60])
        if status != expect_status:
            return False, '状态码 %s（期望 %s）' % (status, expect_status)
        # 只读一小块响应体，够判断有没有被塞 Portal 页就行（省流量也不拖时间）
        chunk = b''
        try:
            for piece in response.iter_content(2048):
                chunk = piece or b''
                break
        except Exception:
            chunk = b''
        body = chunk.lower().replace(b'\x00', b'')
        hijacked = b'srun' in body or b'xidian' in body
        if expect_status == 204:
            hijacked = hijacked or b'portal' in body or bool(body.strip(b'\r\n \t'))
        if hijacked:
            return False, '响应体像 Portal 页面'
        return True, '%s -> %s' % (url, status)
    except requests.exceptions.RequestException as error:
        return False, str(error)[:70]
    finally:
        if response is not None:
            response.close()


def check_internet():
    """探测是否真的能上外网：返回 (是否通, 说明)。

    注意：
      * 未登录时校园网会把请求劫持到 Portal 页面（HTTP 200 + 一个网页），所以不能只看
        200/204，还要看状态码是否与探测地址的约定一致、内容是不是 Portal 页；
      * 探测地址有好几个（不同厂商、http/https 都有），只要有一个通就算联网 ——
        单个地址抽风不能当成"断网"，否则会白白把正在用的拨号拆掉重拨；
      * 一轮探测有时间预算（PROBE_BUDGET），离线时不至于卡很久。
    """
    global _LAST_PROBE_SUMMARY
    deadline = time.time() + PROBE_BUDGET
    reasons = []
    for url, expect_status in INTERNET_PROBES:
        timeout = max(2, min(PROBE_TIMEOUT, deadline - time.time()))
        ok, reason = _probe_internet(url, expect_status, timeout)
        if ok:
            _LAST_PROBE_SUMMARY = None      # 恢复联网后，下次失败要重新报原因
            return True, reason
        debug('外网探测 %s 不通: %s' % (url, reason))
        reasons.append('%s %s' % (_short_host(url), reason))
        if time.time() >= deadline:
            reasons.append('（到时间预算，后面几个不试了）')
            break
    summary = '；'.join(reasons)
    if summary != _LAST_PROBE_SUMMARY:      # 同样的原因不重复刷屏
        _LAST_PROBE_SUMMARY = summary
        log('外网探测都没通过：%s' % summary)
    return False, '外网不通（%d 个探测地址都没通，原因见运行日志）' % len(INTERNET_PROBES)


# 门户域名上次解析到的 IP：用来做「不依赖 DNS」的校园网活性检查
_PORTAL_LAST_IP = ''
_LAST_PROBE_SUMMARY = None


def _remember_portal_ip():
    """记下门户域名解析到的 IP（判断链路活性时要用，见 campus_link_alive()）。"""
    global _PORTAL_LAST_IP
    try:
        host = PORTAL.split('//', 1)[-1].split('/', 1)[0].split(':', 1)[0]
        _PORTAL_LAST_IP = socket.gethostbyname(host)
    except Exception as error:
        debug('解析门户 IP 失败: %s' % error)


def campus_link_alive(timeout=None):
    """校园网那一段链路是不是还活着（不看外网、也不依赖 DNS）。

    做法：直接按上次解析到的门户 IP 访问 https://<IP>/cgi-bin/get_challenge
    （门户的 80 端口不通，443 可以；带 IP 访问时不能用证书校验）。

    返回 True / False / None（None = 还不知道门户 IP，判断不了）。
    用途：外网探测失败时，先看校园网这条路是不是真的断了 —— 如果门户按 IP 还能应答，
    那就只是 DNS / 上游 / 代理的问题，不该把用户正在用的拨号拆了重拨。
    """
    if not _PORTAL_LAST_IP:
        return None
    url = 'https://%s/cgi-bin/get_challenge' % _PORTAL_LAST_IP
    try:
        response = requests.get(url, params={'username': USERNAME + DOMAIN, 'ip': '',
                                             'callback': 'jQuery1',
                                             '_': str(int(time.time()))},
                                headers=HEADERS, proxies=NO_PROXY, verify=False,
                                timeout=timeout or PROBE_TIMEOUT, stream=True)
        try:
            return response.status_code == 200
        finally:
            response.close()
    except requests.exceptions.RequestException as error:
        debug('按 IP 探测门户(%s)失败: %s' % (_PORTAL_LAST_IP, error))
        return False


def portal_reachable(timeout=None):
    """Portal 是否可达（用来判断是不是还没拿到 IP / 不在校园网）。"""
    try:
        response = requests.get(PORTAL + '/cgi-bin/get_challenge',
                                params={'username': USERNAME + DOMAIN, 'ip': '',
                                        'callback': 'jQuery1', '_': str(int(time.time()))},
                                headers=HEADERS, proxies=NO_PROXY,
                                timeout=timeout or REQUEST_TIMEOUT)
        if response.status_code == 200:
            _remember_portal_ip()
            return True
        return False
    except requests.exceptions.RequestException as error:
        debug('Portal 不可达: %s' % error)
        return False


def build_login_params(token, ip, challenge_ip=''):
    """按 Portal.js 的规则拼出 action=login 的全部参数。"""
    username = USERNAME + DOMAIN
    auth_ip = ip or challenge_ip
    info = encode_info({
        'username': username,
        'password': PASSWORD,
        'ip': auth_ip,
        'acid': AC_ID,
        'enc_ver': ENCRYPT_VER,
    }, token)
    hmd5 = hmac_md5_hex(PASSWORD, token)
    # chksum = SHA1(token + 用户名 + token + hmd5 + token + ac_id + token + ip
    #               + token + n + token + type + token + info)
    checksum_source = (token + username
                       + token + hmd5
                       + token + str(AC_ID)
                       + token + auth_ip
                       + token + str(ENCRYPT_N)
                       + token + str(ENCRYPT_TYPE)
                       + token + info)
    return {
        'action': 'login',
        'username': username,
        'password': '{MD5}' + hmd5,
        'os': DEVICE_OS,
        'name': DEVICE_NAME,
        'double_stack': '0',
        'chksum': sha1_hex(checksum_source),
        'info': info,
        'ac_id': str(AC_ID),
        'ip': auth_ip,
        'n': str(ENCRYPT_N),
        'type': str(ENCRYPT_TYPE),
    }


def login(ip=''):
    """执行一次完整登录：取 token -> 提交认证。返回 (是否成功, 说明)。"""
    session = new_session()
    try:
        challenge = get_challenge(session, ip)
    except Exception as error:
        return False, '获取 token 失败: %s' % error

    token = challenge.get('challenge')
    if not token:
        return False, '获取 token 失败: %s' % (challenge.get('error_msg') or challenge)

    params = build_login_params(token, ip, str(challenge.get('client_ip') or ''))
    try:
        result = _api_get(session, '/cgi-bin/srun_portal', params)
    except Exception as error:
        return False, '提交认证请求失败: %s' % error

    error_code = str(result.get('error', ''))
    if error_code == 'ok' or result.get('res') == 'ok':
        return True, '认证成功（%s）' % (result.get('suc_msg') or 'ok')
    if error_code == 'ip_already_online_error' or \
            result.get('suc_msg') == 'ip_already_online_error':
        return True, '本机 IP 已经在线，无需重复登录'
    if is_already_online_error(result):
        # err_code=2 = 本机/账号上已有在线会话。只要外网确实通，就当「已在线」处理：
        # 不需要重复认证，更不能去踢会话（踢了会把正在用的好连接弄断）。
        connected, detail = check_internet()
        if connected:
            return True, '账号已在线（%s），无需重复认证' % detail
        return False, '认证失败: %s' % _explain_error(result)
    return False, '认证失败: %s' % _explain_error(result)


def _explain_error(result):
    """把 Portal 返回的错误码翻译成人能看懂的说明。"""
    code = str(result.get('error', ''))
    message = result.get('error_msg') or result.get('suc_msg') or code
    known = {
        'sign_error': '签名校验失败（token 过期或参数不对），脚本会自动重试',
        'challenge_expire_error': 'token 已过期（60 秒内未完成认证），脚本会自动重试',
        'auth_info_error': 'info 加密串格式错误',
        'user_not_found_error': '账号不存在，请检查 USERNAME',
        'password_error': '密码错误，请检查 PASSWORD',
        'ip_already_online_error': '该 IP 已在线',
        'not_online_error': '该 IP 当前不在线',
    }
    if code in known:
        return '%s（%s）' % (known[code], message)
    if _looks_like_err_code_2(message):
        return ('INFO Error，err_code=2：本机/账号上已经有一个在线会话（走网线拨号 PPPoE 时，'
                '拨号本身就是用同一套账号认证的，所以再走 Portal 必然被拒）；'
                '外网通时无需重复认证，外网确实不通才需要清掉旧会话')
    if str(result.get('ecode')) == 'E2620':
        return '账号在线设备数已达上限(E2620)，请在自助服务里踢掉其它设备'
    return str(message)


def _looks_like_err_code_2(message):
    """判断错误信息是不是 srun 的 err_code=2。

    注意：西电 Portal 会把中文逗号按 GBK 重新编码，返回的实际字符串是
    `INFO Error锛宔rr_code=2`（"err_code" 的 e 被吃掉，变成 "锛宔rr_code"），
    所以不能直接匹配 `'err_code=2'`，只匹配不受乱码影响的 `code=2` 部分。
    """
    return re.search(r'code\s*=\s*2\b', str(message)) is not None


def is_already_online_error(result):
    """Portal 返回的是不是「本机 / 账号已有在线会话」这类错误。

    * `error == 'ip_already_online_error'`：该 IP 已经在线；
    * `error_msg` 里带 `err_code=2`：srun 的会话冲突（网线拨号 PPPoE 在线时必然出现）。
    """
    code = str(result.get('error') or '')
    message = str(result.get('error_msg') or result.get('suc_msg') or '')
    if code == 'ip_already_online_error' or 'ip_already_online' in message:
        return True
    return _looks_like_err_code_2(message)


def logout(ip=''):
    """注销当前 IP 的登录（把网断掉，用于验证脚本真的生效）。"""
    session = new_session()
    try:
        challenge = get_challenge(session, ip)
        auth_ip = ip or str(challenge.get('client_ip') or '')
        result = _api_get(session, '/cgi-bin/srun_portal', {
            'action': 'logout',
            'username': USERNAME + DOMAIN,
            'ac_id': str(AC_ID),
            'ip': auth_ip,
        })
    except Exception as error:
        return False, '注销请求失败: %s' % error
    if str(result.get('error')) == 'ok' or result.get('res') == 'ok':
        return True, '已注销，网络应已断开'
    return False, '注销失败: %s' % _explain_error(result)


def kick_session(ip=''):
    """用 Portal 的"设备下线"接口（/cgi-bin/rad_user_dm）强制踢掉本机旧会话。

    实测：当本机 IP 上残留了状态异常的旧会话时，普通 action=logout 不一定有效，
    而 rad_user_dm 能把会话真正踢掉，随后登录就会返回 login_ok。
    """
    session = new_session()
    target = ip
    if not target:
        info = get_online_info(session)
        target = str(info.get('online_ip') or '')
    if not target or target == '::':
        return False, '没有查到需要清理的旧会话'
    now = int(time.time())
    sign = sha1_hex(str(now) + USERNAME + target + '1' + str(now))
    try:
        result = _api_get(session, '/cgi-bin/rad_user_dm', {
            'ip': target,
            'username': USERNAME + DOMAIN,
            'time': str(now),
            'unbind': '1',
            'sign': sign,
        })
    except Exception as error:
        return False, '强制下线请求失败: %s' % error
    if str(result.get('error')) == 'ok' or result.get('res') == 'ok':
        return True, '已强制下线 %s 上的旧会话' % target
    return False, '强制下线未成功: %s' % _explain_error(result)


def online_sessions(session=None):
    """列出该账号当前的所有在线会话：[{ip, os_name, class_name, add_time}, ...]。

    两条路（前一条不通就退到下一条）：
      1. Portal 门户网页上「在线设备管理」用的接口
         `/v1/srun_portal_online?user_name=账号&password=md5(密码)`，
         账号在每个 IP 上的会话都能看到（和手动去 zfw.xidian.edu.cn 踢设备看到的一样）；
      2. 退回 `rad_user_info`：对本机各个 IP 逐个查，响应里的 `online_device_detail`
         就是账号的全部会话（要求其中某个 IP 正好在线，所以只在第 1 条失败时兜底）。
    """
    session = session or new_session()
    sessions = _portal_online_sessions(session) or _rad_online_sessions(session)
    # 同一个 IP 上会挂好几条会话（每条链路一条），按 IP 去重：注销时一个 IP 一次就够
    unique = {}
    for item in sessions:
        unique.setdefault(item['ip'], item)
    return list(unique.values())


def _portal_online_sessions(session):
    """按 Portal 的「在线设备管理」接口取会话列表；取不到返回 []。"""
    if not USERNAME or not PASSWORD:
        debug('没有账号/密码，跳过在线设备列表接口')
        return []
    try:
        result = _api_get(session, '/v1/srun_portal_online', {
            'user_name': USERNAME + DOMAIN,
            'password': md5_hex(PASSWORD),
        })
    except Exception as error:
        debug('在线设备列表请求失败: %s' % error)
        return []
    rows = result.get('data') if isinstance(result, dict) else None
    if not isinstance(rows, list):
        debug('在线设备列表返回异常: %s' % str(result)[:200])
        return []
    sessions = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        ip = str(item.get('ip') or '').strip()
        if not ip:
            continue
        sessions.append({
            'ip': ip,
            'os_name': str(item.get('os_name') or ''),
            'class_name': '',            # 这个接口不给客户端名字，只有 OS
            'add_time': str(item.get('add_time') or ''),
        })
    return sessions


def _rad_online_sessions(session):
    """兜底：用 rad_user_info(+online_device_detail) 拼出会话列表（去重）。"""
    found = {}
    candidates = ['']
    for ip in local_ipv4_addresses():
        if ip not in candidates:
            candidates.append(ip)
    for ip in candidates:
        try:
            info = _api_get(session, '/cgi-bin/rad_user_info', {'ip': ip})
        except Exception as error:
            debug('rad_user_info(%s) 查询失败: %s' % (ip or '本机', error))
            continue
        if str(info.get('error')) != 'ok':
            continue
        if str(info.get('user_name') or '').lower() != (USERNAME + DOMAIN).lower():
            continue        # 这个 IP 上在线的是别的账号，不能动
        detail = info.get('online_device_detail')
        if isinstance(detail, str):
            try:
                detail = json.loads(detail)
            except ValueError:
                detail = None
        if not isinstance(detail, dict):
            continue
        for entry in detail.values():
            if not isinstance(entry, dict):
                continue
            target = str(entry.get('ip') or '').strip()
            if not target:
                continue
            found[target] = {
                'ip': target,
                'os_name': str(entry.get('os_name') or ''),
                'class_name': str(entry.get('class_name') or ''),
                'add_time': '',
            }
    return list(found.values())


def _looks_like_ours(item, local_ips):
    """这个在线会话是不是本机自己留下的。

    * 网卡 IP 对得上 → 肯定是本机（当前或刚用过的 IP）；
    * 客户端名(class_name)和 OS 名都对得上（rad_user_info 里有 class_name）→ 本机；
    * `/v1/srun_portal_online` 只返回 OS 名，OS 名对得上就当作「像是本机」
      （同一个账号在另一台 Windows 10 上登录也会被算进来，所以实在不想动别人时
      把 CLEAR_OTHER_DEVICES 设成 False 也要接受这个误差；默认 True，本来就都会清）。
    """
    if item['ip'] in local_ips:
        return True
    if item.get('os_name') != DEVICE_OS:
        return False
    return item.get('class_name') in ('', DEVICE_NAME)


def clear_stale_sessions():
    """清掉挡着本机登录的旧会话：本机自己的优先，必要时连账号上其它在线设备一起清。

    背景：`err_code=2` = 账号上已经有在线会话，Portal 就会拒绝新的登录；而这个会话
    往往不是本机当前的 IP（例如网线拨号断掉后会话还留在 NAS 上、本机换成了别的 IP），
    所以不能只看本机 IP，得按「在线设备」列表逐个注销（等价于在门户里踢设备）。

    返回 (是否清掉了至少一个, 说明)。
    """
    sessions = online_sessions()
    if not sessions:
        # 列表都拿不到，就退回老办法：对着本机当前 IP 先设备下线、再普通注销
        ok, message = kick_session()
        if not ok:
            ok2, message2 = logout()
            ok = ok2
            message = '%s / %s' % (message, message2)
        return ok, message

    local_ips = set(local_ipv4_addresses())
    mine = [item for item in sessions if _looks_like_ours(item, local_ips)]
    others = [item for item in sessions if item not in mine]
    if mine:
        log('本机残留的旧会话：%s' % '、'.join(item['ip'] for item in mine))
    if others:
        if CLEAR_OTHER_DEVICES:
            log('账号上还有其它在线设备，一并注销（不想这样就把 CLEAR_OTHER_DEVICES 设成 False）：%s'
                % '、'.join(item['ip'] for item in others))
        else:
            log('账号上还有其它在线设备（按配置不动它们）：%s'
                % '、'.join(item['ip'] for item in others))

    results = []
    cleared = False
    for item in (mine + others if CLEAR_OTHER_DEVICES else mine):
        ip = item['ip']
        ok, message = kick_session(ip)
        if not ok:
            ok2, message2 = logout(ip)
            ok = ok2
            message = '%s / %s' % (message, message2)
        cleared = cleared or ok
        results.append('%s %s' % (ip, '已注销' if ok else '没清掉（%s）' % message))
        time.sleep(1)
    if not results:
        return False, '账号上没有查到需要清理的在线会话'
    return cleared, '；'.join(results)


# ============================== 无线网络（Wi-Fi） ==============================
# 说明：netsh 的中文输出可能按 UTF-8 或 GBK 编码，而且界面文字是本地化的，
# 所以这里只解析 ASCII 关键字（SSID / name=...），不解析任何中文提示，避免编码坑。

_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)

# 记录上一次 Wi-Fi 失败原因，避免每轮重试都往日志里刷同样的话
_WIFI_LAST_FAILURE = None


def _netsh(args, timeout=None):
    """执行 netsh 命令并返回文本输出（静默、无窗口）。"""
    command = ['netsh'] + list(args)
    try:
        proc = subprocess.run(command, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT,
                              timeout=timeout or WIFI_SCAN_TIMEOUT,
                              creationflags=_NO_WINDOW)
    except Exception as error:
        debug('执行失败 %s: %s' % (' '.join(command), error))
        return ''
    raw = proc.stdout or b''
    if not raw:
        return ''
    for encoding in ('utf-8', 'gbk'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', 'replace')


def wifi_current_ssid():
    """返回本机 Wi-Fi 当前关联的 SSID；没连上返回 None。"""
    text = _netsh(['wlan', 'show', 'interfaces'])
    for line in text.splitlines():
        key, sep, value = line.partition(':')
        # 只认 SSID 这一行（BSSID 不算），值形如 "SSID   : stu-xdwlan"
        if sep and key.strip() == 'SSID':
            value = value.strip()
            if value:
                return value
    return None


def wifi_profile_exists(ssid):
    """系统里是否已经保存过该 SSID 的无线配置文件。"""
    text = _netsh(['wlan', 'show', 'profiles'])
    for line in text.splitlines():
        key, sep, value = line.partition(':')
        if sep and value.strip() == ssid:
            return True
    return False


def wifi_network_visible(ssid):
    """扫描结果里能否看到该 SSID。"""
    text = _netsh(['wlan', 'show', 'networks'])
    for line in text.splitlines():
        key, sep, value = line.partition(':')
        if sep and key.strip().startswith('SSID') and value.strip() == ssid:
            return True
    return False


def _quote(value):
    """SSID 里带空格时 netsh 需要引号。"""
    return '"%s"' % value if ' ' in value else value


def _add_open_profile(ssid):
    """给开放式（无密码）无线网创建配置文件，用于系统里没保存过该网络的情况。"""
    xml = ('<?xml version="1.0"?>\n'
           '<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">\n'
           '  <name>%s</name>\n'
           '  <SSIDConfig><SSID><name>%s</name></SSID></SSIDConfig>\n'
           '  <connectionType>ESS</connectionType>\n'
           '  <connectionMode>auto</connectionMode>\n'
           '  <MSM><security><authEncryption>\n'
           '    <authentication>open</authentication>\n'
           '    <encryption>none</encryption>\n'
           '    <useOneX>false</useOneX>\n'
           '  </authEncryption></security></MSM>\n'
           '</WLANProfile>\n' % (ssid, ssid))
    path = os.path.join(tempfile.gettempdir(), 'autoconn_%s.xml' % ssid)
    try:
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write(xml)
    except Exception as error:
        return False, '写配置文件失败: %s' % error
    output = _netsh(['wlan', 'add', 'profile', 'filename=%s' % path, 'user=current'])
    debug('add profile 输出: %s' % output.strip()[:200])
    try:
        os.remove(path)      # 用完立刻删掉，不在磁盘上留临时文件
    except Exception:
        pass
    return wifi_profile_exists(ssid), 'add profile'


def connect_wifi(ssid=None, wait=None, verbose=True):
    """确保 Wi-Fi 连到指定 SSID（默认 stu-xdwlan）。返回 (是否已连上, 说明)。"""
    ssid = ssid if ssid is not None else WIFI_SSID
    wait = wait or WIFI_CONNECT_WAIT
    if not ssid:
        return False, '未配置 WIFI_SSID，跳过 Wi-Fi 连接'
    if not WIFI_AUTO_CONNECT:
        return False, 'WIFI_AUTO_CONNECT 已关闭，跳过 Wi-Fi 连接'

    current = wifi_current_ssid()
    if current == ssid:
        if verbose:
            log('Wi-Fi 已连接 %s，无需处理' % ssid)
        return True, '已连接 %s' % ssid
    if current:
        if verbose:
            log('Wi-Fi 当前连着 %s，准备切换到 %s' % (current, ssid))
    else:
        if verbose:
            log('Wi-Fi 未连接，尝试连接 %s …' % ssid)

    if not wifi_profile_exists(ssid):
        log('系统里没有 %s 的无线配置文件，尝试自动创建' % ssid)
        if not wifi_network_visible(ssid):
            return False, '扫描不到 %s（可能不在校园网覆盖范围内）' % ssid
        ok, message = _add_open_profile(ssid)
        if not ok:
            return False, '创建 %s 的无线配置文件失败（%s）' % (ssid, message)

    if not wifi_network_visible(ssid):
        return False, '当前扫描不到 %s，稍后重试' % ssid

    _netsh(['wlan', 'connect', 'name=%s' % _quote(ssid), 'ssid=%s' % _quote(ssid)])

    waited = 0
    while waited < wait:
        time.sleep(2)
        waited += 2
        if wifi_current_ssid() == ssid:
            log('已连上 Wi-Fi %s（等待 %d 秒）' % (ssid, waited))
            return True, '已连接 %s' % ssid
    return False, '连接 %s 超时（%d 秒）' % (ssid, wait)


# ============================== 有线拨号（PPPoE） ==============================
# 说明：
#  * Windows 没有命令行"新建拨号连接"的接口，所以这里按拨号本(rasphone.pbk)的格式
#    自己写一个 Type=5（宽带/PPPoE）条目。关键字段要和 Windows 自己创建的一致：
#        Device=WAN Miniport (PPPOE)   DEVICE=PPPoE   Port=<PPPoE?-0>
#    （写成 DEVICE=vpn / Port=VPN2-0 时 Windows 也能找到条目、也会发起发现报文，
#      但拨号会失败——错误 868，实测踩过这个坑。）
#  * 拨号本必须是单字节编码（UTF-8/ANSI 都行），UTF-16 一律不认（错误 623）。
#  * 脚本会优先复用系统里已有的拨号连接（例如 Windows 建的"宽带连接"）。

PPPOE_PBK_USER = os.path.join(
    os.environ.get('APPDATA', ''),
    r'Microsoft\Network\Connections\Pbk\rasphone.pbk')
PPPOE_PBK_ALL = r'C:\ProgramData\Microsoft\Network\Connections\Pbk\rasphone.pbk'

_PPPOE_TEMPLATE = """[{name}]
Encoding=1
PBVersion=8
Type=5
AutoLogon=0
UseRasCredentials=0
DialParamsUID={uid}
Guid={guid}
VpnStrategy=0
ExcludedProtocols=0
LcpExtensions=1
DataEncryption=8
SwCompression=0
NegotiateMultilinkAlways=0
SkipDoubleDialDialog=0
DialMode=0
OverridePref=15
RedialAttempts=1
RedialSeconds=30
IdleDisconnectSeconds=0
RedialOnLinkFailure=1
CallbackMode=0
CustomDialDll=
CustomDialFunc=
CustomRasDialDll=
ForceSecureCompartment=0
DisableIKENameEkuCheck=0
AuthRestrictions=552
IpPrioritizeRemote=1
IpInterfaceMetric=0
IpHeaderCompression=0
IpAddress=0.0.0.0
IpDnsAddress=0.0.0.0
IpDns2Address=0.0.0.0
IpWinsAddress=0.0.0.0
IpWins2Address=0.0.0.0
IpAssign=1
IpNameAssign=1
IpDnsFlags=0
IpNBTFlags=0
TcpWindowSize=0
UseFlags=3
IpSecFlags=0
Ipv6Assign=1
Ipv6Address=::
Ipv6PrefixLength=0
Ipv6PrioritizeRemote=1
Ipv6InterfaceMetric=0
Ipv6NameAssign=1
Ipv6DnsAddress=::
Ipv6Dns2Address=::
NETCOMPONENTS=
ms_msclient=0
ms_server=0
{port_line}
Device=WAN Miniport (PPPOE)
DEVICE=PPPoE
PhoneNumber=
AreaCode=
CountryCode=0
CountryID=0
UseDialingRules=0
Comment=
FriendlyName=
LastSelectedPhone=0
PromoteAlternates=0
TryNextAlternateOnFail=1
"""


def _run_command(args, timeout=60, encoding='gbk'):
    """执行外部命令，返回 (退出码, 文本输出)。"""
    try:
        proc = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=timeout, creationflags=_NO_WINDOW)
    except subprocess.TimeoutExpired:
        return 999, '命令超时（%s 秒）' % timeout
    except Exception as error:
        return 998, '命令执行失败: %s' % error
    return proc.returncode, proc.stdout.decode(encoding, 'replace')


def _powershell_json(script):
    """执行 PowerShell 脚本并返回 JSON 结果（强制 UTF-8 输出，避免中文乱码）。"""
    command = '[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;' + script
    code, text = _run_command(['powershell', '-NoProfile', '-Command', command],
                              timeout=40, encoding='utf-8')
    text = text.lstrip('\ufeff').strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        debug('PowerShell 输出不是 JSON: %s' % text[:200])
        return None


def wired_adapters():
    """列出物理有线网卡：[{name, link, ip}, ...]。"""
    script = ("Get-NetAdapter -Physical | Where-Object {$_.MediaType -eq '802.3'} | "
              "ForEach-Object { "
              "$ip = (Get-NetIPAddress -InterfaceAlias $_.Name -AddressFamily IPv4 "
              "-ErrorAction SilentlyContinue | Select-Object -First 1).IPAddress; "
              "[pscustomobject]@{ Name=$_.Name; Status=$_.Status; IP=$ip } } | "
              "ConvertTo-Json -Compress")
    data = _powershell_json(script)
    if not data:
        return []
    if isinstance(data, dict):
        data = [data]
    result = []
    for item in data:
        if not isinstance(item, dict):
            continue
        result.append({
            'name': str(item.get('Name') or ''),
            'link': str(item.get('Status') or '').lower() in ('up', 'connected'),
            'ip': str(item.get('IP') or ''),
        })
    return result


def ethernet_link_up():
    """是否插着网线（物理有线网卡链路 Up）。"""
    return any(item['link'] for item in wired_adapters())


def ethernet_has_ipv4():
    """有线网卡是否拿到了可用 IP（排除 169.254.x.x 这种自动私有地址）。"""
    for item in wired_adapters():
        ip = item['ip']
        if item['link'] and ip and not ip.startswith('169.254.'):
            return True
    return False


def local_ipv4_addresses():
    """本机所有网卡（含有线 / 无线 / 拨号）的 IPv4，用来认出本机自己留下的旧会话。"""
    script = ("Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | "
              "Where-Object { $_.IPAddress -notlike '127.*' -and "
              "$_.IPAddress -notlike '169.254.*' } | "
              "Select-Object -ExpandProperty IPAddress | ConvertTo-Json -Compress")
    data = _powershell_json(script)
    if isinstance(data, str):
        data = [data]
    return [str(item).strip() for item in (data or []) if str(item).strip()]


def _read_text_file(path):
    """读取可能不是 UTF-8 的文本文件（拨号本可能是 ANSI 或 UTF-8）。"""
    try:
        with open(path, 'rb') as handle:
            raw = handle.read()
    except Exception as error:
        debug('读取失败 %s: %s' % (path, error))
        return ''
    for encoding in ('utf-8-sig', 'mbcs'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', 'replace')


def pppoe_entries():
    """列出系统里所有宽带(PPPoE, Type=5)拨号条目：[(名称, 端口), ...]。

    先扫"所有用户"的拨号本（Windows 自己创建的连接在这里，格式最标准），
    再扫当前用户的拨号本（脚本自己创建的在这里）。
    """
    found = []
    for path in (PPPOE_PBK_ALL, PPPOE_PBK_USER):
        if not os.path.exists(path):
            continue
        name = None
        is_pppoe = False
        port = ''
        for line in _read_text_file(path).splitlines():
            line = line.strip()
            if line.startswith('[') and line.endswith(']'):
                if name and is_pppoe:
                    found.append((name, port))
                name = line[1:-1]
                is_pppoe = False
                port = ''
            elif name:
                if line.lower().startswith('type='):
                    is_pppoe = line.split('=', 1)[1].strip() == '5'
                elif line.lower().startswith('port='):
                    port = line.split('=', 1)[1].strip()
        if name and is_pppoe:
            found.append((name, port))
    return found


def pppoe_entry_exists(name=None):
    """拨号本里是否已经有这个名字的条目。"""
    name = name or PPPOE_NAME
    if not name:
        return False
    return any(entry_name == name for entry_name, _ in pppoe_entries())


def _pbk_writable(path):
    """该拨号本文件是否可写（"所有用户"拨号本一般需要管理员权限）。"""
    try:
        if os.path.exists(path):
            with open(path, 'ab'):
                return True
        folder = os.path.dirname(path)
        if os.path.isdir(folder):
            probe = os.path.join(folder, '.autoconn_probe')
            with open(probe, 'wb') as handle:
                handle.write(b'')
            os.remove(probe)
            return True
    except Exception:
        return False
    return False


def ensure_pppoe_entry(force=False):
    """确保系统里有 PPPOE_NAME 这个宽带(PPPoE)条目；返回 (是否可用, 说明)。

    优先写"所有用户"的拨号本（Windows 自己创建的连接都在这里，rasdial 一定认；
    但通常需要管理员权限），写不进去就退回当前用户的拨号本。
    """
    if not PPPOE_NAME:
        return False, '未配置 PPPOE_NAME'

    if pppoe_entry_exists() and not force:
        return True, '拨号条目已存在'

    # 端口名从系统已有条目里抄（例如 PPPoE5-0），抄不到就交给系统自己挑
    port = ''
    for _, existing_port in pppoe_entries():
        if existing_port:
            port = existing_port
            break
    entry = _PPPOE_TEMPLATE.format(
        name=PPPOE_NAME,
        uid=int(time.time() * 1000) % 100000000,
        guid=str(uuid.uuid4()).upper(),
        port_line=('Port=%s' % port) if port else 'Port=')

    target = PPPOE_PBK_ALL if _pbk_writable(PPPOE_PBK_ALL) else PPPOE_PBK_USER
    folder = os.path.dirname(target)
    try:
        if not os.path.isdir(folder):
            os.makedirs(folder)
        existing = _read_text_file(target)
        if existing and not existing.endswith('\n'):
            existing += '\r\n'
        # 必须用单字节编码写：UTF-16 的拨号本 Windows 认不出来（错误 623）
        with open(target, 'wb') as handle:
            handle.write((existing + entry).encode('mbcs'))
    except Exception as error:
        return False, '写入拨号本失败: %s' % error

    # 刚写进去的文件 RAS 服务不一定立刻读到，等一下再拨（拨号本身才是真正的检验）
    time.sleep(2)
    return True, '已创建拨号条目 %s（%s）' % (PPPOE_NAME, target)


def active_dial_connections():
    """rasdial 列表里当前已连接的拨号连接名。

    注意：中文界面下输出形如
        已连接
        宽带连接
        命令已完成。
    有没有横线分隔行并不固定，所以这里只做关键字过滤，不依赖界面文字。
    """
    code, text = _run_command(['rasdial'], timeout=20)
    names = []
    for line in text.splitlines():
        line = line.strip()
        if not line or set(line) <= set('-'):          # 空行 / 横线
            continue
        if '命令' in line or 'Command' in line:        # "命令已完成。"
            continue
        if line in ('已连接', 'Connected'):            # 表头
            continue
        if line.startswith('没有连接') or line.lower().startswith('no connections'):
            continue
        names.append(line)
    return names


def pppoe_connected(name=None):
    """拨号是否已连接：给了名字就判断该连接，否则任一条拨号连接都算。"""
    actives = active_dial_connections()
    if name:
        return name in actives
    return bool(actives)


def pppoe_dial(user=None, password=None, wait=None, name=None):
    """拨号。优先复用系统里已有的 PPPoE 条目，没有才创建自己的。返回 (是否成功, 说明)。"""
    user = user or PPPOE_USER or (USERNAME + DOMAIN)
    password = password or PPPOE_PASSWORD or PASSWORD
    timeout = wait or PPPOE_DIAL_TIMEOUT

    if pppoe_connected(name):
        return True, '%s 已经是连接状态' % (name or PPPOE_NAME)

    # 优先用系统里已有的条目（Windows 建的"宽带连接"最标准）；一个都没有才自己建
    if name:
        if name == PPPOE_NAME and not pppoe_entry_exists(name):
            ensure_pppoe_entry()
        candidates = [name]
    else:
        candidates = [entry_name for entry_name, _ in pppoe_entries()]
        if not candidates and PPPOE_NAME:
            ok, message = ensure_pppoe_entry()
            if not ok:
                return False, '系统里没有拨号连接，自动创建也失败：%s' % message
            candidates = [PPPOE_NAME]
    if not candidates:
        return False, '系统里没有任何拨号连接，且未配置 PPPOE_NAME'

    last = ''
    for candidate in candidates:
        log('开始拨号 %s（账号 %s）…' % (candidate, user))
        code, text = _run_command(['rasdial', candidate, user, password], timeout=timeout)
        if code == 0:
            return True, '拨号成功（%s）' % candidate
        if code == 999:
            _run_command(['rasdial', candidate, '/disconnect'], timeout=20)
            last = '拨号超时（%s 秒）' % timeout
        else:
            detail = ''
            for item in text.splitlines():
                item = item.strip()
                if '错误' in item and any(ch.isdigit() for ch in item):
                    detail = item
                    break
            last = '连接 %s 失败（错误码 %s）%s' % (candidate, code, detail)
            if code == 623 and candidate == PPPOE_NAME:
                last += ('；系统没把脚本自建的拨号条目交给 RAS。可以手动建一条：'
                         '设置 → 网络和 Internet → 拨号 → 设置新连接 → 连接到 Internet → '
                         '创建新连接 → 宽带(PPPoE)，填上账号密码，脚本以后会自动使用它。')
        log('  %s' % last)
        time.sleep(2)

    return False, last or '没有可用的拨号连接'


def pppoe_hangup(name=None):
    """断开拨号连接（不指定就断开所有已连接的拨号）。"""
    targets = [name] if name else active_dial_connections()
    if not targets:
        return False, '当前没有已连接的拨号'
    messages = []
    for target in targets:
        code, text = _run_command(['rasdial', target, '/disconnect'], timeout=30)
        messages.append('%s:%s' % (target, '已断开' if code == 0 else '失败(%s)' % code))
    return all('失败' not in m for m in messages), '；'.join(messages)


# ============================== 主流程 ==============================


def wait_for_network(max_wait=90):
    """开机后网卡/网关可能还没就绪，等一下再动手。"""
    waited = 0
    while waited < max_wait:
        if STOP_REQUESTED:
            return False
        if portal_reachable(timeout=5):
            return True
        if waited == 0:
            log('Portal 暂时不可达（可能网卡还没就绪），稍候重试…')
        time.sleep(5)
        waited += 5
    return False


def ensure_wifi(wait=None):
    """Portal 不可达时先确保无线连上校园网，再继续认证。"""
    global _WIFI_LAST_FAILURE
    if not WIFI_SSID or not WIFI_AUTO_CONNECT:
        return False
    ok, message = connect_wifi(wait=wait, verbose=True)
    if ok:
        _WIFI_LAST_FAILURE = None
        return True
    if message != _WIFI_LAST_FAILURE:   # 同样的失败原因不重复刷日志
        log('Wi-Fi 连接未成功：%s' % message)
        _WIFI_LAST_FAILURE = message
    return False


def ensure_pppoe(allow_redial=True):
    """插着网线时用网线拨号（PPPoE）把网络接上。返回 (是否成功, 说明)。

    allow_redial=False 时只会在"还没拨号"的情况下拨，绝不去动已经连着的拨号：
    拆掉重拨会把用户正在用的连接断一下（IP 也变了，代理 / 网页上的连接全断），
    只有确认链路上真出问题了才值得这么干。
    """
    if not PPPOE_ENABLE or not PPPOE_NAME:
        return False, '未启用网线拨号'

    actives = active_dial_connections()
    if actives:
        if not allow_redial:
            return False, '已有拨号连接（%s），这次不动它' % '、'.join(actives)
        log('已有拨号连接（%s）但网络不通，重拨一次' % '、'.join(actives))
        pppoe_hangup()
        time.sleep(2)
    elif not ethernet_link_up():
        return False, '没有插网线（有线网卡链路 Down），跳过拨号'
    else:
        log('检测到网线已插入，尝试网线拨号（PPPoE）…')

    ok, message = pppoe_dial()
    log('网线拨号：%s' % message)
    return ok, message


def recover_link(attempt=1, dial=True):
    """断网后重新建立链路：先网线拨号（PPPoE），还不行再连 Wi-Fi。返回是否已能上网。

    dial=False 时只做 Wi-Fi 那一步（拨号已经试过，不用重复拨）。
    """
    if dial and PPPOE_ENABLE:
        # 已经有拨号连着的话，先确认「校园网这一段」是真断了再拆：
        # 外网探测失败的原因很多（DNS 抽风、上游 CDN 不通、校园网往响应里插东西…），
        # 按 IP 直连门户能应答就说明校园网这条路是活的 → 这次不重拨，等下一轮再说。
        allow_redial = True
        if active_dial_connections():
            alive = campus_link_alive()
            if alive:
                allow_redial = False
                log('外网探测不通，但校园网链路还活着（门户按 IP 能应答）→ 这次不重拨拨号，等下一轮')
            elif alive is False:
                log('校园网链路也探测不到（门户按 IP 没应答）→ 准备重拨拨号')
        ok, _ = ensure_pppoe(allow_redial=allow_redial)
        if ok:
            time.sleep(3)
            connected, detail = check_internet()
            if connected:
                log('网线拨号后已能上网（%s），无需 Portal 认证' % detail)
                return True
        elif not allow_redial:
            # 拨号没动：至少把它当"这轮没恢复"，让调用方按正常节奏重试
            return False
    if not portal_reachable(timeout=5):
        ensure_wifi(wait=WIFI_CONNECT_WAIT if attempt == 1 else 15)
        wait_for_network(max_wait=WIFI_CONNECT_WAIT)
    connected, detail = check_internet()
    if connected:
        log('接入网络后已能上网（%s）' % detail)
    return connected


def auto_connect():
    """核心逻辑：能上外网就退出；否则登录，失败就按间隔重试。"""
    session = new_session()
    online_user = who_is_online(session)
    if online_user:
        log('Portal 记录显示 %s 已在线' % online_user)

    kicked = False
    link_recovered = False
    already_online = 0
    pppoe_done = False
    for attempt in range(1, MAX_RETRY + 1):
        if STOP_REQUESTED:
            log('已按请求停止本次连接（界面里点了「停止」）')
            return 1
        connected, detail = check_internet()
        if connected:
            log('网络已连通（%s），第 %d 次检查，无需登录' % (detail, attempt))
            return 0

        # Portal 不可达通常意味着链路层没通：先试网线拨号，再试 Wi-Fi
        # （拨号整个流程只做一次，Wi-Fi 每轮都可以再试）
        if not portal_reachable(timeout=5):
            dial = not pppoe_done and PPPOE_ENABLE
            pppoe_done = pppoe_done or dial
            if recover_link(attempt, dial=dial):
                return 0
        elif attempt == 1:
            wait_for_network()

        ok, message = login()
        if ok:
            log('第 %d 次登录：%s' % (attempt, message))
            time.sleep(2)
            connected, detail = check_internet()
            if connected:
                log('联网验证通过（%s）' % detail)
                return 0
            log('登录接口返回成功，但外网仍不通，继续重试…')
            if '已在线' in message:
                # Portal 认为已在线，但外网探测仍失败：可能是探测地址被校园网劫持，
                # 连续出现两次就停止折腾，避免无意义的循环。
                already_online += 1
                if already_online >= 2:
                    log('Portal 显示本机已在线，但外网探测无法确认；'
                        '若确实上不了网，请用 --login --verbose 查看详细返回')
                    return 0
        else:
            log('第 %d 次登录失败：%s' % (attempt, message))

        # 「已有在线会话」类失败要分三种情况处理：
        #   * 外网已经通了（例如网线拨号 PPPoE 正好恢复了）→ 按成功处理，绝不踢会话；
        #   * 外网不通，但本机的链路是断的（拨号掉线 / 没连 Wi-Fi）→ 先把链路接回来，
        #     断网测试时最常见的就是这一种：Portal 能到（校园网里哪都到得了它），
        #     可网线拨号已经掉线，这时候光重试 Portal 认证永远登不上去；
        #   * 链路没问题还是被拒 → 是账号上残留的旧会话挡着，把它们注销掉再重登。
        conflict = any(key in message for key in
                       ('code=2', '已在线', 'ip_already_online', '旧会话'))
        if conflict:
            connected, detail = check_internet()
            if connected:
                log('Portal 提示已有在线会话，但外网已恢复（%s），按成功处理' % detail)
                return 0
            if not link_recovered:
                link_recovered = True
                dial = not pppoe_done and PPPOE_ENABLE
                pppoe_done = pppoe_done or dial
                if recover_link(attempt, dial=dial):
                    return 0
            if not kicked:
                kicked = True
                # 拨号还连着、而且校园网这一段是活的（门户按 IP 能应答）→ 说明问题不在
                # 「会话残留」上：这时候去注销会话，会把正在用的拨号那条会话也踢下来，
                # 白白把好连接弄断。那就什么都别动，等下一轮再试。
                if active_dial_connections() and campus_link_alive() is True:
                    log('拨号还在、校园网链路也活着 → 先不注销会话（免得把正在用的连接踢断），等下一轮再试')
                else:
                    cleared, kick_message = clear_stale_sessions()
                    log('清理旧会话：%s' % kick_message)
                    if cleared:
                        # 会话刚清掉，不用再等 RETRY_INTERVAL，立刻重登一次
                        time.sleep(3)
                        connected, detail = check_internet()
                        if connected:
                            log('清理旧会话后已能上网（%s）' % detail)
                            return 0
                        continue

        if attempt < MAX_RETRY:
            time.sleep(RETRY_INTERVAL)

    log('连续 %d 次尝试仍然失败，请检查账号密码 / 是否在校园网内' % MAX_RETRY)
    return 1


def main(argv=None):
    global VERBOSE
    parser = argparse.ArgumentParser(
        description='西安电子科技大学校园网自动登录（srun 新版 Portal）')
    parser.add_argument('--check', action='store_true',
                        help='只检查网络与登录状态，不做任何登录动作')
    parser.add_argument('--login', action='store_true',
                        help='强制登录一次（不判断当前是否已经在线）')
    parser.add_argument('--logout', action='store_true',
                        help='注销当前登录（把网断掉，用于验证）')
    parser.add_argument('--wifi', action='store_true',
                        help='只执行"连上 WIFI_SSID 指定的无线网"这一步')
    parser.add_argument('--pppoe', action='store_true',
                        help='只用网线拨号（PPPoE），自动创建拨号条目')
    parser.add_argument('--pppoe-down', action='store_true',
                        help='断开网线拨号连接')
    parser.add_argument('--verbose', action='store_true', help='打印请求细节')
    args = parser.parse_args(argv)

    VERBOSE = args.verbose
    _trim_log()

    if args.check:
        log('当前 Wi-Fi: %s' % (wifi_current_ssid() or '未连接'))
        log('目标 Wi-Fi: %s（%s）' % (WIFI_SSID or '未配置',
                                    '可自动连接' if WIFI_AUTO_CONNECT else '已关闭'))
        adapters = wired_adapters()
        if not adapters:
            log('有线网卡: 未检测到物理有线网卡')
        for item in adapters:
            log('有线网卡 [%s]: 链路=%s IP=%s'
                % (item['name'], '已连接' if item['link'] else '未连接',
                   item['ip'] or '无'))
        log('网线拨号: %s（连接名 %s，当前%s）'
            % ('已启用' if PPPOE_ENABLE else '已关闭', PPPOE_NAME,
               '已连接' if pppoe_connected() else '未连接'))
        entries = pppoe_entries()
        log('系统里的拨号条目: %s'
            % ('、'.join('%s(端口%s)' % (n, p or '默认') for n, p in entries)
               if entries else '无'))
        connected, detail = check_internet()
        log('外网连通: %s（%s）' % (connected, detail))
        online_user = who_is_online(new_session())
        log('Portal 在线账号: %s' % (online_user or '未查询到（未登录）'))
        return 0 if connected else 1

    if args.pppoe_down:
        ok, message = pppoe_hangup()
        log('断开拨号: %s（%s）' % (ok, message))
        return 0 if ok else 1

    if args.pppoe:
        ok, message = pppoe_dial()
        log('网线拨号结果: %s（%s）' % (ok, message))
        if ok:
            log('外网连通: %s' % (check_internet(),))
        return 0 if ok else 1

    if args.wifi:
        log('当前 Wi-Fi: %s' % (wifi_current_ssid() or '未连接'))
        ok, message = connect_wifi(verbose=True)
        log('Wi-Fi 连接结果: %s（%s）' % (ok, message))
        return 0 if ok else 1

    if args.logout:
        ok, message = logout()
        log(message)
        return 0 if ok else 1

    if args.login:
        ok, message = login()
        log(message)
        return 0 if ok else 1

    return auto_connect()


if __name__ == '__main__':
    sys.exit(main())
