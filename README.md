# 西电校园网自动登录（Auto-xdwlan）

开机后自动连上西电校园网（`w.xidian.edu.cn`，新版 srun Portal）：
一个图形界面程序（`dist\Auto-xdwlan.exe`，双击即用）+ 一份核心逻辑脚本（`autoconn.py`）。

## 一、代码文件清单

```
E:\code\Auto-xdwlan\
├─ dist\Auto-xdwlan.exe     图形界面版：单文件 exe（47 MB），双击即用
├─ app.py                   PyQt5 界面源码（exe 的打包入口）
├─ autoconn.py              核心网络逻辑：链路选择 + srun Portal 认证（无界面，可命令行单跑）
├─ build_exe.ps1            一键打包脚本：生成 dist\Auto-xdwlan.exe
├─ README.md                本文件
├─ logo\
│   ├─ Auto-xdwlan.png      logo 原图
│   ├─ Auto-xdwlan.ico      程序 / 托盘图标（多尺寸）
│   └─ logo_192.png         界面里显示的 logo 小图
├─ settings.json            本机私有配置（账号、密码、各选项；程序运行时生成）
├─ crash.log                只有出现内部异常时才生成（排查用，可直接删；超 256 KB 滚成 crash.log.old）
└─ .gitignore               提交时忽略 settings.json / crash.log / dist / build / __pycache__
```

不上传到 Git 仓库的内容：`settings.json`（含账号密码）、`crash.log`、`dist\`（47 MB 的 exe）、`build\`、`__pycache__\`。

## 二、软件功能

界面版（`Auto-xdwlan.exe`）：

| 区域 | 功能 |
| --- | --- |
| 连接状态 | 状态灯 + 「已连接 / 未连接 / 未接入网络」；显示上网方式（网线拨号·宽带连接 / 无线 SSID / 有线直连）、Portal 在线账号、外网连通详情；每 60 秒自动刷新 |
| 操作按钮 | 「立即连接」跑完整流程（拨号 → 连 Wi-Fi → Portal 认证）；「刷新」只看状态不动网络；「断开」注销 Portal 并挂断拨号；任务进行中这四个按钮会置灰，可点「停止」中止 |
| 账号信息 | 账号、密码（可点「显示」核对）、运营商（校园网 / 电信 `@dx` / 联通 `@lt` / 移动 `@yd`）、记住密码 |
| 设置 | **开机自启动**（写当前用户注册表，带 `--tray` 静默进托盘，不需要管理员）、**后台运行**（关窗口 = 最小化到托盘，不断网）、启动后自动连接、**断线自动重连**（间隔 1~240 分钟）、允许连接 Wi-Fi / 允许网线拨号 |
| 运行日志 | 滚动显示每一步（连 Wi-Fi、拨号、取 token、提交认证、外网探测结果）；**只在窗口里显示**，平时不写日志文件（只有内部异常才追加 `crash.log`） |
| 托盘 | 双击/单击唤出主界面；右键菜单：显示主界面 / 立即连接 / 刷新状态 / 退出 |
| 单实例 | 重复双击 exe 不会开第二个进程（第二个进程**会立刻退出**，这是正常的），而是把已在托盘运行的窗口叫出来 |

命令行版（`autoconn.py`）能力：状态自检、强制登录、注销、只连 Wi-Fi、只拨号、断开拨号、详细模式；
退出码 `0` = 已能上网，`1` = 失败，方便被别的脚本/计划任务调用。

## 三、软件逻辑

### 3.1 三条上网链路（按顺序自动选，前面能上网就不动后面的）

1. **网线直连**：有线网卡拿到正常 IPv4 → 直接走 Portal 认证。
2. **网线拨号（PPPoE）**：插着网线却只有 `169.254.x.x` → `rasdial` 拨号。
   系统里已有拨号连接（如手工建的「宽带连接」）就**直接复用**；一条都没有时才按 `rasphone.pbk`
   格式自建 `XidianPPPoE`（写在当前用户拨号本，不需要管理员）。拨通一般就能上网，通常无需 Portal。
3. **无线**：Portal 不可达 → `netsh wlan connect stu-xdwlan`（开放式网络，系统缺配置文件时自动生成）
   → 等网关就绪 → 走 Portal 认证。

判断「能不能上网」的硬标准：探测 `http://connect.rom.miui.com/generate_204` 必须返回 **204**。
未登录时校园网会把请求劫持到 Portal 页并返回 200，所以只看状态码不够，脚本还回退探测 `http://www.baidu.com/`。

### 3.2 Portal（srun）认证算法

```
0. 外网不通时先建链路（见 3.1）
1. GET /cgi-bin/get_challenge   → challenge(token) + client_ip（token 与(用户名,IP)绑定，60 秒过期）
2. GET /cgi-bin/srun_portal     → action=login 提交
     password = '{MD5}' + HMAC-MD5(明文密码, token)
     info     = '{SRBX1}' + 自定义字母表base64( XXTEA(json用户信息, 密钥=token) )
     chksum   = SHA1(token+用户名+token+hmd5+token+ac_id+token+ip+token+200+token+1+token+info)
3. 成功判定：error == 'ok' 或 res == 'ok'（'ip_already_online_error' 也算成功）；
   `err_code=2`（**原文是 `INFO Error锛宔rr_code=2`** —— 中文逗号被 Portal 按 GBK 重编码，
   把 `err_code` 的 `e` 吃掉了，所以脚本按 `code=2` 匹配）表示「本机/账号已有在线会话」：
   外网通就当成功（网线拨号 PPPoE 在线时必然遇到，不用重复认证），外网确实不通才走下面两步
4. 外网确实不通时：**先把本机链路接回来**（网线拨号重拨 → 连 Wi-Fi），还是被拒才清旧会话：
   1. `GET /v1/srun_portal_online?user_name=账号&password=md5(密码)` —— 门户网页上
      「在线设备管理」用的接口，能看到账号在所有 IP 上的会话（含每台设备的 OS 名）；
      取不到就退回 `rad_user_info`（响应里的 `online_device_detail` 就是全部会话）
   2. 逐个 IP 注销：先 `/cgi-bin/rad_user_dm`（设备下线，sign=sha1(时间+账号+IP+1+时间)），
      不行再 `action=logout`（带上该 IP）；本机的旧会话先清，必要时连账号上其它设备一起清
```

XXTEA 用的自定义 base64 字母表（与门户前端 `Portal.js` 一致）：
`LVoJPiCN2R8G90yg+hmFHuacZ1OWMnrsSTXkYpUq/3dlbfKwv6xztjI7DeBE45QA`

加解密/编码实现与前端 `Portal.js` 的 `encode()/s()/l()/base64` **逐字节对齐验证过**
（用 `cscript` 跑前端原版 JS 与 Python 对比，密文十六进制完全一致）。

### 3.3 autoconn.py 主流程 `auto_connect()`

```
查一次 Portal 在线账号（仅记录）
for attempt in 1..MAX_RETRY(20):
    check_internet() 能通 → 成功退出(0)
    Portal 不可达时：
        先试一次网线拨号（PPPoE）→ 通了就退出
        还不可达 → 连 Wi-Fi(stu-xdwlan) + 等网关就绪（最多 30 秒）
    提交 login()：成功则等 2 秒再验外网 → 通就退出
        若返回 err_code=2 / ip_already_online（本机或账号已有在线会话）：
            外网已通 → 直接按成功算（网线拨号 PPPoE 在线时必然遇到，不用重复认证）
            外网不通 → 先恢复本机链路（重拨 / 连 Wi-Fi，一次）—— 断网测试最常见的就是这种：
                       Portal 哪都能到，可拨号已经掉线，光重试 Portal 认证永远登不上去
            还是被拒 → clear_stale_sessions() 按「在线设备」列表注销旧会话，随即重登（不必等间隔）
        若返回「已在线」但外网仍不通：连续两次就停止折腾（避免探测被劫持时死循环）
    休眠 RETRY_INTERVAL(15 秒) 后重试
全部失败 → 提示「检查账号密码 / 是否在校园网内」，退出(1)
```

`clear_stale_sessions()` 清旧会话的顺序：

1. 先清「看起来是本机」的会话：网卡 IP 对得上，或 OS 名 / 客户端名对得上（`_looks_like_ours()`）；
2. 剩下的是账号上其它设备的会话，只有 `CLEAR_OTHER_DEVICES = True` 时才一起注销（默认 True，
   日志里会写明动了哪些 IP）。因为 `err_code=2` 是**账号级**的：会话留在别的 IP 上时，
   只清本机那一个 IP 根本没用 —— 这就是以前「断网后一直登不上、日志里反复 err_code=2」的老 bug。

### 3.4 界面（app.py）的任务模型

* 所有网络动作都在 `Worker(QtCore.QThread)` 后台线程里跑，日志通过 `LOG_BUS` 信号回到界面线程，界面不卡。
* 只有三件事：`connect / check / disconnect`（`JOBS`）；同一时刻只跑一个，**后到的请求排队**（`pending_job`），
  前一个做完接着跑 —— 不会出现「启动时的状态检查把自动连接顶掉」。
* 启动节奏：200ms 后 `check`；600ms 后若开了「启动后自动连接」再 `connect`；之后每 60 秒 `check` 一次；
  开了「断线自动重连」则每 N 分钟 `connect` 一次。
* 单实例：用 `QLocalServer` 占坑，第二个实例连上去发条消息，第一个实例把窗口唤到前台，第二个自己退出
  （所以「双击 exe 一闪就没了」不是崩溃，而是托盘里本来就有一个实例在跑）。
* 界面日志只在内存里（最多 600 行），退出即清空。
* **崩溃兜底**：启动时 `install_excepthook()` 接管未捕获异常 —— PyQt5 默认会把「槽函数 / 定时器回调里的
  Python 异常」升级成 `qFatal()` → `abort()`，进程直接消失（Windows 错误报告里是
  `Auto-xdwlan.exe` / `ucrtbase.dll` / `0xc0000409`）；现在改成写 `crash.log` + 界面/托盘提示，**程序继续跑**。
* 工作线程统一用 `retire_worker()` 回收：等线程真正结束（`QThread.finished`）再 `deleteLater`，
  避免 Qt 的 `QThread: Destroyed while thread is still running` 同样会 abort 掉进程。

### 3.5 配置与开机自启

* 设置存 `settings.json`，**跟着 exe 走**（exe 旁边生成；源码运行时写在项目目录）。
  密码用异或 + base64 混淆存储，**不是加密**。
* 界面把配置直接同步到底层模块的全局变量（`autoconn.USERNAME/PASSWORD/DOMAIN/WIFI_*/PPPOE_*`）；
  `autoconn.py` 自身也会尝试读同目录的 `settings.json`（所以命令行用法不需要把密码写进源码）。
* 开机自启：写 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`，值为 `"...\Auto-xdwlan.exe" --tray`。

## 四、用法

### 4.1 直接使用（推荐）

1. 双击 `E:\code\Auto-xdwlan\dist\Auto-xdwlan.exe`
2. 在「账号信息」里填账号 / 密码 / 运营商 → 点「保存配置」
3. 需要开机自动联网：勾「开机自启动」；想让关窗口后继续后台跑：勾「后台运行」
4. 想立刻联网：点「立即连接」；只想看状态：点「刷新」

```powershell
# 也可以带参数启动
E:\code\Auto-xdwlan\dist\Auto-xdwlan.exe                # 正常显示窗口
E:\code\Auto-xdwlan\dist\Auto-xdwlan.exe --tray         # 直接后台进托盘（开机自启用的就是这个）
E:\code\Auto-xdwlan\dist\Auto-xdwlan.exe --connect      # 启动后立刻连接一次
```

### 4.2 源码方式运行（改界面代码时调试）

```powershell
D:\Anaconda\python.exe E:\code\Auto-xdwlan\app.py
```

### 4.3 重新打包 exe（改完 app.py / autoconn.py 后）

```powershell
cd E:\code\Auto-xdwlan-main
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1 -Python D:\Anaconda\envs\paddle_env\python.exe
Copy-Item .\dist\Auto-xdwlan.exe D:\Auto-xdwlan\ -Force      # 部署目录（exe 和 settings.json 都在这儿）
```

输出 `dist\Auto-xdwlan.exe`。**打包前先退出正在运行的程序**（脚本会检测，占用时会提示"请先退出程序"）。
打包会把 `dist\` 整个重建，但脚本会自动把 `dist\settings.json` 备份到临时目录、打完再还原，
所以界面里填过的账号密码不会丢。

本机打包环境（2026-09 实测）：`D:\Anaconda\python.exe` 里**没有** PyInstaller，
能用的解释器是 `D:\Anaconda\envs\paddle_env\python.exe`（Python 3.9 + PyQt5 5.15.9 + PyInstaller 6.16，
打出来 ~40.8 MB）；`envs\tracker`（Python 3.7，和 README 里写的 3.7 + PyQt5 5.9.2 最接近）没有 PyQt5，打不了。
注意两条命令不能粘成一行，PowerShell 会把整串当成一个文件名（`-File "…ps1Copy-Item"` 就是这个问题），
要么换行，要么用 `;` 分隔。

### 4.4 只用命令行（不启动界面）

```powershell
D:\Anaconda\python.exe E:\code\Auto-xdwlan\autoconn.py              # 该连就自动连
D:\Anaconda\python.exe E:\code\Auto-xdwlan\autoconn.py --check      # 只检查状态，不做任何动作
D:\Anaconda\python.exe E:\code\Auto-xdwlan\autoconn.py --login      # 强制登录一次
D:\Anaconda\python.exe E:\code\Auto-xdwlan\autoconn.py --logout     # 注销（把本机踢下线，用于验证）
D:\Anaconda\python.exe E:\code\Auto-xdwlan\autoconn.py --wifi       # 只连校园 Wi-Fi
D:\Anaconda\python.exe E:\code\Auto-xdwlan\autoconn.py --pppoe      # 只做网线拨号
D:\Anaconda\python.exe E:\code\Auto-xdwlan\autoconn.py --pppoe-down # 断开网线拨号
D:\Anaconda\python.exe E:\code\Auto-xdwlan\autoconn.py --verbose    # 打印请求细节
```

### 4.5 可调参数（`autoconn.py` 顶部配置区）

| 常量 | 默认值 | 说明 |
| --- | --- | --- |
| `USERNAME` / `PASSWORD` | `''` | 留空即可：会读同目录 `settings.json` 里的账号密码 |
| `DOMAIN` | `''` | 运营商后缀：`''` 校园网 / `'@dx'` 电信 / `'@lt'` 联通 / `'@yd'` 移动 |
| `PORTAL` / `AC_ID` | `w.xidian.edu.cn` / `1` | 认证门户与认证域 |
| `WIFI_SSID` | `stu-xdwlan` | 要自动连的无线网；设成 `''` 就完全不碰 Wi-Fi |
| `WIFI_AUTO_CONNECT` | `True` | 是否允许自动连接 / 切换 Wi-Fi |
| `WIFI_CONNECT_WAIT` | `30` | 等待关联成功 / 网关就绪的最长秒数 |
| `PPPOE_ENABLE` | `True` | 是否允许网线拨号 |
| `PPPOE_NAME` | `XidianPPPoE` | 自建拨号条目名（已有别的拨号连接会直接复用，如「宽带连接」） |
| `PPPOE_USER` / `PPPOE_PASSWORD` | `''` | 留空 = 用上面的账号密码 |
| `MAX_RETRY` / `RETRY_INTERVAL` | `20` / `15` | 重试次数 / 每次间隔秒数（界面版设为 18 / 10） |
| `CLEAR_OTHER_DEVICES` | `True` | 断网后被 err_code=2 挡住时，是否连账号上其它在线设备一起注销；`False` = 只清本机的 |
| `REQUEST_TIMEOUT` | `10` | 单次 HTTP 超时秒数（界面版设为 8） |
| `LOG_FILE` | `None` | 默认不写日志文件；需要留档时自己赋一个路径 |

界面里能直接改的（存进 `settings.json`）：账号、密码、运营商、记住密码、开机自启动、后台运行、
启动后自动连接、断线自动重连及间隔、允许连接 Wi-Fi、允许网线拨号。

### 4.6 把改动更新到 GitHub

```powershell
cd E:\code\Auto-xdwlan
git add -A
git commit -m "说明这次改了什么"
git push            # 开着 Clash 代理即可
```

## 五、注意事项

* **依赖**：`requests`（命令行版）；界面版还需要 `PyQt5`（Anaconda 自带）；打包需要 `pyinstaller`。
  当前打包环境：`D:\Anaconda\envs\paddle_env`（Python 3.9 + PyQt5 5.15.9 + PyInstaller 6.16），
  见 4.3 —— 仓库最初是用 Python 3.7 + PyQt5 5.9.2 + PyInstaller 5.13.2 打的，那套环境本机已经没有。
* **平时不写日志文件**：运行日志只在界面窗口里显示（内存中），退出即清空；
  只有出现「内部异常」时才会在程序同目录追加 `crash.log`（完整 traceback，超 256 KB 自动滚成 `crash.log.old`），
  排查完可以直接删。
* **断网后能自己连上了**（v2.2）：以前拔网线 / 掉线后，账号在 NAS 上残留的会话会让 Portal 一直回
  `err_code=2`，而脚本既不重拨拨号、又因为只看「本机当前 IP」而不去清会话，于是无限重试也上不去。
  现在：先恢复本机链路（重拨 / 连 Wi-Fi）→ 还是被拒就把「在线设备」列表里的旧会话注销掉再重登（见 3.2 / 3.3）。
* **不会再「自己关闭」了**（v2.1）：旧版偶发窗口毫无征兆消失 —— 那是 PyQt5 的默认行为：
  槽函数 / 定时器回调里只要有一个未捕获的 Python 异常，PyQt 就 `qFatal()` → `abort()` 整个进程
  （Windows 错误报告里记的是 `Auto-xdwlan.exe` / `ucrtbase.dll` / `0xc0000409`，事件查看器可见）。
  现在启动就装 `sys.excepthook` 兜底：异常写 `crash.log`、界面给提示，程序继续运行。
* **校园网请求一律直连、不走系统代理**：程序内部对所有请求设了 `trust_env=False` / `proxies=None`，
  所以开着 Clash / VPN 等系统代理（甚至代理已关闭）时，联网认证依然正常 —— 代理只影响 git 推送，不影响本程序。
* **任务可以中止**：连接/检测进行中时「立即连接 / 刷新 / 断开 / 保存配置」会临时置灰（防止任务冲突），
  此时点「停止」即可立刻中止当前任务（最坏情况约 1 分钟自动结束）。
* **正常情况下只会生成 `settings.json`**（出现内部异常时多一个 `crash.log`），里面有账号密码
  （异或 + base64 混淆，**不是加密**），别外发。
* **移动 exe 后要重新勾一次「开机自启动」**（注册表里存的是完整路径）；配置跟着 exe 走，可以整个拷走用。
* **`build_exe.ps1` 必须保持 UTF-8 with BOM**：用不支持 BOM 的编辑器保存后，PowerShell 会报「缺少右 }」。
* **网络 / 代理**：这台电脑的 hosts 把 `github.com` 等屏蔽成了 `127.0.0.1`（改它需要管理员权限），
  所以仓库里配了 `http.proxy = http://127.0.0.1:7890`（Clash）与 `http.schannelCheckRevoke = false`；
  换网络或不再用代理时执行 `git config --local --unset http.proxy` 取消。
* **远程仓库**：<https://github.com/Xiujinhub/Auto-xdwlan>（`settings.json` 已在 `.gitignore` 中，不会上传）。
* **常见报错速查**：
  `E2620` = 账号在线设备数超限（去 `zfw.xidian.edu.cn` 踢设备）；
  `login_error + err_code=2` = 本机/账号已有在线会话（走网线拨号时必然出现）：外网通就无需再认证；
  外网确实不通时，脚本会先把本机链路接回来（重拨 / 连 Wi-Fi），还不行才按「在线设备」列表清旧会话
  （`CLEAR_OTHER_DEVICES=True` 时连账号上其它设备一起注销，日志里会写明动了哪些 IP；也可 `--logout` 手动清）；
  拨号 `623` = 系统已有「所有用户」的拨号本（手动在“设置 → 网络和 Internet → 拨号”里建一条宽带连接，脚本之后会自动复用）。
* 机器上若还跑着第三方校园网客户端（例如 `D:\xdwlan-login\xdwlan-login.exe`），两者不冲突、功能有重叠，
  同时开可能重复登录，但不会互相破坏。
