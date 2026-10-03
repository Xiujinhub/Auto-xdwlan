# -*- coding: utf-8 -*-
"""界面自检：构造窗口、渲染截图、逐个控件量「文字宽度 vs 控件宽度」。"""
import os
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, r'e:\code\Auto-xdwlan-main')

from PyQt5 import QtWidgets, QtCore      # noqa: E402
import app as A                           # noqa: E402

# 不许打扰正在运行的实例，也不许真去连网
A.InstanceGuard = type('NoGuard', (QtCore.QObject,), {'__init__': lambda self, parent=None: None,
                                                      'acquire': lambda self: True,
                                                      'notify_existing': lambda self: False})
_real_load = A.load_settings
A.load_settings = lambda: dict(_real_load(), connect_on_start=False, autostart=False,
                               auto_reconnect=False, background=False)

qapp = QtWidgets.QApplication(sys.argv)
win = A.MainWindow()
win.show()
qapp.processEvents()
qt_scale = qapp.primaryScreen().logicalDotsPerInch() / 96.0
print('screen: dpr=%s logicalDpi=%s scale=%.2f  window=%dx%d'
      % (qapp.primaryScreen().devicePixelRatio(), qapp.primaryScreen().logicalDotsPerInch(),
         qt_scale, win.width(), win.height()))

# 灌一些真实状态进去，确认长文案也能显示全
win.status_text.setText('已连接')
win.status_sub.setText('外网通畅 · 出口 10.193.176.78')
win.value_route.setText('网线拨号（宽带连接）')
win.value_user.setText('25201111510')
win.value_wifi.setText('XDU-STUDENT-WLAN-5G(10.196.23.197)')
win.value_net.setText('通畅（204）')
win.log_view.appendPlainText('第 1/3 轮：按人工做法连接（有线：已插网线；无线：未连接）')
win.log_view.appendPlainText('插着网线 → 按人工做法拨号（宽带连接，第 1 轮）…')
win.log_view.appendPlainText('网线拨号：拨号成功（宽带连接）')
win.log_view.appendPlainText('拨号后已能上网（http://connect.rom.miui.com/generate_204 -> 204）')
qapp.processEvents()

offenders = []
PAD = {'QCheckBox': 26, 'QPushButton': 18, 'QToolButton': 16, 'QLabel': 2}
for widget in win.findChildren(QtWidgets.QWidget):
    if not widget.isVisible():
        continue
    text = getattr(widget, 'text', None)
    if not isinstance(text, str) or not text.strip():
        continue
    if isinstance(widget, (QtWidgets.QLineEdit, QtWidgets.QSpinBox, QtWidgets.QComboBox)):
        continue
    if widget.wordWrap():            # 允许换行的标签不算裁切
        continue
    need = widget.fontMetrics().boundingRect(text).width() + PAD.get(type(widget).__name__, 12)
    if need > widget.width() + 1:
        offenders.append((type(widget).__name__, text, need, widget.width()))

print('--- 放不下的控件：%d 个 ---' % len(offenders))
for kind, text, need, have in offenders:
    print('   %-12s 需要 %3dpx / 实际 %3dpx  %s' % (kind, need, have, text))

path = os.path.join(os.environ.get('TEMP', '.'), 'ui_v215_full.png')
win.grab().save(path)
print('截图: %s' % path)
