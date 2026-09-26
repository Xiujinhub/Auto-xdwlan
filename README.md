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
└─ .gitignore               提交时忽略 settings.json / dist / build / __pycache__
```

不上传到 Git 仓库的内容：`settings.json`（含账号密码）、`dist\`（47 MB 的 exe）、`build\`、`__pycache__\`。

## 二、软件功能

界面版（`Auto-xdwlan.exe`）：

| 区域 | 功能 |
| --- | --- |
| 连接状态 | 状态灯 + 「已连接 / 未连接 / 未接入网络」；显示上网方式（网线拨号·宽带连接 / 无线 SSID / 有线直连）、Portal 在线账号、外网连通详情；每 60 秒自动刷新 |
| 操作按钮 | 「立即连接」跑完整流程（拨号 → 连 Wi-Fi → Portal 认证）；「刷新」只看状态不动网络；「断开」注销 Portal 并挂断拨号；任务进行中这四个按钮会置灰，可点「停止」中止 |
| 账号信息 | 账号、密码（可点「显示」核对）、运营商（校园网 / 电信 `@dx` / 联通 `@lt` / 移动 `@yd`）、记住密码 |
| 设置 | **开机自启动**（写当前用户注册表，带 `--tray` 静默进托盘，不需要管理员）、**后台运行**（关窗口 = 最小化到托盘，不断网）、启动后自动连接、**断线自动重连**（间隔 1~240 分钟）、允许连接 Wi-Fi / 允许网线拨号 |
| 运行日志 | 滚动显示每一步（连 Wi-Fi、拨号、取 token、提交认证、外网探测结果）；**只在窗口里显示，不写日志文件** |
| 托盘 | 双击/单击唤出主界面；右键菜单：显示主界面 / 立即连接 / 刷新状态 / 退出 |
| 单实例 | 重复双击 exe 不会开第二个进程，而是把已在托盘运行的窗口叫出来 |

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
3. 成功判定：error == 'ok' 或 res == 'ok'（'ip_already_online_error' 也算成功）
4. err_code=2（本机 IP 上有状态异常的旧会话）→ 先调 /cgi-bin/rad_user_dm 踢掉旧会话再重试
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
        若返回「已在线」但外网仍不通：连续两次就停止折腾（避免探测被劫持时死循环）
    检测到旧会话 → clear_stale_session() 踢掉一次再重试
    休眠 RETRY_INTERVAL(15 秒) 后重试
全部失败 → 提示「检查账号密码 / 是否在校园网内」，退出(1)
```

### 3.4 界面（app.py）的任务模型

* 所有网络动作都在 `Worker(QtCore.QThread)` 后台线程里跑，日志通过 `LOG_BUS` 信号回到界面线程，界面不卡。
* 只有三件事：`connect / check / disconnect`（`JOBS`）；同一时刻只跑一个，**后到的请求排队**（`pending_job`），
  前一个做完接着跑 —— 不会出现「启动时的状态检查把自动连接顶掉」。
* 启动节奏：200ms 后 `check`；600ms 后若开了「启动后自动连接」再 `connect`；之后每 60 秒 `check` 一次；
  开了「断线自动重连」则每 N 分钟 `connect` 一次。
* 单实例：用 `QLocalServer` 占坑，第二个实例连上去发条消息，第一个实例把窗口唤到前台，第二个自己退出。
* 界面日志只在内存里（最多 600 行），退出即清空。

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
powershell -ExecutionPolicy Bypass -File E:\code\Auto-xdwlan\build_exe.ps1
```

输出 `dist\Auto-xdwlan.exe`。**打包前先退出正在运行的程序**（脚本会检测，占用时会提示"请先退出程序"）。

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
  当前打包环境：Python 3.7 + PyQt5 5.9.2 + PyInstaller 5.13.2。
* **不写日志文件**：日志只在界面窗口里显示（内存中），退出即清空，磁盘上不会留 `.log`。
* **校园网请求一律直连、不走系统代理**：程序内部对所有请求设了 `trust_env=False` / `proxies=None`，
  所以开着 Clash / VPN 等系统代理（甚至代理已关闭）时，联网认证依然正常 —— 代理只影响 git 推送，不影响本程序。
* **任务可以中止**：连接/检测进行中时「立即连接 / 刷新 / 断开 / 保存配置」会临时置灰（防止任务冲突），
  此时点「停止」即可立刻中止当前任务（最坏情况约 1 分钟自动结束）。
* **`settings.json` 是唯一会生成的文件**，里面有账号密码（异或 + base64 混淆，**不是加密**），别外发。
* **移动 exe 后要重新勾一次「开机自启动」**（注册表里存的是完整路径）；配置跟着 exe 走，可以整个拷走用。
* **`build_exe.ps1` 必须保持 UTF-8 with BOM**：用不支持 BOM 的编辑器保存后，PowerShell 会报「缺少右 }」。
* **网络 / 代理**：这台电脑的 hosts 把 `github.com` 等屏蔽成了 `127.0.0.1`（改它需要管理员权限），
  所以仓库里配了 `http.proxy = http://127.0.0.1:7890`（Clash）与 `http.schannelCheckRevoke = false`；
  换网络或不再用代理时执行 `git config --local --unset http.proxy` 取消。
* **远程仓库**：<https://github.com/Xiujinhub/Auto-xdwlan>（`settings.json` 已在 `.gitignore` 中，不会上传）。
* **常见报错速查**：
  `E2620` = 账号在线设备数超限（去 `zfw.xidian.edu.cn` 踢设备）；
  `login_error + err_code=2` = 本机 IP 有状态异常的旧会话（脚本会自动清理重试，也可 `--logout` 手动清）；
  拨号 `623` = 系统已有「所有用户」的拨号本（手动在“设置 → 网络和 Internet → 拨号”里建一条宽带连接，脚本之后会自动复用）。
* 机器上若还跑着第三方校园网客户端（例如 `D:\xdwlan-login\xdwlan-login.exe`），两者不冲突、功能有重叠，
  同时开可能重复登录，但不会互相破坏。
