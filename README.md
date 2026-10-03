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
| 状态区（顶部） | 状态灯 + 「已连接 / 未连接 / 未接入网络」，副标题写「外网通畅 · 出口 IP」；下面 2×2 显示上网方式、无线网络、在线账号、外网连通（**完整信息在鼠标悬停提示里**，所以不必占地方）；每 60 秒自动刷新 |
| 操作按钮 | 右上角「立即连接」跑完整流程（拨号 → 连 Wi-Fi → Portal 认证）、「刷新」只看状态不动网络；状态区右下「断开」注销 Portal 并挂断拨号、「停止」中止任务。任务进行中除「停止」外都会置灰 |
| 账号 | 一行放账号 + 密码（可点「显示」核对）；第二行放运营商（校园网 / 电信 `@dx` / 联通 `@lt` / 移动 `@yd`）+ 记住密码 |
| 设置 | 三行短标签（详细说明看鼠标悬停）：**开机自启动**（写当前用户注册表，带 `--tray` 静默进托盘，不需要管理员）、**后台运行**（关窗口 = 最小化到托盘，不断网）、启动后自动连接、允许连接 Wi-Fi、允许网线拨号、**断线自动重连**（间隔 1~240 分钟）；不常用的两项收在「**▸ 高级**」里（Portal 被拒时试一次拨号 / 会话冲突时清旧会话） |
| 运行日志 | 滚动显示每一步（连 Wi-Fi、拨号、取 token、提交认证、外网探测结果）；右上「清空」、可点「收起 / 展开」把日志折叠起来让窗口更紧凑；**只在窗口里显示**，平时不写日志文件（只有内部异常才追加 `crash.log`） |
| 托盘 | 双击/单击唤出主界面；右键菜单：显示主界面 / 立即连接 / 刷新状态 / 退出 |
| 单实例 | 重复双击 exe 不会开第二个进程（第二个进程**会立刻退出**，这是正常的），而是把已在托盘运行的窗口叫出来 |

> 界面布局（v2.9 重构）：原来是「连接状态 / 账号信息 / 设置 / 运行日志」四张卡竖着排、账号和设置各占一大块，
> 很容易顶到屏幕边上还要滚动。现在合成 **状态（含操作）+ 账号 + 设置** 三块 + 可折叠日志：
> 状态的四行详情压成 2×2、主操作移到右上角、设置改成三行短标签（细节进 tooltip）、
> 「高级」两项和运行日志都能收起 —— 窗口默认 470×690，一般不用滚动。
> （**v2.10**：又按「控件文字 vs 实际宽度」逐个核对了一遍 —— 窗口加宽到 500×700，按钮/输入框按文字留够宽度，
> 去掉会被截断的占位提示（账号框只留「学号」、密码框「密码」，说明放 tooltip），重连间隔的「分钟」改成独立标签，
> 状态值和长文案允许换行 —— 三种状态（已连接 / 未连接 / 未接入网络）审计下来 0 处显示不全。）

命令行版（`autoconn.py`）能力：状态自检、强制登录、注销、只连 Wi-Fi、只拨号、断开拨号、详细模式；
退出码 `0` = 已能上网，`1` = 失败，方便被别的脚本/计划任务调用。

## 三、软件逻辑

### 3.1 三条上网链路（按顺序自动选，前面能上网就不动后面的）

1. **网线直连**：有线网卡拿到正常 IPv4 → 直接走 Portal 认证。
2. **网线拨号（PPPoE）**：插着网线却只有 `169.254.x.x` → `rasdial` 拨号。
   系统里已有拨号连接（如手工建的「宽带连接」）就**直接复用**；一条都没有时才按 `rasphone.pbk`
   格式自建 `XidianPPPoE`（写在当前用户拨号本，不需要管理员）。拨通一般就能上网，通常无需 Portal。
3. **无线**：无线那条只在「Portal 不可达」或者「网线这条试过一轮还是上不去」时才用 ——
   `netsh wlan connect stu-xdwlan`（开放式网络，系统缺配置文件时自动生成）
   → 等网关就绪 → 走 Portal 认证。
   说明（v2.4）：以前无线只在 Portal 不可达时才连，于是网线插着、但这个墙口根本用不了
   （围墙花园能打开门户页 = Portal 可达）时会一直死磕网线、**永远不试 Wi-Fi**；
   现在只要「有网线也有可用 IP」这条试过一轮仍上不去，就会改用 Wi-Fi 再试。
   另外连 Wi-Fi 不再要求"这次扫描必须列出该 SSID"（`netsh wlan show networks` 结果带缓存，
   接口刚动过时经常短暂为空，以前会因此直接放弃连接）。

判断「能不能上网」的硬标准：轮流探测**多个互相独立的地址**（不同厂商、http/https 都有，
见 `autoconn.py` 的 `INTERNET_PROBES`），任何一个通过就算联网：

| 探测地址 | 期望 | 说明 |
| --- | --- | --- |
| `http://connect.rom.miui.com/generate_204` | 204 | 最快，优先试 |
| `https://connect.rom.miui.com/generate_204` | 204 | https 不会被校园网往响应里插东西 |
| `https://connectivitycheck.platform.hicloud.com/generate_204` | 204 | 华为 |
| `https://detectportal.firefox.com/success.txt` | 200 | Mozilla |
| `http://www.baidu.com/` | 200 | 它会 302 跳到 https，**同站跳转算通** |

未登录时校园网会把请求劫持到 Portal 页（还会往 HTTP 响应里插提示），所以光看状态码不够：
204 类要求响应体为空、200 类要求响应体里没有 `srun` / `xidian`。
一轮探测有 12 秒总预算（`PROBE_BUDGET`，单条最多 5 秒），离线时不会卡很久。

> 以前这里只有 miui + 百度两条，而百度的 http 探测早就废了（现在返回 302 跳到 https，
> 脚本不跟跳转 → 永远失败），等于全靠 miui 一条撑着；那条一抖就被判成「断网」，
> 接着把用户正在用的拨号拆掉重拨 —— 这正是「连着代理时网络老是断一下」的来源，v2.3 修掉。

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
    外网不通时：无线没连在 WIFI_SSID 上、而扫描结果里确实有它 → 先换上 Wi-Fi 再看能不能上网
                （v2.5 新增：以前只有「Portal 不可达」那一支才会连 Wi-Fi，于是「连在别的网上、
                  Portal 又可达」时脚本永远不会去连校园网）；扫不到校园网（在家的场景）不动无线
    Portal 不可达时：
        先试一次网线拨号（PPPoE）→ 通了就退出
        还不可达 → 连 Wi-Fi(stu-xdwlan) + 等网关就绪（最多 30 秒）
    网线这条（有网线、也有可用 IP）试过一轮仍上不去 → 改用 Wi-Fi(stu-xdwlan) 再试（v2.4 新增，
        墙口坏了 / 只给拨号不给直连时，真正能用的就是无线）；
        v2.6：出口 IP 仍在有线、且门户按 IP 能应答时不再去连无线（账号单会话策略下必然被拒，
        而且 Windows 的流量还是走有线 —— 会白折腾），直接把「要用无线就拔网线 / 禁用有线网卡」写进日志
    提交 login()：成功则等 2 秒再验外网 → 通就退出
        若返回 err_code=2 / ip_already_online（本机或账号已有在线会话）：
            外网已通 → 直接按成功算（网线拨号 PPPoE 在线时必然遇到，不用重复认证）
            外网不通 → 链路本来就在（无线连在校园网 / 拨号在线 / 有线有可用 IP）时直接清旧会话重登：
                       复用 login() 里刚探过的外网结果、跳过一遍「恢复链路」（v2.5：这两步原来各要
                       十几秒，表现就是「日志半天不动、只能手动去浏览器登录」）
                       链路确实断了才先恢复本机链路（重拨 / 连 Wi-Fi，一次）—— 断网测试最常见的就是这种：
                       Portal 哪都能到，可拨号已经掉线，光重试 Portal 认证永远登不上去
                       但**拨号还连着时先别急着拆**：按 IP 直连门户（不依赖 DNS）看看校园网这一段
                       还活着吗 —— 活着说明只是外网/DNS/CDN 抽风，这次不重拨、也不注销会话；
                       探测不到才算链路真出问题，才允许拆掉重拨
            还是被拒 → v2.6 起默认（CLEAR_SESSIONS=False）**不注销任何会话**：只把「账号被谁占着」
                       写进日志（_report_session_conflict()），然后按间隔重试、等 NAS 自己释放；
                       有线在手时额外试一次网线拨号（PPPOE_FALLBACK，同一个账号换个认证通道，
                       失败原因原样进日志）。设 CLEAR_SESSIONS=True 才走老路：
                       clear_stale_sessions() 按「在线设备」列表注销旧会话，随即重登（不必等间隔）
                       （拨号在 + 校园网链路活着 时同样跳过：此刻注销会把正在用的拨号会话踢断）
        若返回「已在线」但外网仍不通：连续两次就停止折腾（避免探测被劫持时死循环）
    休眠 RETRY_INTERVAL(15 秒) 后重试
全部失败 → 提示「检查账号密码 / 是否在校园网内」，退出(1)
```

`clear_stale_sessions()` 清旧会话的顺序（只在 `CLEAR_SESSIONS = True` 时才会被调用）：

1. 先清「看起来是本机」的会话：网卡 IP 对得上，或 OS 名 / 客户端名对得上（`_looks_like_ours()`）；
2. 剩下的是账号上其它设备的会话，只有 `CLEAR_OTHER_DEVICES = True` 时才一起注销（v2.6 起默认 **False**，
   不再动别人的设备；打开时日志里会写明动了哪些 IP）。

> v2.6 实测结论（2026-09-30，本机同时插网线 + 连 stu-xdwlan）：
> * 同一个账号**同一时刻只允许一条在线会话** —— 有线直连 / 网线拨号 / 无线共用这套账号。
>   本机有线那条在线时，从无线发起的认证必然被拒（`err_code=2`）；
> * 用 `rad_user_dm` 把会话踢掉后，门户的「在线设备」列表立刻变空，但 **NAS 上的计费会话还要几分钟
>   才释放**，这期间**所有**登录（含本机重新登录）都回 `err_code=2`；
> * 所以「先清会话再重登」经常不是救命，而是把本来能用的一条链路弄断好几分钟 —— 这就是
>   日志里「清完旧会话还是 err_code=2、连续重试全失败」的由来。因此 v2.6 把它改成默认不做。

再补一句 `campus_link_alive()`（v2.3 新增）：外网探测失败时，先按**上次解析到的门户 IP** 直连
`https://<IP>/cgi-bin/get_challenge`（门户 80 端口不通、443 可以，所以只能 https + 不校验证书）。
它不看外网、也不依赖 DNS，专门回答「校园网这一段还活着吗」——活着就别拆拨号、别注销会话，
只安静重试；只有它也探测不到，才认为链路真出问题了。

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
cd E:\code\Auto-xdwlan
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1 -Python D:\Anaconda\python.exe
Copy-Item .\dist\Auto-xdwlan.exe D:\Auto-xdwlan\ -Force      # 部署目录（exe 和 settings.json 都在这儿）
```

输出 `dist\Auto-xdwlan.exe`。**打包前先退出正在运行的程序**（脚本会检测，占用时会提示"请先退出程序"）。
打包会把 `dist\` 整个重建，但脚本会自动把 `dist\settings.json` 备份到临时目录、打完再还原，
所以界面里填过的账号密码不会丢。

本机打包环境（2026-09-30 实测）：直接用 `D:\Anaconda\python.exe`（Python 3.12.4 + PyQt5 5.15.10），
缺 PyInstaller 时装一次即可：`python -m pip install pyinstaller`（本次装的是 6.22.3，打出来 ~57 MB）。
以前用的 `D:\Anaconda\envs\paddle_env`（Python 3.9 + PyQt5 5.15.9 + PyInstaller 6.16，~40.8 MB）在本机已经不存在，
`envs\tracker`（Python 3.7，和最早写的 3.7 + PyQt5 5.9.2 最接近）没有 PyQt5，打不了。
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
| `PPPOE_FALLBACK` | `True` | Portal 被拒（err_code=2）时，是否再用同一个账号试一次网线拨号 |
| `CONFLICT_WAIT` | `300` | 被「已有在线会话」挡住时，等 NAS 释放旧会话的等待窗口（秒）；期间每 `RETRY_INTERVAL` 秒重试一次，**不注销任何会话**，点「停止」可随时中止 |
| `CLEAR_SESSIONS` | `False` | 被 err_code=2 挡住时是否主动注销挡路的旧会话（v2.6 起默认不做：踢掉后 NAS 要几分钟才释放，期间连本机都登不上） |
| `CLEAR_OTHER_DEVICES` | `False` | `CLEAR_SESSIONS=True` 时，是否连账号上其它在线设备一起注销；`False` = 只清本机的 |
| `PROBE_TIMEOUT` / `PROBE_BUDGET` | `5` / `12` | 单个探测地址的超时 / 一轮探测的总预算（秒） |
| `REQUEST_TIMEOUT` | `10` | 单次 HTTP 超时秒数（界面版设为 8） |
| `LOG_FILE` | `None` | 默认不写日志文件；需要留档时自己赋一个路径 |

界面里能直接改的（存进 `settings.json`）：账号、密码、运营商、记住密码、开机自启动、后台运行、
启动后自动连接、断线自动重连及间隔、允许连接 Wi-Fi、允许网线拨号、
Portal 被拒时试一次网线拨号、会话冲突时清掉旧会话（后两项 v2.6 新增，后者默认不勾）。

### 4.6 把改动更新到 GitHub

```powershell
cd E:\code\Auto-xdwlan
git add -A
git commit -m "说明这次改了什么"
git push            # 开着 Watt Toolkit / Clash 即可
git tag v2.5        # 发版时打个轻量 tag（历史 tag 都是轻量的）
git push origin v2.5
```

本机在这台电脑上推送要先关掉证书**吊销检查**（github 流量被 Watt Toolkit 的本地证书拦截，
schannel 拿不到 CRL 会直接失败）。`E:\code\Auto-xdwlan` 这个仓库已经配好了：

```powershell
git config --local http.sslBackend schannel        # 用 Windows 证书库（已信任 Watt Toolkit 的 CA）
git config --local http.schannelCheckRevoke false  # 不查吊销
```

报 `SSL certificate problem: unable to get local issuer certificate` 就是上面这两项没配
（Git 自带的 OpenSSL 根证书列表里没有 Watt Toolkit 的 CA）。要是这台机器上还没有 `.git`
（例如从 GitHub 下的 zip），重建成本地仓库：

```powershell
git init -b main
git remote add origin https://github.com/Xiujinhub/Auto-xdwlan.git
git config --local http.sslBackend schannel; git config --local http.schannelCheckRevoke false
git fetch origin; git reset origin/main      # 用远端 HEAD 对齐历史，工作区文件保持不动
```

发布带 exe 的 Release：exe 不进仓库（`dist/` 已 gitignore，且仓库文件 >50 MB 会告警），
走 Release 附件上传（API：`POST /repos/:owner/:repo/releases` 再
`POST https://uploads.github.com/repos/:owner/:repo/releases/:id/assets?name=Auto-xdwlan.exe`）。

## 五、注意事项

* **当前版本 v2.11**（2026-10-01）：联网行为明确成「完全照手动操作」—— 能上外网就不动 → 插着网线就拨号
  （网线这条路不通就换 Wi-Fi）→ 最后才做校园网认证；**默认不做任何「清 IP / 踢会话」动作**
  （要用「▸ 高级」里的开关才会清，默认关闭）。本次发布把版本号从 v2.10 提到 v2.11
  并用本机环境重新构建 exe（连网逻辑与 v2.10 相同），保证 `tag` / 源码 / `exe` 三者严格对应。
* **依赖**：`requests`（命令行版）；界面版还需要 `PyQt5`（Anaconda 自带）；打包需要 `pyinstaller`。
  **v2.11 的 exe 用的是这台机器（`E:\code`）的打包环境**：`D:\Anaconda\python.exe`
  = Python 3.7.0 + PyQt5 5.9.2 + PyInstaller 5.13.2 + requests 2.19.1（见 4.3）。
  另一台机器上用的是 `D:\Anaconda\envs\paddle_env`（Python 3.9 + PyQt5 5.15.9 + PyInstaller 6.16，打出来更小），
  但那套环境在本机不存在；两边源码完全相同，exe 体积差异只来自打包环境。
* **平时不写日志文件**：运行日志只在界面窗口里显示（内存中），退出即清空；
  只有出现「内部异常」时才会在程序同目录追加 `crash.log`（完整 traceback，超 256 KB 自动滚成 `crash.log.old`），
  排查完可以直接删。
* **自动连接不再「卡着不动」**（v2.5）：两处改动 ——
  ① 「账号已有在线会话」（`err_code=2`）时，复用刚探过的外网结果、链路本来就在就跳过一遍「恢复链路」，
     直接注销挡路的旧会话再重登，比原来快十几秒（原来要先整轮探外网、再做一遍恢复链路，才轮到清会话）；
  ② 外网不通时也会主动确认无线连的是不是校园网（见 3.3）；「无线超时连不上、而且当前什么都没连着」
     时先断开再重连一次（Realtek 网卡偶发「关联时被驱动程序断开」，重连一次通常就好），
     本来连着别的网络时不会去拆它。
  另外每次连接开始时日志会先写一行「本机当前链路：有线=…；无线=…；拨号=…；出口IP=…」
  （v2.6 起带上出口 IP），一眼就能看出到底走的是哪条链路、是「没连上」还是「连上了但认证被拒」。
* **同时插着网线 + 连着无线时，无线其实是「挂着不用」的，认证也上不去**（v2.6 实测结论）：
  Windows 按网卡跃点选路（有线 25 < 无线 45），流量和 Portal 认证走的都是有线那条；
  而账号只允许一条在线会话 → 日志里的「无线=stu-xdwlan」只是关联成功，认证会被拒（`err_code=2`）。
  连接开始时那句「注意：本机同时插着网线、又连着 stu-xdwlan…」就是在说这件事。
  **想让无线真正上网：拔掉网线，或在「网络连接 → 适配器选项」里禁用有线网卡**；
  只想用有线就什么都不用做。另外，账号被别的设备（手机 / 另一台电脑）占着时，
  本机无论走哪条链路都会被拒 —— 这时工具只报状态、不再去踢别人的设备，
  等那台设备断开或 NAS 自己释放（几分钟）就会自动恢复。
* **拨号刚连上又被踢断**（v2.8，实测）：RasMan 日志里出现过「21:05:54 建立成功 → 21:06:05 断开（11 秒）」
  「20:37:27 建立成功 → 20:37:42 断开（15 秒）」。原因是同一账号只允许一条在线会话：拨号刚起来时
  若同时在走 Portal 认证，NAS 就会把拨号那条踢掉。v2.8 起：检测到拨号已连接 → 先给 `DIAL_GRACE`
  （默认 20 秒）让链路跑起来，这期间**不做 Portal 认证**；还是不通就明确断开拨号、改走 Portal，
  两边不再互相抢同一个账号。想稳定用拨号，就别让别的设备/程序同时登这个账号。

## 六、本机修复记录（2026-10-03，v2.12 / v2.13）

* **自动连接改成「照人工操作」的顺序**（v2.13）：人工最省事的就是「插着网线 → 打开系统里那条
  「宽带连接」拨号，只填账号密码，不看 IP、也不碰 Portal 认证」。现在 `auto_connect()` 的顺序是：
  **能上外网就不动 → 插着网线就先拨号 → 拨号不通 / 没插网线才连 Wi-Fi → 最后才做 Portal 认证**。
  以前是「先 Portal 认证，被 `err_code=2` 挡住时才轮到你拨号」，而那个账号冲突只会拖住 Portal，
  拨号往往能直接成功 —— 看起来就像「软件不肯照我手动的方式连」。
  想退回旧顺序：`autoconn.PREFER_DIAL = False`。
* **被 `err_code=2` 挡住时的处理**（v2.12）：先打印「账号被谁占着」→ 试拨号（最多 3 次、间隔 45 秒）→
  再等 NAS 释放（`CONFLICT_WAIT`，默认 300 秒，随时可点「停止」）；默认**不注销任何会话**、
  也不动别人的设备；同时不再白等 28~45 秒去连无线（会打印「跳过连无线」）。
* **版本对应**：`v2.13` 的源码 / tag / Release 附件 `Auto-xdwlan.exe` 严格一致；
  本次 exe 用 `D:\Anaconda\envs\paddle_env`（Python 3.9 + PyQt5 5.15.9 + PyInstaller 6.16）打包，约 40.9 MB
  （v2.11 那份 47.9 MB 只是打包环境不同，逻辑以本次为准）。
* 涉及文件：`autoconn.py`（连接顺序与冲突处理）、`app.py`（版本号 2.13）。
* **v2.14（2026-10-03）：彻底不做「踢设备」，并把原因说清楚。**
  实测遇到「僵尸会话」：某个 IP 上的会话挂几小时也不回收（本次是 `10.192.111.213` 从 03:55
  一直在线），Portal 就永远回 `err_code=2`，**但拨号照样能成**（拨号是另一套认证通道）。
  所以：`clear_sessions` 默认关（本机 settings.json 也改成 false）、日志里不再出现「踢 IP」的建议，
  而是写清「会话在谁那儿、已在线多久」，并给出三条出路（插网线让程序拨号 / 到那台设备退出 / 等 NAS 回收），
  同时继续按间隔自动重试。

* **「手动能连、自动连不上」的真相**（v2.7）：账号上还挂着旧会话时，Portal 一直回 `err_code=2`，
  **NAS 真正释放它要几分钟**（实测 2~10 分钟）。手动在浏览器里点「登录」能成功，多半只是
  「多按了几次、等到了那一刻」—— 网页版在收到 `ip_already_online_error` 时也只是
  `logout()` 完再 `login()`（Portal.js 的 `reAuth()`），并不会让 NAS 立刻放行。
  但界面版的默认重试是 8 次 × 8 秒 ≈ 1 分钟就放弃，**比 NAS 释放得还早**，于是看起来就是
  「手动行、自动不行」。v2.7 起：命中这种情况会进入 `CONFLICT_WAIT`（默认 300 秒）等待窗口，
  每 `RETRY_INTERVAL` 秒探一次外网 + 试一次登录，**全程不注销、不踢任何会话**（踢了自己也要
  跟着掉线几分钟，反而更慢），期间点「停止」可随时中止。
  想手动复现网页那套「注销后再登录」，可以跑 `python autoconn.py --reauth`（会让本机掉线几秒到几分钟，慎用）。
* **Wi-Fi 开关被关掉时会直接说清楚**（v2.6）：Windows 的 Wi-Fi 开关（或笔记本无线快捷键 / 飞行模式）
  把无线**软件**关掉后，`netsh wlan connect` 会直接报 **0x80342002**、扫描结果变空
  （`netsh wlan show interfaces` 显示「无线电状态：硬件 开 / 软件 关」）。以前日志只会说
  「连接 stu-xdwlan 超时」，很容易误判成信号问题；现在会直接提示「无线网卡被系统关掉了 → 去打开 Wi-Fi 开关」。
* **账号填错一位会先提醒**（v2.6）：西电学号是 11 位数字，不是 11 位时连接日志第一行就会提醒
  （实测过一次少写一位：Portal 对错账号也回含糊的 `err_code=2`，看起来特别像"会话冲突"，白查半天）。
* **没网线 / 网线用不了时会自动改用 Wi-Fi**（v2.4）：以前无线只在 Portal 不可达时才连，
  网线插着（哪怕这个墙口只给拨号、根本做不了直连认证）时 Portal 照样可达 → 脚本一直死磕网线。
  现在「有网线也有可用 IP」这条路试过一轮仍上不去，就会去连 `WIFI_SSID`（默认 `stu-xdwlan`）再认证；
  连 Wi-Fi 也不再要求"这次扫描必须能看到该 SSID"（扫描结果是缓存的，经常短暂为空）。
  想让脚本完全不碰 Wi-Fi：清空 `WIFI_SSID` 或取消界面里的「允许连接 Wi-Fi」。
* **别把正在用的连接拆了**（v2.3）：外网探测失败 ≠ 链路坏了。探测地址现在有 5 个（互相独立，
  http/https 都有，其中百度的 http 探测以前一直是废的、等于全靠 miui 一条），
  而且拨号还连着时会先 `campus_link_alive()` 按 IP 直连门户确认「校园网这段还活着吗」：
  活着就只安静重试，不重拨拨号、也不注销会话。**断线自动重连的间隔别设太短**
  （界面里的「分钟」，之前设成 2 分钟会让这类抽风反复触发，建议 5 ~ 10 分钟）。
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
  `login_error + err_code=2` = 账号已有一条在线会话（西电同一账号同一时刻只允许一条：有线直连 /
  网线拨号 / 无线共用同一套账号）。外网通就无需再认证；外网确实不通时，v2.6 会先把「账号被谁占着」
  写进日志（`_report_session_conflict()`）再按间隔重试、等 NAS 自己释放（刚踢过会话要几分钟），
  有线在手时额外试一次网线拨号；确需主动清会话时才把 `CLEAR_SESSIONS` 设为 True
  （也可 `--logout` 手动清）。注意：同一 IP 上通常挂着 3 条会话记录，`rad_user_dm` 按 IP 一次清干净；
  拨号 `623` = 系统已有「所有用户」的拨号本（手动在“设置 → 网络和 Internet → 拨号”里建一条宽带连接，脚本之后会自动复用）。
* 机器上若还跑着第三方校园网客户端（例如 `D:\xdwlan-login\xdwlan-login.exe`），两者不冲突、功能有重叠，
  同时开可能重复登录，但不会互相破坏。
