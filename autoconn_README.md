# 西电校园网自动登录（Auto-xdwlan）

让电脑开机后自动连上西电校园网（`w.xidian.edu.cn`，新版 srun Portal）：
一个图形界面 exe（`Auto-xdwlan.exe`，点击即用）+ 一份核心逻辑脚本（`autoconn.py`）。

## 一、文件清单

```
E:\code\Auto-xdwlan\
├─ dist\Auto-xdwlan.exe     图形界面版：单文件 exe（47 MB），双击即用 ← 日常用这个
├─ app.py                   PyQt5 界面源码（exe 的打包入口，也是 autoconn.py 的界面壳）
├─ autoconn.py              核心网络逻辑：链路选择 + Portal 认证（无界面，可命令行单跑）
├─ build_exe.ps1            一键把 app.py 打包成 dist\Auto-xdwlan.exe
├─ logo\
│   ├─ Auto-xdwlan.png      logo 原图
│   ├─ Auto-xdwlan.ico      程序/托盘图标（多尺寸）
│   └─ logo_192.png         界面里显示的 logo 小图
├─ autoconn_README.md       本文件
├─ .gitignore               提交时忽略 settings.json / dist / build / __pycache__（密码不进仓库）
└─ settings.json            界面保存的账号与设置（首次「保存配置」后自动生成）
```

已删除的多余文件（本次清理）：

| 已删除 | 原因 |
| --- | --- |
| `autoconn_run.vbs` | 静默启动器（`pythonw` 拉起脚本）。界面版自己就能开机后台启动，不再需要 |
| `install_autoconn_task.ps1` | 注册计划任务的老方案。功能已被界面版内置的「开机自启动」覆盖，老任务也已注销 |
| `autoconn.py.bak` | 最初那份旧脚本（旧的 `srun_portal_pc.php` 接口，服务器早已返回 404），已无用 |

> 现在只用「`dist\Auto-xdwlan.exe`（或 `app.py`）+ `autoconn.py`」两个程序文件，没有启动器、没有计划任务。
> 程序**不产生任何日志文件**：日志只在界面窗口里显示（内存中），关掉即清空。

## 二、快速开始

```powershell
# 1) 直接用（推荐）：双击 dist\Auto-xdwlan.exe，填账号密码 → 保存配置
E:\code\Auto-xdwlan\dist\Auto-xdwlan.exe

# 2) 改界面源码后调试运行
D:\Anaconda\python.exe E:\code\Auto-xdwlan\app.py

# 3) 改完代码重新打包（生成 dist\Auto-xdwlan.exe，打包前请先退出正在运行的程序）
powershell -ExecutionPolicy Bypass -File E:\code\Auto-xdwlan\build_exe.ps1

# 4) 只用命令行（不走界面）
D:\Anaconda\python.exe E:\code\Auto-xdwlan\autoconn.py --check     # 只看状态
D:\Anaconda\python.exe E:\code\Auto-xdwlan\autoconn.py             # 该连就自动连（开机自启可挂这个）
```

常用命令行参数：`--check` 只检查、`--login` 强制登录一次、`--logout` 注销（踢下线）、
`--wifi` 只连校园 Wi-Fi、`--pppoe` 只拨号、`--pppoe-down` 断拨号、`--verbose` 打印请求细节。

## 三、核心功能

界面版（`Auto-xdwlan.exe`）：

| 区域 | 功能 |
| --- | --- |
| 连接状态 | 状态灯 + 「已连接 / 未连接 / 未接入网络」；显示上网方式（网线拨号·宽带连接 / 无线 SSID / 有线直连）、Portal 在线账号、外网连通详情；每 60 秒自动刷新 |
| 操作按钮 | 「立即连接」跑完整流程（拨号 → 连 Wi-Fi → Portal 认证）；「刷新」只看状态不动网络；「断开」注销 Portal 并挂断拨号 |
| 账号信息 | 账号、密码（可点「显示」核对）、运营商（校园网 / 电信 `@dx` / 联通 `@lt` / 移动 `@yd`）、记住密码 |
| 设置 | **开机自启动**（写当前用户注册表 `HKCU\...\Run`，带 `--tray` 静默进托盘，不需要管理员）、**后台运行**（关窗口=最小化到托盘，不断网）、启动后自动连接、**断线自动重连**（间隔 1~240 分钟）、允许连接 Wi-Fi / 允许网线拨号 |
| 运行日志 | 滚动显示每一步（连 Wi-Fi、拨号、取 token、提交认证、外网探测结果）；只显示在窗口里，不写文件 |
| 托盘 | 双击/单击唤出主界面；右键菜单：显示主界面 / 立即连接 / 刷新状态 / 退出 |
| 单实例 | 重复双击 exe 不会开第二个进程，而是把已在托盘运行的窗口叫出来 |

命令行版（`autoconn.py`）：状态自检、强制登录、注销、只连 Wi-Fi、只拨号、断拨号、详细模式，
可被任何脚本/计划任务调用（退出码 0 = 已能上网，1 = 失败）。

## 四、核心逻辑

### 4.1 三条上网路径（按顺序自动选，前面能上网就不动后面的）

1. **网线直连**：有线网卡拿到正常 IPv4 → 直接走 Portal 认证。
2. **网线拨号（PPPoE）**：插着网线却只有 `169.254.x.x` → `rasdial` 拨号。
   系统里已有拨号连接（比如手工建的「宽带连接」）就**直接复用**；一条都没有时才按 `rasphone.pbk`
   格式自建 `XidianPPPoE`（写在当前用户拨号本，不需要管理员）。拨通一般就能上网，通常无需 Portal。
3. **无线**：Portal 不可达 → `netsh wlan connect stu-xdwlan`（开放式网络，系统缺配置文件时按开放式自动生成）
   → 等网关就绪 → 走 Portal 认证。

判断「能不能上网」的硬标准：探测 `http://connect.rom.miui.com/generate_204` 必须返回 **204**。
未登录时校园网会把请求劫持到 Portal 页并返回 200，所以只看状态码不够，脚本还回退探测 `http://www.baidu.com/`。

### 4.2 Portal（srun）认证算法

```
0. 外网不通时先建链路（见 4.1）
1. GET /cgi-bin/get_challenge   → challenge(token) + client_ip（token 与(用户名,IP)绑定，60 秒过期）
2. GET /cgi-bin/srun_portal     → action=login 提交
     password = '{MD5}' + HMAC-MD5(明文密码, token)
     info     = '{SRBX1}' + 自定义字母表base64( XXTEA(json用户信息, 密钥=token) )
     chksum   = SHA1(token+用户名+token+hmd5+token+ac_id+token+ip+token+200+token+1+token+info)
3. 成功判定：error == 'ok' 或 res == 'ok'（'ip_already_online_error' 也算成功）
4. err_code=2（本机 IP 上有状态异常的旧会话）→ 先调 /cgi-bin/rad_user_dm 踢掉旧会话再重试
```

XXTEA 用的自定义 base64 字母表（与门户前端 `Portal.js` 一致）：

```
LVoJPiCN2R8G90yg+hmFHuacZ1OWMnrsSTXkYpUq/3dlbfKwv6xztjI7DeBE45QA
```

加解密/编码实现与前端 `Portal.js` 的 `encode()/s()/l()/base64` **逐字节对齐验证过**
（用 `cscript` 跑前端原版 JS 与 Python 对比，密文十六进制完全一致），不是照抄网上的旧实现。

### 4.3 autoconn.py 主流程 `auto_connect()`

```
查一次 Portal 在线账号（仅记录）
for attempt in 1..MAX_RETRY(20):
    check_internet() 能通 → 成功退出(0)
    Portal 不可达时：
        先试一次网线拨号（PPPoE）→ 通了就退出
        还不可达 → 连 Wi-Fi(stu-xdwlan) + 等网关就绪（最多 30 秒）
    提交 login()：成功则等 2 秒再验外网 → 通就退出
        若返回「已在线」但外网仍不通：连续两次就停止折腾（避免探测被劫持时死循环）
    检测到旧会话 → clear_stale_session() 踢掉一次再重试
    休眠 RETRY_INTERVAL(15 秒) 后重试
全部失败 → 提示「检查账号密码 / 是否在校园网内」，退出(1)
```

### 4.4 界面 app.py 的任务模型（界面为什么不卡）

* 所有网络动作都在 `Worker(QtCore.QThread)` 后台线程里跑，日志通过 `LOG_BUS` 信号回到界面线程。
* 只有三件事：`connect / check / disconnect`（`JOBS`）；同一时刻只跑一个，**后到的请求排队**（`pending_job`），
  前一个做完接着跑 —— 不会出现「启动时的状态检查把自动连接顶掉」的问题。
* 启动节奏：200ms 后 `check`；600ms 后若开了「启动后自动连接」再 `connect`；
  之后每 60 秒 `check` 一次；开了「断线自动重连」则每 N 分钟 `connect` 一次。
* 单实例：用 `QLocalServer` 占坑，第二个实例连上去发条消息，第一个实例把窗口唤到前台，第二个自己退出。
* 界面日志只存在内存里（最多 600 行），退出即清空，**不写任何 `.log` 文件**。

### 4.5 配置与开机自启

* 设置存 `settings.json`，**跟着 exe 走**（exe 旁边生成；源码运行时写在项目目录）。
  密码用异或 + base64 混淆存储，**不是加密**，别把文件发给别人。
* 界面把配置直接同步到底层模块的全局变量（`autoconn.USERNAME/PASSWORD/DOMAIN/WIFI_*/PPPOE_*`），
  底层不读配置文件，改一次到处生效。
* 开机自启：写 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`，值为 `"...\Auto-xdwlan.exe" --tray`
  （不需要管理员）。勾选后如果挪动了 exe，要重新勾一次（注册表里存的是旧路径）。

## 五、可调参数（`autoconn.py` 顶部配置区）

| 常量 | 默认值 | 说明 |
| --- | --- | --- |
| `USERNAME` / `PASSWORD` | 已填好 | 校园网账号密码（在界面里改也行，界面优先） |
| `DOMAIN` | `''` | 运营商后缀：`''` 校园网 / `'@dx'` 电信 / `'@lt'` 联通 / `'@yd'` 移动 |
| `PORTAL` / `AC_ID` | `w.xidian.edu.cn` / `1` | 认证门户与认证域 |
| `WIFI_SSID` | `stu-xdwlan` | 要自动连的无线网；设成 `''` 就完全不碰 Wi-Fi |
| `WIFI_AUTO_CONNECT` | `True` | 是否允许自动连接 / 切换 Wi-Fi |
| `WIFI_CONNECT_WAIT` | `30` | 等待关联成功 / 网关就绪的最长秒数 |
| `PPPOE_ENABLE` | `True` | 是否允许网线拨号 |
| `PPPOE_NAME` | `XidianPPPoE` | 自建拨号条目的名字（已有别的拨号连接会直接复用，如「宽带连接」） |
| `PPPOE_USER` / `PPPOE_PASSWORD` | `''` | 留空 = 用上面的账号密码；运营商线路可写 `'学号@dx'` |
| `MAX_RETRY` / `RETRY_INTERVAL` | `20` / `15` | 重试次数 / 每次间隔秒数（界面版设为 18 / 10，见 `app.py`） |
| `REQUEST_TIMEOUT` | `10` | 单次 HTTP 超时秒数（界面版设为 8） |
| `LOG_FILE` | `None` | **默认不写日志文件**；需要留档时自己赋一个路径 |

界面里能直接改的（存进 `settings.json`）：账号、密码、运营商、记住密码、开机自启动、后台运行、
启动后自动连接、断线自动重连及间隔、允许连接 Wi-Fi、允许网线拨号。

## 六、失败时的排查顺序

1. 界面版看窗口里的「运行日志」，命令行版看控制台输出（都不写日志文件）。
2. `autoconn.py --check` 看 Portal 能不能连上：连不上说明**不在校园网内**或网卡没拿到 IP。
3. `autoconn.py --login --verbose` 会打印完整请求参数和服务器原始返回。
4. 服务器返回码含义（脚本已自动翻译成中文）：
   * `login_ok`：认证成功
   * `ip_already_online_error`：本机 IP 已在线，脚本视为成功
   * `sign_error` / `challenge_expire_error`：token 过期（60 秒内没提交完），脚本会自动重试
   * `E2620`：账号在线设备数超限，去自助服务 `zfw.xidian.edu.cn` 踢掉设备
   * `login_error` + `INFO Error，err_code=2`：本机 IP 上残留了状态异常的旧会话。脚本会自动调用
     「设备下线」接口清掉再重试；手动清理也可以 `autoconn.py --logout`
5. 拨号报 **623**：系统已有「所有用户」的拨号本（RAS 可能不认自建条目）。手动建一条即可：
   设置 → 网络和 Internet → 拨号 → 设置新连接 → 连接到 Internet → 宽带(PPPoE)，填账号密码，脚本之后会自动复用。

## 七、已在你这台电脑上实测通过

| 场景 | 结果 |
| --- | --- |
| 已在线时运行 | `网络已连通（generate_204 -> 204），无需登录`，退出码 0，不做任何多余操作 |
| 用 Portal「设备下线」接口踢掉会话（模拟开机未登录） | 自动登录成功：`第 1 次登录：认证成功（login_ok）` → `联网验证通过` |
| 手动断开 Wi-Fi | 自己扫描并连回：`Wi-Fi 未连接，尝试连接 stu-xdwlan…` → `已连上 Wi-Fi stu-xdwlan` → `网络已连通` |
| 挂断网线拨号（模拟开机没网） | 自己拨号回来：`检测到网线已插入，尝试网线拨号（PPPoE）…` → `拨号成功（宽带连接）` → `网线拨号后已能上网`（4.8 秒） |
| 打包后的 exe 实机运行 | 界面显示「已连接 / 网线拨号·宽带连接 / 在线账号」，日志面板正常，**dist 目录里只有 exe、没有任何 .log** |
| 开机自启开关 | 注册表 `HKCU\...\Run` 的写入与取消均实测正确 |

实测还确认了三个关键点：

1. **未登录时校园网会把任意 HTTP 请求劫持到 Portal 页并返回 200** → 判断在线不能只看状态码。
2. **Wi-Fi 断开时 Portal 完全不可达** → 必须先建好链路（拨号 / 连 Wi-Fi），再走认证。
3. **网线拨号（PPPoE）复用同一套账号密码就能拨通**，拨上后不需要 Portal 认证。

## 八、注意事项

* 依赖：`requests`（命令行版）；界面版还需要 `PyQt5`（Anaconda 自带）；打包需要 `pyinstaller`。
  当前打包环境：Python 3.7 + PyQt5 5.9.2 + PyInstaller 5.13.2。
* `build_exe.ps1` 是 **UTF-8 with BOM**：用不支持 BOM 的编辑器保存后，若 PowerShell 报「缺少右 }」，
  另存为「UTF-8 带 BOM」即可。打包前先退出正在运行的程序（脚本已会自动检测并中文提示）。
* 程序不写日志文件，但会在自己目录生成 `settings.json`（配置）；密码是混淆存储，不是加密。
* 如果机器上还在跑第三方校园网客户端（例如 `D:\xdwlan-login\xdwlan-login.exe`），两者不冲突、
  功能有重叠（都会做 Portal 认证），同时开可能重复登录，但不会互相破坏。
* `dist\Auto-xdwlan.exe` 就是最新版；可以整个拷到别处用（配置跟着 exe 走），
  移动后记得重新勾一次「开机自启动」。
* **远程仓库**：<https://github.com/Xiujinhub/Auto-xdwlan>。`settings.json`（含账号密码）已加入
  `.gitignore`，不会被上传；`autoconn.py` 里的 `USERNAME/PASSWORD` 也留空，账号密码只存在本机。
* 这台电脑的 hosts 把 github.com 屏蔽成了 `127.0.0.1`，所以仓库里配了代理：
  `git config --local http.proxy http://127.0.0.1:7890`（Clash 端口）、`http.schannelCheckRevoke false`。
  换网络/关代理后想取消：`git config --local --unset http.proxy`。

