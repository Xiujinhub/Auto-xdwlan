# -*- coding: utf-8 -*-
"""v2.15「照人工操作」连接流程的顺序单测：全部 stub，不碰真实网络。"""
import sys

sys.path.insert(0, r'e:\code\Auto-xdwlan-main')
import autoconn

calls = []
logs = []


class FakeTime:
    """把 sleep 变成空操作（RETRY_INTERVAL=0 时循环本来就跳过）。"""

    @staticmethod
    def sleep(seconds):
        pass

    @staticmethod
    def time():
        return 10000.0


def run(name, cable=True, dial_ok=True, wifi_ok=True, login_ok=True,
        online_after=(), already=False, pppoe_enable=True):
    calls.clear()
    logs.clear()
    state = {'connected': already}

    autoconn.time = FakeTime()
    autoconn.USERNAME = '25201111510'
    autoconn.PASSWORD = 'stub'
    autoconn.DOMAIN = ''
    autoconn.MAX_RETRY = 2
    autoconn.RETRY_INTERVAL = 0
    autoconn.DIAL_SETTLE = 0
    autoconn.PPPOE_ENABLE = pppoe_enable
    autoconn.PPPOE_NAME = '宽带连接'
    autoconn.WIFI_SSID = 'stu-xdwlan'
    autoconn.WIFI_AUTO_CONNECT = True
    autoconn.STOP_REQUESTED = False

    autoconn.log = lambda msg, also_print=True: logs.append(str(msg))
    autoconn.debug = lambda msg: None
    autoconn.new_session = lambda: None
    autoconn.link_report = lambda: 'stub 链路'
    autoconn.who_is_online = lambda session: ''
    autoconn.ethernet_link_up = lambda: cable
    autoconn.ethernet_has_ipv4 = lambda: cable
    autoconn.active_dial_connections = lambda: []
    autoconn.wifi_current_ssid = lambda: ''
    autoconn.wait_for_network = lambda max_wait=90: True
    autoconn.portal_reachable = lambda timeout=None: True
    autoconn._report_session_conflict = lambda: calls.append('report')

    def check_internet():
        calls.append('check_internet')
        return (state['connected'], 'stub 204')

    def pppoe_dial(*a, **k):
        calls.append('dial')
        if not dial_ok:
            return False, 'stub 错误 619：端口未连接'
        if 'dial' in online_after:
            state['connected'] = True
        return True, '拨号成功'

    def connect_wifi(*a, **k):
        calls.append('wifi')
        if not wifi_ok:
            return False, 'stub 连不上'
        if 'wifi' in online_after:
            state['connected'] = True
        return True, '已连接'

    def login(*a, **k):
        calls.append('login')
        if login_ok:
            if 'login' in online_after:
                state['connected'] = True
            return True, '认证成功'
        return False, '认证失败: INFO Error，err_code=2：账号已经有一条在线会话'

    autoconn.check_internet = check_internet
    autoconn.pppoe_dial = pppoe_dial
    autoconn.pppoe_hangup = lambda *a, **k: (True, 'stub')
    autoconn.connect_wifi = connect_wifi
    autoconn.login = login

    code = autoconn.auto_connect()
    print('== %s ==  动作顺序: %s   返回: %s' % (name, ' -> '.join(calls), code))
    for line in logs:
        if any(key in line for key in ('拨号', 'Wi-Fi', 'Portal', '已连通', '轮', '占着')):
            print('     %s' % line)
    return code, list(calls)


failures = []


def expect(name, want_code, want_calls, **kwargs):
    code, order = run(name, **kwargs)
    ok = code == want_code and order == want_calls
    print('   -> %s' % ('通过' if ok else '不通过！期望 %s / %s' % (want_code, want_calls)))
    if not ok:
        failures.append(name)
    print()


expect('A 插着网线，拨号成功就能上网（不该去动 Wi-Fi / Portal）',
       0, ['check_internet', 'dial', 'check_internet'],
       cable=True, online_after=('dial',))
expect('B 没插网线，连 Wi-Fi 就能上网（不该拨号）',
       0, ['check_internet', 'wifi', 'check_internet'],
       cable=False, online_after=('wifi',))
expect('C 插网线但拨号失败 → 连 Wi-Fi → Portal 认证成功',
       0, ['check_internet', 'dial', 'wifi', 'check_internet', 'login', 'check_internet'],
       cable=True, dial_ok=False, online_after=('login',))
expect('D 账号被别的设备占着（err_code=2）：记录原因后继续重试，不做任何踢会话动作',
       1, ['check_internet', 'dial', 'wifi', 'check_internet', 'login', 'report'] * 2,
       cable=True, dial_ok=False, login_ok=False)
expect('E 已经能上网：什么都不做',
       0, ['check_internet'], already=True)
expect('F 界面里关掉「允许网线拨号」→ 不拨号，走 Wi-Fi / Portal',
       0, ['check_internet', 'wifi', 'check_internet'],
       cable=True, pppoe_enable=False, online_after=('wifi',))

print('结果：%s' % ('全部通过' if not failures else '失败 %s' % failures))
sys.exit(1 if failures else 0)
