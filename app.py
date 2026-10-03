# -*- coding: utf-8 -*-
"""
Auto-xdwlan —— 西安电子科技大学校园网自动连接（图形界面版）

界面功能：
    * 账号 / 密码 / 运营商 配置（保存在 exe 同目录的 settings.json）
    * 连接状态实时显示（网线拨号 / Wi-Fi / Portal 在线账号 / 外网连通）
    * 一键「立即连接」「断开」「刷新状态」
    * 设置：开机自启动、后台运行（关闭窗口最小化到托盘）、启动后自动连接、断线自动重连
    * 托盘图标菜单（显示主界面 / 立即连接 / 刷新状态 / 退出）

用法：
    python app.py            打开界面
    python app.py --tray     直接后台（托盘）启动，不弹窗口（开机自启用这个）
    python app.py --connect  后台启动并立刻连接一次

打包成单文件 exe：见 build_exe.ps1（PyInstaller）

底层网络逻辑全部复用 autoconn.py（Portal 认证 / Wi-Fi 连接 / PPPoE 拨号）。
"""

import base64
import json
import os
import sys
import time
import traceback

from PyQt5 import QtCore, QtGui, QtNetwork, QtWidgets

import autoconn

APP_NAME = 'Auto-xdwlan'
APP_TITLE = '西电校园网自动连接'
APP_VERSION = '2.18'
SETTINGS_FILE = 'settings.json'
LOCAL_SERVER = 'Auto-xdwlan-gui'

# 蓝色科技风配色
C_BG = '#0A1B30'
C_CARD = '#0F2540'
C_CARD_BORDER = '#1A4166'
C_ACCENT = '#2E8BFF'
C_ACCENT2 = '#38D6FF'
C_TEXT = '#E4F1FF'
C_TEXT_DIM = '#8FB4D4'
C_OK = '#22D3A6'
C_WARN = '#FFB020'
C_ERR = '#FF5C7A'
C_IDLE = '#5B7B99'


# ============================== 路径 / 资源 ==============================


def app_dir():
    """程序所在目录（打包后是 exe 所在目录，开发时是脚本目录）。"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def resource_path(name):
    """打包后资源文件被解包到临时目录，这里统一取路径。"""
    base = getattr(sys, '_MEIPASS', app_dir())
    return os.path.join(base, name)


SETTINGS_PATH = os.path.join(app_dir(), SETTINGS_FILE)
ICON_PATH = resource_path(os.path.join('logo', 'Auto-xdwlan.ico'))
LOGO_PATH = resource_path(os.path.join('logo', 'logo_192.png'))


# ============================== 日志 ==============================


class LogBus(QtCore.QObject):
    """把任意线程里的日志转发到界面线程。"""

    message = QtCore.pyqtSignal(str)


LOG_BUS = LogBus()


def log_line(message, also_print=True):
    """替换 autoconn 的日志函数：只推送到界面，不写任何日志文件。"""
    stamp = time.strftime('%Y-%m-%d %H:%M:%S')
    LOG_BUS.message.emit('[%s] %s' % (stamp[11:], message))


# ============================== 崩溃保护 ==============================
#
# 为什么需要这段？PyQt5（5.5 起）对「槽函数 / 定时器回调里未捕获的 Python 异常」
# 的默认处理是 qFatal() —— 直接 abort() 掉整个进程（Windows 上表现为
# 0xC0000409 / ucrtbase.dll 的静默崩溃，窗口凭空消失，没有任何提示）。
# 装上 sys.excepthook 后，异常会被我们自己接住并写进 crash.log，程序继续运行。

CRASH_LOG = os.path.join(app_dir(), 'crash.log')


class CrashBus(QtCore.QObject):
    """把任意线程里的异常信息转发到界面线程。"""

    message = QtCore.pyqtSignal(str)


CRASH_BUS = CrashBus()


def write_crash_log(text):
    """追加写 crash.log（超过 256 KB 就滚动成 crash.log.old）。"""
    try:
        if os.path.exists(CRASH_LOG) and os.path.getsize(CRASH_LOG) > 256 * 1024:
            try:
                os.remove(CRASH_LOG + '.old')
            except OSError:
                pass
            os.replace(CRASH_LOG, CRASH_LOG + '.old')
        with open(CRASH_LOG, 'a', encoding='utf-8') as handle:
            handle.write(text)
    except Exception:
        pass


def _excepthook(exc_type, exc_value, exc_tb):
    """兜底异常处理：记录 + 提示，绝不 re-raise（否则 PyQt 会 abort 进程）。"""
    header = '[%s] 未捕获异常（已忽略，程序继续运行）\n' % time.strftime('%Y-%m-%d %H:%M:%S')
    try:
        detail = ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))
    except Exception:
        detail = '%s: %s\n' % (exc_type, exc_value)
    write_crash_log(header + detail + '\n')
    try:
        CRASH_BUS.message.emit(header + detail)
    except Exception:
        pass


def install_excepthook():
    """接管未捕获异常，避免 PyQt5 qFatal() 把程序直接干掉。"""
    sys.excepthook = _excepthook


def retire_worker(worker):
    """安全回收工作线程：等线程真正结束后再 deleteLater。

    直接在线程自己的信号槽里 deleteLater，有可能在线程还没结束时就
    析构 QThread，Qt 会 qFatal('QThread: Destroyed while thread is still
    running') 直接 abort 掉进程，所以统一走这里。
    """
    if worker is None:
        return
    try:
        worker.finished.connect(worker.deleteLater)
        if worker.isFinished():
            worker.deleteLater()
    except Exception:
        pass


# ============================== 配置读写 ==============================

SETTINGS_DEFAULTS = {
    'username': autoconn.USERNAME,
    'password': autoconn.PASSWORD,
    'domain': '',
    'remember_password': True,
    'wifi_ssid': autoconn.WIFI_SSID,
    'wifi_enable': True,
    'pppoe_enable': True,
    'pppoe_name': autoconn.PPPOE_NAME,
    'autostart': False,
    'background': True,
    'connect_on_start': True,
    'auto_reconnect': True,
    'reconnect_minutes': 10,
}

_OBF_KEY = b'Auto-xdwlan-2024'


def _obfuscate(text):
    """简单异或 + base64（只是避免明文，不是加密）。"""
    raw = text.encode('utf-8')
    mixed = bytes(byte ^ _OBF_KEY[index % len(_OBF_KEY)] for index, byte in enumerate(raw))
    return base64.b64encode(mixed).decode('ascii')


def _deobfuscate(text):
    try:
        mixed = base64.b64decode(text.encode('ascii'))
    except Exception:
        return ''
    raw = bytes(byte ^ _OBF_KEY[index % len(_OBF_KEY)] for index, byte in enumerate(mixed))
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return ''


def load_settings():
    data = dict(SETTINGS_DEFAULTS)
    try:
        with open(SETTINGS_PATH, 'r', encoding='utf-8') as handle:
            stored = json.load(handle)
        if isinstance(stored, dict):
            for key, value in stored.items():
                if key in ('password', 'password_enc'):
                    continue
                if key in data:
                    data[key] = value
            if stored.get('password_enc'):
                data['password'] = _deobfuscate(stored['password_enc'])
            elif stored.get('password'):
                data['password'] = stored['password']
    except Exception:
        pass
    return data


def save_settings(data):
    payload = dict(data)
    payload.pop('password', None)
    payload.pop('password_enc', None)
    if data.get('remember_password'):
        payload['password_enc'] = _obfuscate(data.get('password', ''))
    try:
        with open(SETTINGS_PATH, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        return True, '配置已保存'
    except Exception as error:
        return False, '保存配置失败: %s' % error


def apply_settings(data):
    """把界面配置同步到底层 autoconn 模块的全局变量。"""
    autoconn.USERNAME = (data.get('username') or '').strip()
    autoconn.PASSWORD = data.get('password') or ''
    autoconn.DOMAIN = data.get('domain') or ''
    autoconn.WIFI_SSID = (data.get('wifi_ssid') or '').strip()
    autoconn.WIFI_AUTO_CONNECT = bool(data.get('wifi_enable'))
    autoconn.PPPOE_ENABLE = bool(data.get('pppoe_enable'))
    autoconn.PPPOE_NAME = (data.get('pppoe_name') or '').strip() or 'XidianPPoE'
    autoconn.PPPOE_USER = ''
    autoconn.PPPOE_PASSWORD = ''
    autoconn.MAX_RETRY = 8         # 界面里不要长时间卡着（配合「停止」按钮）
    autoconn.RETRY_INTERVAL = 10   # 每轮之间的间隔：拨号 / 连 Wi-Fi / Portal 都试过之后歇多久
    autoconn.DIAL_SETTLE = 15      # 拨号成功后留给它验证外网的时间（期间不做 Portal 认证）
    autoconn.REQUEST_TIMEOUT = 8
    autoconn.LOG_FILE = None  # 不写日志文件


# ============================== 开机自启（注册表） ==============================

RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
RUN_VALUE = 'Auto-xdwlan'


def autostart_command():
    """开机自启命令行：exe（或 python 脚本）+ --tray。"""
    if getattr(sys, 'frozen', False):
        return '"%s" --tray' % os.path.abspath(sys.executable)
    script = os.path.join(app_dir(), 'app.py')
    return '"%s" "%s" --tray' % (os.path.abspath(sys.executable), script)


def autostart_state():
    """返回当前注册表里的自启命令，没有则返回 None。"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, RUN_VALUE)
            return value
    except Exception:
        return None


def set_autostart(enabled):
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, autostart_command())
                return True, '已设置开机自启动'
            try:
                winreg.DeleteValue(key, RUN_VALUE)
            except FileNotFoundError:
                pass
            return True, '已取消开机自启动'
    except Exception as error:
        return False, '设置开机自启失败: %s' % error


# ============================== 网络任务（后台线程里跑） ==============================


def job_connect():
    """完整流程：能上网就直接返回，否则拨号 / Wi-Fi / Portal 认证。"""
    log_line('开始连接校园网…')
    code = autoconn.auto_connect()
    return code == 0, {'code': code}


def job_check():
    """检查当前状态：Wi-Fi、拨号、网线、外网、Portal 在线账号。"""
    info = {}
    try:
        info['wifi'] = autoconn.wifi_current_ssid() or ''
    except Exception:
        info['wifi'] = ''
    try:
        info['dial'] = autoconn.active_dial_connections()
    except Exception:
        info['dial'] = []
    info['link'] = False
    info['ip'] = False
    # 网线和无线可能同时在用（Windows 只会走其中一条），所以两个状态都要显示。
    # wired_state() 一次查完有线网卡，只起一次 PowerShell（比较慢的那一步）。
    try:
        info['link'], info['ip'], _ = autoconn.wired_state()
    except Exception:
        pass
    try:
        info['egress'] = autoconn.campus_source_ip()
    except Exception:
        info['egress'] = ''
    info['target'] = autoconn.WIFI_SSID

    connected, detail = autoconn.check_internet()
    info['connected'] = connected
    info['detail'] = detail

    online_user = ''
    try:
        if connected or autoconn.portal_reachable(timeout=5):
            online_user = autoconn.who_is_online(autoconn.new_session()) or ''
    except Exception:
        online_user = ''
    info['user'] = online_user
    return connected, info


def job_disconnect():
    """下线：注销 Portal 登录，断开网线拨号。"""
    messages = []
    ok, message = autoconn.logout()
    messages.append('注销: %s' % message)
    try:
        if autoconn.active_dial_connections():
            _, dial_message = autoconn.pppoe_hangup()
            messages.append('断开拨号: %s' % dial_message)
    except Exception:
        pass
    return ok, {'message': '；'.join(messages)}


JOBS = {
    'connect': job_connect,
    'check': job_check,
    'disconnect': job_disconnect,
}

JOB_TITLES = {
    'connect': '连接',
    'check': '状态检测',
    'disconnect': '断开',
}


class Worker(QtCore.QThread):
    """在后台线程执行网络任务，避免界面卡死。"""

    finished_job = QtCore.pyqtSignal(str, bool, dict)

    def __init__(self, job_name, parent=None):
        super(Worker, self).__init__(parent)
        self.job_name = job_name

    def run(self):
        ok, info = False, {}
        try:
            ok, info = JOBS[self.job_name]()
        except Exception as error:
            info = {'error': str(error)}
            log_line('%s 失败: %s' % (JOB_TITLES.get(self.job_name, self.job_name), error))
        if isinstance(info, dict):
            info['job'] = self.job_name
        self.finished_job.emit(self.job_name, ok, info)


# ============================== 单实例（第二次启动时唤出已有窗口） ==============================


class InstanceGuard(QtCore.QObject):
    activated = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super(InstanceGuard, self).__init__(parent)
        self.server = QtNetwork.QLocalServer(self)
        self.server.newConnection.connect(self._on_connection)

    def listen(self):
        if self.server.listen(LOCAL_SERVER):
            return True
        QtNetwork.QLocalServer.removeServer(LOCAL_SERVER)
        return self.server.listen(LOCAL_SERVER)

    def notify_existing(self):
        socket = QtNetwork.QLocalSocket()
        socket.connectToServer(LOCAL_SERVER)
        if not socket.waitForConnected(400):
            return False
        socket.write(b'SHOW')
        socket.flush()
        socket.waitForBytesWritten(400)
        socket.disconnectFromServer()
        return True

    def _on_connection(self):
        socket = self.server.nextPendingConnection()
        if socket is None:
            return
        socket.readyRead.connect(socket.deleteLater)
        socket.disconnected.connect(socket.deleteLater)
        socket.waitForReadyRead(300)
        socket.readAll()
        self.activated.emit()


# ============================== 界面样式（蓝色科技风） ==============================

STYLE = """
QLabel { color: #C9E2F7; font-size: 12.5px; }
QFrame#Root {
    background-color: #0A1B30;
    border: 1px solid #1B4670;
    border-radius: 16px;
}
QLabel#AppTitle { color: #EAF4FF; font-size: 17px; font-weight: 600; }
QLabel#AppSubtitle { color: #7FA6C9; font-size: 11px; }
QToolButton#WinBtn {
    color: #7FA6C9; background: transparent; border: none;
    border-radius: 6px; font-size: 14px; padding: 2px 8px;
}
QToolButton#WinBtn:hover { background-color: #16385A; color: #E4F1FF; }
QToolButton#WinBtnClose:hover { background-color: #C2334B; color: #FFFFFF; }
QFrame#Card {
    background-color: #0F2540;
    border: 1px solid #1A4166;
    border-radius: 12px;
}
QLabel#CardTitle { color: #62C6FF; font-size: 12px; font-weight: 600; }
QLabel#FieldKey { color: #7FA6C9; font-size: 12px; }
QLabel#FieldValue { color: #D9ECFF; font-size: 12.5px; }
QLabel#Hint { color: #6E93B4; font-size: 11.5px; }
QLabel#StatusText { color: #EAF4FF; font-size: 18px; font-weight: 600; }
QLabel#StatusSub { color: #7FA6C9; font-size: 11.5px; }
QLineEdit {
    background-color: #0A1D33;
    border: 1px solid #1D4A75;
    border-radius: 8px;
    padding: 7px 10px;
    color: #E4F1FF;
    font-size: 13px;
    selection-background-color: #2E8BFF;
}
QLineEdit:focus { border: 1px solid #38A9FF; background-color: #0C2239; }
QComboBox {
    background-color: #0A1D33;
    border: 1px solid #1D4A75;
    border-radius: 8px;
    padding: 6px 10px;
    color: #E4F1FF;
    font-size: 13px;
}
QComboBox:hover { border: 1px solid #38A9FF; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView {
    background-color: #0F2540; border: 1px solid #1D4A75; color: #E4F1FF;
    selection-background-color: #1B4E7E; outline: none;
}
QPushButton#Primary {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #1E6BFF, stop:1 #35CFFF);
    color: #FFFFFF; border: none; border-radius: 9px;
    padding: 9px 16px; font-size: 13px; font-weight: 600;
}
QPushButton#Primary:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #3B82FF, stop:1 #5ADCFF);
}
QPushButton#Primary:disabled { background: #1B3A5C; color: #7B98B4; }
QPushButton#Ghost {
    background: transparent; border: 1px solid #24557F; color: #9CC6E8;
    border-radius: 9px; padding: 8px 14px; font-size: 12.5px;
}
QPushButton#Ghost:hover { border-color: #38A9FF; color: #D7EBFF; background-color: #12304F; }
QPushButton#Ghost:disabled { color: #5B7B99; border-color: #1B3A5C; }
QPushButton#Tiny {
    background: transparent; border: 1px solid #24557F; color: #9CC6E8;
    border-radius: 7px; padding: 3px 8px; font-size: 11.5px;
}
QPushButton#Tiny:hover { border-color: #38A9FF; color: #D7EBFF; }
QToolButton#Link {
    color: #62C6FF; background: transparent; border: none;
    font-size: 12px; padding: 2px 6px;
}
QToolButton#Link:hover { color: #9ADBFF; }
QCheckBox { color: #BFDCF3; font-size: 12.5px; spacing: 9px; }
QCheckBox::indicator {
    width: 15px; height: 15px; border-radius: 5px;
    border: 1px solid #2A5C86; background-color: #0A1D33;
}
QCheckBox::indicator:hover { border: 1px solid #38A9FF; }
QCheckBox::indicator:checked {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 #1E6BFF, stop:1 #35CFFF);
    border: 1px solid #4FC3FF;
}
QSpinBox {
    background-color: #0A1D33; border: 1px solid #1D4A75; border-radius: 7px;
    color: #E4F1FF; padding: 3px 6px; font-size: 12.5px;
}
QSpinBox::up-button, QSpinBox::down-button { width: 14px; background: transparent; border: none; }
QPlainTextEdit#Log {
    background-color: #08192B; border: 1px solid #143A5C; border-radius: 8px;
    color: #86A8C4; font-family: Consolas, "Courier New";
    font-size: 11.5px; padding: 6px;
}
QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }
QScrollBar::handle:vertical { background: #1E4E7A; border-radius: 4px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: #2C6EA8; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0px; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QMenu {
    background-color: #0F2540; border: 1px solid #1D4A75; color: #D9ECFF;
    padding: 4px;
}
QMenu::item { padding: 6px 22px 6px 14px; font-size: 12.5px; }
QMenu::item:selected { background-color: #1B4E7E; }
QMenu::separator { height: 1px; background: #1D4A75; margin: 4px 8px; }
QToolTip {
    background-color: #0F2540; color: #D9ECFF;
    border: 1px solid #1D4A75; padding: 4px;
}
"""


class Card(QtWidgets.QFrame):
    """带标题的圆角卡片。"""

    def __init__(self, title, parent=None):
        super(Card, self).__init__(parent)
        self.setObjectName('Card')
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(13, 10, 13, 11)
        outer.setSpacing(7)
        if title:
            label = QtWidgets.QLabel(title)
            label.setObjectName('CardTitle')
            outer.addWidget(label)
        self.body = QtWidgets.QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(7)
        outer.addLayout(self.body)


class HeaderBar(QtWidgets.QWidget):
    """无边框窗口的标题栏，按住可拖动窗口。"""

    def __init__(self, parent=None):
        super(HeaderBar, self).__init__(parent)
        self.setObjectName('Header')
        self._offset = None

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self._offset = event.globalPos() - self.window().frameGeometry().topLeft()
            event.accept()
        else:
            super(HeaderBar, self).mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._offset is not None and event.buttons() & QtCore.Qt.LeftButton:
            self.window().move(event.globalPos() - self._offset)
            event.accept()
        else:
            super(HeaderBar, self).mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._offset = None
        super(HeaderBar, self).mouseReleaseEvent(event)


def field_label(text, object_name='FieldValue'):
    label = QtWidgets.QLabel(text)
    label.setObjectName(object_name)
    return label


DOMAIN_CHOICES = (
    ('校园网', ''),
    ('中国电信 @dx', '@dx'),
    ('中国联通 @lt', '@lt'),
    ('中国移动 @yd', '@yd'),
)


# ============================== 主窗口 ==============================


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, tray_only=False):
        super(MainWindow, self).__init__()
        self.settings = load_settings()
        apply_settings(self.settings)
        self.settings['autostart'] = autostart_state() is not None
        self.worker = None
        self.force_quit = False
        self.tray_only = tray_only
        self.status_info = {}
        self.busy = False
        self.pending_job = None
        self._loading = False

        self.setWindowTitle('%s · %s' % (APP_NAME, APP_TITLE))
        self.setWindowFlags(QtCore.Qt.FramelessWindowHint | QtCore.Qt.Window)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setMinimumSize(470, 500)
        available = QtWidgets.QApplication.primaryScreen().availableGeometry()
        height = min(700, max(500, available.height() - 60))
        width = min(500, max(470, available.width() - 60))
        self.resize(width, height)
        self.move(available.center() - self.rect().center())
        if os.path.exists(ICON_PATH):
            self.setWindowIcon(QtGui.QIcon(ICON_PATH))

        self._build_ui()
        self._build_tray()

        LOG_BUS.message.connect(self.append_log)

        self.status_timer = QtCore.QTimer(self)
        self.status_timer.setInterval(30 * 1000)   # v2.16：60 → 30 秒，断网更快被发现
        self.status_timer.timeout.connect(self._auto_refresh)
        self.status_timer.start()

        self.reconnect_timer = QtCore.QTimer(self)
        self.reconnect_timer.timeout.connect(self._auto_reconnect)
        self._update_reconnect_timer()

        # v2.16：断网后马上安排一次自动重连（不再傻等那个「每 N 分钟」的定时器）
        self.next_auto_connect_at = 0.0    # 下一次允许自动重连的最早时刻
        self.connect_job_auto = False      # 当前这次「连接」是不是自动发起的

        QtCore.QTimer.singleShot(200, lambda: self.start_job('check'))
        if self.settings.get('connect_on_start'):
            QtCore.QTimer.singleShot(600, lambda: self.start_job('connect'))

    # ---------- 界面搭建 ----------

    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        outer = QtWidgets.QVBoxLayout(central)
        outer.setContentsMargins(13, 13, 13, 13)
        outer.setSpacing(0)

        self.panel = QtWidgets.QFrame()
        self.panel.setObjectName('Root')
        outer.addWidget(self.panel)
        shadow = QtWidgets.QGraphicsDropShadowEffect(self.panel)
        shadow.setBlurRadius(34)
        shadow.setOffset(0, 6)
        shadow.setColor(QtGui.QColor(0, 0, 0, 170))
        self.panel.setGraphicsEffect(shadow)

        box = QtWidgets.QVBoxLayout(self.panel)
        box.setContentsMargins(15, 12, 15, 10)
        box.setSpacing(10)
        box.addWidget(self._build_header())

        scroller = QtWidgets.QScrollArea()
        scroller.setObjectName('Scroller')
        scroller.setWidgetResizable(True)
        scroller.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroller.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroller.setStyleSheet('QScrollArea#Scroller, QScrollArea#Scroller > QWidget '
                               '{ background: transparent; border: none; }')
        content = QtWidgets.QWidget()
        content.setObjectName('Content')
        content.setStyleSheet('#Content { background: transparent; }')
        scroller.setWidget(content)
        box.addWidget(scroller, 1)

        inner = QtWidgets.QVBoxLayout(content)
        inner.setContentsMargins(2, 0, 2, 0)
        inner.setSpacing(8)
        inner.addWidget(self._build_status_card())
        inner.addWidget(self._build_account_card())
        inner.addWidget(self._build_settings_card())
        self.inner_layout = inner
        self.log_card = self._build_log_card()
        self.log_card_index = inner.count()
        inner.addWidget(self.log_card, 1)
        box.addWidget(self._build_footer())

    def _build_header(self):
        header = HeaderBar()
        self.header = header
        lay = QtWidgets.QHBoxLayout(header)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(11)

        logo = QtWidgets.QLabel()
        logo.setFixedSize(42, 42)
        pixmap = QtGui.QPixmap(LOGO_PATH)
        if not pixmap.isNull():
            logo.setPixmap(pixmap.scaled(42, 42, QtCore.Qt.KeepAspectRatio,
                                         QtCore.Qt.SmoothTransformation))
        lay.addWidget(logo)

        text_box = QtWidgets.QVBoxLayout()
        text_box.setSpacing(1)
        text_box.addWidget(field_label(APP_NAME, 'AppTitle'))
        text_box.addWidget(field_label(APP_TITLE + ' · v' + APP_VERSION, 'AppSubtitle'))
        lay.addLayout(text_box)
        lay.addStretch(1)

        minimize = QtWidgets.QToolButton()
        minimize.setObjectName('WinBtn')
        minimize.setText('—')
        minimize.setFixedWidth(32)
        minimize.setToolTip('最小化到托盘')
        minimize.setCursor(QtCore.Qt.PointingHandCursor)
        minimize.clicked.connect(self.hide_to_tray)
        lay.addWidget(minimize)

        close = QtWidgets.QToolButton()
        close.setObjectName('WinBtnClose')
        close.setText('✕')
        close.setFixedWidth(32)
        close.setToolTip('关闭（后台运行时最小化到托盘）')
        close.setCursor(QtCore.Qt.PointingHandCursor)
        close.clicked.connect(self.close)
        lay.addWidget(close)
        return header

    def _build_status_card(self):
        card = Card('')          # 大状态就是标题，不再单独占一行
        self.status_card = card

        top = QtWidgets.QHBoxLayout()
        top.setSpacing(10)
        self.dot = QtWidgets.QLabel()
        self.dot.setFixedSize(16, 16)
        self._paint_dot(C_IDLE)
        top.addWidget(self.dot)

        text_box = QtWidgets.QVBoxLayout()
        text_box.setSpacing(1)
        self.status_text = field_label('检测中…', 'StatusText')
        self.status_sub = field_label('正在获取当前网络状态', 'StatusSub')
        # 允许被压缩 + 自动换行，否则长文案（出口 IP 等）会把右侧按钮挤出窗口
        self.status_sub.setWordWrap(True)
        self.status_sub.setSizePolicy(QtWidgets.QSizePolicy.Ignored,
                                      QtWidgets.QSizePolicy.Preferred)
        text_box.addWidget(self.status_text)
        text_box.addWidget(self.status_sub)
        top.addLayout(text_box, 1)

        # 主操作放右上角：最常用的「立即连接」一眼就能点到
        self.connect_btn = QtWidgets.QPushButton('立即连接')
        self.connect_btn.setObjectName('Primary')
        self.connect_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.connect_btn.setFixedWidth(92)
        self.connect_btn.clicked.connect(lambda: self.start_job('connect'))
        top.addWidget(self.connect_btn)

        self.refresh_btn = QtWidgets.QPushButton('刷新')
        self.refresh_btn.setObjectName('Ghost')
        self.refresh_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.refresh_btn.setFixedWidth(52)
        self.refresh_btn.clicked.connect(lambda: self.start_job('check'))
        top.addWidget(self.refresh_btn)
        card.body.addLayout(top)

        # 四项详情压成 2×2，省掉一半高度（完整信息放 tooltip，鼠标停上去就能看）
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(5)
        rows = (('上网方式', 'route', '在线账号', 'user'),
                ('无线网络', 'wifi', '外网连通', 'net'))
        for index, (left_key, left_attr, right_key, right_attr) in enumerate(rows):
            grid.addWidget(field_label(left_key, 'FieldKey'), index, 0)
            left = field_label('—')
            left.setWordWrap(True)          # 无线名等偶尔很长：宁可换行也别被截掉
            setattr(self, 'value_' + left_attr, left)
            grid.addWidget(left, index, 1)
            grid.addWidget(field_label(right_key, 'FieldKey'), index, 2)
            right = field_label('—')
            right.setWordWrap(True)
            setattr(self, 'value_' + right_attr, right)
            grid.addWidget(right, index, 3)
        grid.setColumnMinimumWidth(0, 56)
        grid.setColumnMinimumWidth(2, 56)
        grid.setColumnStretch(1, 3)
        grid.setColumnStretch(3, 2)
        card.body.addLayout(grid)

        buttons = QtWidgets.QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)

        self.disconnect_btn = QtWidgets.QPushButton('断开')
        self.disconnect_btn.setObjectName('Ghost')
        self.disconnect_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.disconnect_btn.setToolTip('注销校园网登录并断开拨号')
        self.disconnect_btn.clicked.connect(lambda: self.start_job('disconnect'))
        buttons.addWidget(self.disconnect_btn)

        self.stop_btn = QtWidgets.QPushButton('停止')
        self.stop_btn.setObjectName('Ghost')
        self.stop_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.stop_btn.setToolTip('中止正在进行的连接 / 检测任务')
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_job)
        buttons.addWidget(self.stop_btn)
        card.body.addLayout(buttons)
        return card

    def _build_account_card(self):
        card = Card('账号')
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)

        grid.addWidget(field_label('账号', 'FieldKey'), 0, 0)
        self.username_edit = QtWidgets.QLineEdit(self.settings.get('username', ''))
        self.username_edit.setPlaceholderText('学号')
        self.username_edit.setToolTip('统一身份认证账号（西电学号，11 位数字）')
        self.username_edit.setMinimumWidth(64)
        grid.addWidget(self.username_edit, 0, 1)

        grid.addWidget(field_label('密码', 'FieldKey'), 0, 2)
        self.password_edit = QtWidgets.QLineEdit(self.settings.get('password', ''))
        self.password_edit.setEchoMode(QtWidgets.QLineEdit.Password)
        self.password_edit.setPlaceholderText('密码')
        self.password_edit.setMinimumWidth(64)
        grid.addWidget(self.password_edit, 0, 3)
        self.show_password_btn = QtWidgets.QPushButton('显示')
        self.show_password_btn.setObjectName('Tiny')
        self.show_password_btn.setCheckable(True)
        self.show_password_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.show_password_btn.setFixedWidth(46)
        self.show_password_btn.toggled.connect(self._toggle_password)
        grid.addWidget(self.show_password_btn, 0, 4)

        grid.addWidget(field_label('运营商', 'FieldKey'), 1, 0)
        self.domain_combo = QtWidgets.QComboBox()
        for text, value in DOMAIN_CHOICES:
            self.domain_combo.addItem(text, value)
        current = self.settings.get('domain', '')
        for index, (_, value) in enumerate(DOMAIN_CHOICES):
            if value == current:
                self.domain_combo.setCurrentIndex(index)
                break
        grid.addWidget(self.domain_combo, 1, 1)

        self.remember_check = QtWidgets.QCheckBox('记住密码')
        self.remember_check.setChecked(bool(self.settings.get('remember_password', True)))
        self.remember_check.setToolTip('密码以「异或 + base64」混淆后存在本机 settings.json'
                                       '（只是不成明文，不是加密，别外发这个文件）')
        grid.addWidget(self.remember_check, 1, 3, 1, 2)

        grid.setColumnMinimumWidth(0, 42)
        grid.setColumnMinimumWidth(2, 42)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        card.body.addLayout(grid)
        return card

    def _toggle_password(self, shown):
        self.password_edit.setEchoMode(
            QtWidgets.QLineEdit.Normal if shown else QtWidgets.QLineEdit.Password)
        self.show_password_btn.setText('隐藏' if shown else '显示')

    def _toggle_log(self, shown):
        """展开 / 收起运行日志（收起后窗口更紧凑，也不再占着剩余空间）。"""
        self.log_view.setVisible(shown)
        self.log_toggle.setText('收起' if shown else '展开')
        try:
            # 收起时让日志卡回到「只有标题行」的高度，别留一大片空白
            self.inner_layout.setStretch(self.log_card_index, 1 if shown else 0)
        except Exception:
            pass

    def _build_settings_card(self):
        card = Card('设置')

        # 三行短标签 + tooltip 说明（原来一行一个勾选项、标签很长，界面被撑得很高）
        row1 = QtWidgets.QHBoxLayout()
        row1.setSpacing(12)
        self.autostart_check = QtWidgets.QCheckBox('开机自启动')
        self.autostart_check.setToolTip('登录 Windows 后自动在后台运行（写当前用户注册表，不需要管理员）')
        self.autostart_check.setChecked(bool(self.settings.get('autostart')))
        self.autostart_check.toggled.connect(self._on_autostart_toggled)
        row1.addWidget(self.autostart_check)
        self.background_check = QtWidgets.QCheckBox('后台运行')
        self.background_check.setToolTip('关闭窗口时最小化到托盘，不断网')
        self.background_check.setChecked(bool(self.settings.get('background', True)))
        row1.addWidget(self.background_check)
        self.autoconnect_check = QtWidgets.QCheckBox('启动后自动连接')
        self.autoconnect_check.setChecked(bool(self.settings.get('connect_on_start', True)))
        row1.addWidget(self.autoconnect_check)
        row1.addStretch(1)
        card.body.addLayout(row1)

        row2 = QtWidgets.QHBoxLayout()
        row2.setSpacing(12)
        self.wifi_check = QtWidgets.QCheckBox('允许连接 Wi-Fi')
        self.wifi_check.setToolTip('允许自动连接 / 切换校园网无线（默认 stu-xdwlan）')
        self.wifi_check.setChecked(bool(self.settings.get('wifi_enable', True)))
        row2.addWidget(self.wifi_check)
        self.pppoe_check = QtWidgets.QCheckBox('允许网线拨号')
        self.pppoe_check.setToolTip('允许用网线做 PPPoE 拨号（自动复用系统里已有的「宽带连接」）')
        self.pppoe_check.setChecked(bool(self.settings.get('pppoe_enable', True)))
        row2.addWidget(self.pppoe_check)
        row2.addStretch(1)
        card.body.addLayout(row2)

        row3 = QtWidgets.QHBoxLayout()
        row3.setSpacing(8)
        self.reconnect_check = QtWidgets.QCheckBox('断线自动重连')
        self.reconnect_check.setChecked(bool(self.settings.get('auto_reconnect', True)))
        row3.addWidget(self.reconnect_check)
        every = field_label('每', 'FieldKey')
        every.setMinimumWidth(16)
        row3.addWidget(every)
        self.interval_spin = QtWidgets.QSpinBox()
        self.interval_spin.setRange(1, 240)
        self.interval_spin.setValue(int(self.settings.get('reconnect_minutes', 10) or 10))
        self.interval_spin.setFixedWidth(64)
        self.interval_spin.setToolTip('断线后每隔多少分钟重连一次（1~240 分钟）')
        row3.addWidget(self.interval_spin)
        row3.addWidget(field_label('分钟', 'FieldKey'))
        row3.addStretch(1)
        card.body.addLayout(row3)

        # 一行说明自动连接的顺序（照人工操作），省得用户猜程序在干什么
        order = field_label('顺序：插着网线先拨号 → 不行再连 Wi-Fi → 最后才 Portal 认证', 'Hint')
        order.setWordWrap(True)
        order.setToolTip('等价于人工操作：打开「拨号」点连接 / 点 Wi-Fi 选校园网 / 打开浏览器登录一次。\n'
                         '全程不看网卡 IP 是多少，也不做任何「踢设备」的动作')
        card.body.addWidget(order)
        return card

    def _build_log_card(self):
        card = Card('')
        self.log_card = card

        head = QtWidgets.QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(field_label('运行日志', 'CardTitle'))
        hint = field_label('不写文件', 'Hint')
        hint.setToolTip('运行日志只显示在这个窗口里，不写日志文件（只有内部异常才追加 crash.log）')
        head.addWidget(hint)
        head.addStretch(1)
        self.clear_log_btn = QtWidgets.QPushButton('清空')
        self.clear_log_btn.setObjectName('Tiny')
        self.clear_log_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.clear_log_btn.clicked.connect(lambda: self.log_view.clear())
        head.addWidget(self.clear_log_btn)
        self.log_toggle = QtWidgets.QPushButton('收起')
        self.log_toggle.setObjectName('Tiny')
        self.log_toggle.setCheckable(True)
        self.log_toggle.setChecked(True)
        self.log_toggle.setCursor(QtCore.Qt.PointingHandCursor)
        self.log_toggle.setToolTip('收起 / 展开运行日志')
        self.log_toggle.toggled.connect(self._toggle_log)
        head.addWidget(self.log_toggle)
        card.body.addLayout(head)

        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setObjectName('Log')
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(600)
        self.log_view.setMinimumHeight(92)
        self.log_view.setMaximumHeight(150)
        self.log_view.setPlaceholderText('这里会显示连接过程与结果…')
        card.body.addWidget(self.log_view)
        return card

    def _build_footer(self):
        footer = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(footer)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        self.save_btn = QtWidgets.QPushButton('保存配置')
        self.save_btn.setObjectName('Primary')
        self.save_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.save_btn.setFixedWidth(104)
        self.save_btn.clicked.connect(self.save_and_apply)
        lay.addWidget(self.save_btn)

        self.hint = field_label('就绪', 'Hint')
        lay.addWidget(self.hint, 1)

        grip = QtWidgets.QSizeGrip(footer)
        grip.setFixedSize(14, 14)
        lay.addWidget(grip, 0, QtCore.Qt.AlignBottom | QtCore.Qt.AlignRight)
        return footer

    def _build_tray(self):
        icon = QtGui.QIcon(ICON_PATH) if os.path.exists(ICON_PATH) else \
            self.style().standardIcon(QtWidgets.QStyle.SP_ComputerIcon)
        self.tray = QtWidgets.QSystemTrayIcon(icon, self)
        menu = QtWidgets.QMenu()
        self.tray_menu = menu

        show_action = menu.addAction('显示主界面')
        show_action.triggered.connect(self.show_window)
        connect_action = menu.addAction('立即连接')
        connect_action.triggered.connect(lambda: self.start_job('connect', notify=True))
        check_action = menu.addAction('刷新状态')
        check_action.triggered.connect(lambda: self.start_job('check'))
        menu.addSeparator()
        quit_action = menu.addAction('退出 Auto-xdwlan')
        quit_action.triggered.connect(self.quit_app)

        self.tray.setContextMenu(menu)
        self.tray.setToolTip('%s · %s' % (APP_NAME, APP_TITLE))
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason):
        if reason in (QtWidgets.QSystemTrayIcon.Trigger, QtWidgets.QSystemTrayIcon.DoubleClick):
            self.show_window()

    # ---------- 日志 / 状态显示 ----------

    def append_log(self, line):
        try:
            self.log_view.appendPlainText(line)
            scrollbar = self.log_view.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
        except Exception:
            pass

    def on_crash(self, detail):
        """界面线程里处理被兜底的内部异常：提示 + 托盘气泡，程序不退出。"""
        lines = [text for text in detail.strip().splitlines() if text.strip()]
        summary = lines[-1] if lines else '未知错误'
        try:
            log_line('内部异常已被兜底（详情见 crash.log）：%s' % summary)
            self.hint.setText('内部异常已记入 crash.log，程序继续运行')
        except Exception:
            pass
        try:
            self.tray.showMessage(APP_NAME, '程序内部异常，已记入 crash.log',
                                  QtWidgets.QSystemTrayIcon.Warning, 4000)
        except Exception:
            pass

    def _paint_dot(self, color):
        self.dot.setStyleSheet('background-color: %s; border-radius: 8px;' % color)

    def update_status(self, info):
        if not info:
            return
        self.status_info = info
        connected = bool(info.get('connected'))
        dials = info.get('dial') or []
        wifi = info.get('wifi') or ''
        target = info.get('target') or ''
        user = info.get('user') or ''
        detail = info.get('detail') or ''

        # 上网方式用最短的说法，细节（拨号连接名 / 出口 IP）放 tooltip
        egress = info.get('egress') or ''
        if connected:
            self._paint_dot(C_OK)
            self.status_text.setText('已连接')
            self.status_sub.setText('外网通畅' + (' · 出口 %s' % egress if egress else ''))
        elif dials or wifi or info.get('ip') or info.get('link'):
            self._paint_dot(C_ERR)
            self.status_text.setText('未连接')
            self.status_sub.setText('链路已就绪，等待校园网认证')
        else:
            self._paint_dot(C_WARN)
            self.status_text.setText('未接入网络')
            self.status_sub.setText('没检测到可用链路，请检查网线 / Wi-Fi')
        self.status_sub.setToolTip(detail or '')

        if dials:
            route = '网线拨号'
        elif info.get('ip'):
            route = '有线直连'
        elif info.get('link'):
            route = '网线已插入'
        elif wifi:
            route = '无线'
        else:
            route = '无可用链路'
        self.value_route.setText(route)
        self.value_route.setToolTip('出口 IP（流量实际走的那条链路）：%s%s'
                                    % (egress or '未知',
                                       ('\n拨号连接：' + '、'.join(dials)) if dials else ''))

        self.value_wifi.setText(wifi or '未连接')
        self.value_wifi.setToolTip('目标无线：%s' % (target or '未配置'))

        self.value_user.setText(user or '未登录')
        self.value_user.setToolTip('Portal 记录的在线账号')

        self.value_net.setText('通畅' if connected else '不通')
        self.value_net.setToolTip(detail or '—')
        self.tray.setToolTip('%s · %s\n%s%s' % (APP_NAME, self.status_text.text(), route,
                                               ('（出口 %s）' % egress) if egress else ''))

    WM_QUERYENDSESSION = 0x0011
    WM_ENDSESSION = 0x0016

    def nativeEvent(self, event_type, message):
        """关机 / 注销前先把拨号挂断（v2.17）。

        原因：Windows 关机时那条 PPPoE 会话经常没被 NAS 及时释放（RasMan 日志里只有「建立」没有
        「断开」），重启后新拨的这条就和它撞上同一个账号 —— 西电「一号一会话、保留旧的」，
        于是新会话十几秒后被顶掉（实测 11:00:22 建立 → 11:00:34 断开），得再拨一次才稳。
        在系统通知我们「要关机了」的时候主动断开，NAS 就能及时把旧会话放掉。
        只在关机 / 注销时做，程序正常退出（托盘→退出）不动拨号。
        """
        try:
            if event_type in (b'windows_generic_MSG', 'windows_generic_MSG'):
                import ctypes
                from ctypes import wintypes
                # 必须按 wintypes.MSG 的结构取 message（x64 下 hwnd 占 8 字节，
                # 手动按 4 字节数会读错偏移 —— 踩过一次）
                if isinstance(message, int):
                    address = message
                elif hasattr(message, '__int__'):
                    address = int(message)          # PyQt5 给的 sip.voidptr
                else:
                    address = message.value
                msg = ctypes.cast(address, ctypes.POINTER(wintypes.MSG)).contents
                if msg.message in (self.WM_QUERYENDSESSION, self.WM_ENDSESSION):
                    self._hangup_for_shutdown()
        except Exception:
            pass
        return super().nativeEvent(event_type, message)

    def _hangup_for_shutdown(self):
        """只做一次，并且必须很快返回（Windows 关机只给几秒）。"""
        if getattr(self, '_shutdown_hung_up', False):
            return
        self._shutdown_hung_up = True
        try:
            if autoconn.active_dial_connections():
                ok, message = autoconn.pppoe_hangup()
                log_line('系统即将关机 → 已断开拨号：%s' % message)
            else:
                log_line('系统即将关机 → 没有需要断开的拨号')
        except Exception as error:
            log_line('系统关机前断开拨号失败（不影响关机）：%s' % error)

    def set_busy(self, busy, job_name=''):
        self.busy = busy
        for button in (self.connect_btn, self.refresh_btn,
                       self.disconnect_btn, self.save_btn):
            button.setEnabled(not busy)
        self.stop_btn.setEnabled(busy)
        if not busy:
            return
        self.hint.setText('任务进行中，可点「停止」中止')
        self._paint_dot(C_ACCENT)
        if job_name == 'connect':
            self.status_text.setText('正在连接…')
            self.status_sub.setText('正在拨号 / 连接 Wi-Fi / 校园网认证（连上后还会再观察 %d 秒）'
                                    % getattr(autoconn, 'LINK_WATCH', 40))
        elif job_name == 'check':
            self.status_text.setText('检测中…')
            self.status_sub.setText('正在获取当前网络状态')
        else:
            self.status_text.setText('正在断开…')
            self.status_sub.setText('正在注销校园网登录')

    # ---------- 任务调度 ----------

    def start_job(self, job_name, notify=False, auto=False):
        if job_name == 'connect':
            self.connect_job_auto = auto
        if self.busy:
            # 上一个任务还没结束：记下来，等它结束后自动接着跑（开机时的「先检查再连接」就靠这个）
            if job_name != 'check' or not self.pending_job:
                self.pending_job = (job_name, notify)
            self.hint.setText('上一个任务还在进行中，「%s」稍后自动开始'
                              % JOB_TITLES.get(job_name, job_name))
            return False
        if job_name in ('connect', 'disconnect') and \
                not (self.username_edit.text().strip() and self.password_edit.text()):
            message = '请先在界面里填写账号和密码'
            self.hint.setText(message)
            log_line(message)
            self.show_window()
            self.tray.showMessage(APP_NAME, message,
                                  QtWidgets.QSystemTrayIcon.Warning, 4000)
            return False
        autoconn.STOP_REQUESTED = False
        apply_settings(self.collect_settings())
        self.set_busy(True, job_name)
        if notify:
            self.hint.setText('已发起「%s」，可点「停止」中止'
                              % JOB_TITLES.get(job_name, job_name))
        if self.worker is not None:
            # 理论上走不到这里（busy 已经挡住并发任务）；真发生了也不能直接丢掉引用
            retire_worker(self.worker)
        self.worker = Worker(job_name, self)
        self.worker.finished_job.connect(self.on_job_done)
        self.worker.start()
        return True

    def stop_job(self):
        """中止正在进行的任务：置停止标记，工作线程会在下一次检查时退出。"""
        if not self.busy:
            return
        autoconn.STOP_REQUESTED = True
        self.stop_btn.setEnabled(False)
        self.hint.setText('正在停止…（等当前这一步结束）')
        log_line('已请求停止当前任务…')

    def on_job_done(self, job_name, ok, info):
        worker = self.worker
        self.worker = None
        retire_worker(worker)
        self.set_busy(False, job_name)
        autoconn.STOP_REQUESTED = False
        self._run_pending_job()
        if job_name == 'check':
            self.update_status(info)
            if ok:
                self.hint.setText('网络正常')
                self.next_auto_connect_at = 0.0     # 网又通了，下次断网可以立刻重连
            else:
                self.hint.setText('当前无法上网')
                self._auto_reconnect_soon('检查到断网')
            return

        if job_name == 'connect':
            if ok:
                self.hint.setText('连接成功')
                self.next_auto_connect_at = time.time() + 30   # 刚连上，别马上又自动折腾
                self.tray.showMessage(APP_NAME, '校园网已连接，可以正常上网了',
                                      QtWidgets.QSystemTrayIcon.Information, 3000)
            else:
                self.hint.setText('连接失败，请查看日志')
                # 自动重连失败 → 5 分钟后再试（别死磕墙口）；手动点的失败 → 1 分钟后
                self.next_auto_connect_at = time.time() + (300 if self.connect_job_auto else 60)
                self.tray.showMessage(APP_NAME, '连接失败，请检查账号密码或网络环境',
                                      QtWidgets.QSystemTrayIcon.Warning, 4000)
            QtCore.QTimer.singleShot(400, lambda: self.start_job('check'))
            return

        if job_name == 'disconnect':
            message = (info or {}).get('message', '')
            log_line('断开结果：%s' % message)
            self.hint.setText('已断开校园网' if ok else '断开失败，请查看日志')
            QtCore.QTimer.singleShot(400, lambda: self.start_job('check'))

    def _run_pending_job(self):
        """把任务期间被挤掉的请求（例如开机时的自动连接）接着跑。"""
        if not self.pending_job:
            return
        job_name, notify = self.pending_job
        self.pending_job = None
        QtCore.QTimer.singleShot(200, lambda: self.start_job(job_name, notify))

    def _auto_refresh(self):
        if self.pending_job:
            return
        if self.busy:
            return
        self.start_job('check')

    def _auto_reconnect_soon(self, reason):
        """断网后立刻安排一次自动重连（v2.16）。

        以前只有那个「每 N 分钟」的定时器会重连：断网后最多要等 10 分钟，而且 10:43 那次
        拨号 12 秒就被掐掉、后面的检查发现了也没人重连 —— 用户只能自己点「立即连接」。
        现在改成：检查发现断网 → 立刻重连；两次自动重连之间至少隔 60 秒（防抖），
        自动重连失败后 5 分钟内不再自动重试（避免死磕墙口）。
        """
        if not self.settings.get('auto_reconnect'):
            return
        if self.busy or self.pending_job:
            return
        now = time.time()
        if now < self.next_auto_connect_at:
            left = int(self.next_auto_connect_at - now)
            if left > 5:
                log_line('%s：%d 秒后再自动重连…' % (reason, left))
            return
        self.next_auto_connect_at = now + 60
        log_line('%s → 立刻自动重连…' % reason)
        self.start_job('connect', auto=True)

    def _auto_reconnect(self):
        """兜底定时器（界面里那个「每 N 分钟」）：正常情况下上面的立即重连已经处理掉了。"""
        if not self.settings.get('auto_reconnect'):
            return
        if self.busy:
            return
        if self.status_info.get('connected'):
            return
        self._auto_reconnect_soon('兜底定时器到点（网络仍未连通）')

    def _update_reconnect_timer(self):
        minutes = max(1, int(self.settings.get('reconnect_minutes', 10) or 10))
        if self.settings.get('auto_reconnect'):
            self.reconnect_timer.start(minutes * 60 * 1000)
        else:
            self.reconnect_timer.stop()

    # ---------- 配置保存 ----------

    def collect_settings(self):
        data = dict(self.settings)
        data['username'] = self.username_edit.text().strip()
        data['password'] = self.password_edit.text()
        data['domain'] = self.domain_combo.currentData() or ''
        data['remember_password'] = self.remember_check.isChecked()
        data['autostart'] = self.autostart_check.isChecked()
        data['background'] = self.background_check.isChecked()
        data['connect_on_start'] = self.autoconnect_check.isChecked()
        data['auto_reconnect'] = self.reconnect_check.isChecked()
        data['reconnect_minutes'] = int(self.interval_spin.value())
        data['wifi_enable'] = self.wifi_check.isChecked()
        data['pppoe_enable'] = self.pppoe_check.isChecked()
        return data

    def save_and_apply(self):
        data = self.collect_settings()
        if not data['username'] or not data['password']:
            self.hint.setText('账号或密码为空，请先填写')
            log_line('未保存：账号或密码为空')
            return False
        ok, message = save_settings(data)
        self.settings = data
        apply_settings(data)
        self._update_reconnect_timer()
        log_line('%s（账号 %s）' % (message, data['username']))
        self.hint.setText(message)
        return ok

    def _on_autostart_toggled(self, checked):
        if self._loading:
            return
        ok, message = set_autostart(checked)
        log_line(message)
        self.hint.setText(message)
        if not ok:
            self._loading = True
            self.autostart_check.setChecked(not checked)
            self._loading = False

    # ---------- 窗口 / 托盘 ----------

    def show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def hide_to_tray(self):
        self.hide()
        if self.tray.isVisible():
            self.tray.showMessage(APP_NAME, '已最小化到托盘，程序仍在后台运行',
                                  QtWidgets.QSystemTrayIcon.Information, 2000)

    def closeEvent(self, event):
        if self.force_quit or not self.settings.get('background', True):
            event.accept()
            self.quit_app()
            return
        event.ignore()
        self.hide_to_tray()

    def quit_app(self):
        self.force_quit = True
        try:
            save_settings(self.collect_settings())
        except Exception:
            pass
        log_line('退出 %s' % APP_NAME)
        self.status_timer.stop()
        self.reconnect_timer.stop()
        self.tray.hide()
        QtWidgets.QApplication.quit()
        os._exit(0)


# ============================== 入口 ==============================


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    tray_only = ('--tray' in argv) or ('--silent' in argv)
    connect_now = '--connect' in argv

    if sys.stdout is None:
        sys.stdout = open(os.devnull, 'w')
    if sys.stderr is None:
        sys.stderr = open(os.devnull, 'w')

    # 兜住未捕获异常：PyQt5 默认会 qFatal() 直接 abort 掉进程（旧版「自己关闭」的元凶）
    install_excepthook()

    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps, True)
    app = QtWidgets.QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    if os.path.exists(ICON_PATH):
        app.setWindowIcon(QtGui.QIcon(ICON_PATH))
    app.setStyleSheet(STYLE)

    # 把 autoconn 的日志接到界面上（只显示在窗口里，不写日志文件）
    autoconn.log = log_line
    autoconn.LOG_FILE = None

    guard = InstanceGuard()
    if not guard.listen() and guard.notify_existing():
        return 0

    window = MainWindow(tray_only=tray_only)
    CRASH_BUS.message.connect(window.on_crash)
    guard.activated.connect(window.show_window)
    if not tray_only:
        window.show()
    if connect_now:
        QtCore.QTimer.singleShot(500, lambda: window.start_job('connect', notify=True))
    return app.exec_()


if __name__ == '__main__':
    sys.exit(main())
