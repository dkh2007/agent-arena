# Android 端构建说明（2026-11-21 更新）

> 目的：让人在**不装 Android Studio** 的前提下，从零把它编成一个能装到手机上的 APK。
> 所有版本号与路径都是本机实测结果，不是推测；有坑的地方标了「坑」和绕法。

## 0. 这套东西现在是什么状态

- 交付形态（2026-11-20 定）：**Android 原生**，Kotlin + Jetpack Compose。
- 现在已有**三页**：报告页、训练单页、费曼复述页（数据全部来自 `assets/` 下三个 JSON，离线可渲染）。
  界面上不留任何调试入口——2026-11-20 曾有一张「探活后端」卡（点一下打印 `127.0.0.1:8080`），
  外部评审指出这会让评委翻到最后一屏时看到开发者信息，已删除，探活改到电脑侧用 `curl`。
- 费曼复述页：**只有文字输入**，并且是「先交、锁住、再解锁对照」——提交前页面上只有题目本身，
  提交后才出现期望值、错法解释与自查清单。语音与手写两版做过又删了（2026-11-21），
  理由与恢复条件写在 `docs/android-ui-handoff.md` §4.6。
  截图见 `docs/images/feynman-recall.png`、`feynman-hint-locked.png`、`feynman-commit-dialog.png`、
  `feynman-unlocked.png`、`feynman-checklist.png`、`feynman-hint-open.png`。
- 复习提醒页**还没写**——按"一小步一验证"往下加。

## 1. 环境（本机实测，单位：真的有/真的没有）

| 项 | 结果 | 说明 |
|---|---|---|
| JDK | **有**，`C:\Program Files\Java\jdk-21`（21.0.9） | `JAVA_HOME` 已指向它 |
| Android SDK | **有**（本机新装），`%LOCALAPPDATA%\Android\Sdk` | 路径写在 `android/local.properties`，该文件**不进版本库** |
| 已装 SDK 组件 | `platforms/android-35`、`platforms/android-36`、`build-tools/35.0.1`、`build-tools/36.0.0`、`platform-tools 37.0.3` | |
| Gradle | **有**（本机新装），`%USERPROFILE%\tools\gradle-8.9` | 只用来生成 wrapper，日常用 `gradlew` |
| Android Studio | **没有**，也不需要 | 见第 2 节 |
| Android 模拟器 | **没有** | 用真机，见第 4 节 |

## 2. 为什么不用 Android Studio

Studio 自带 SDK/Gradle/模拟器，但它要 GUI 安装、1.5GB+ 下载、还占 8GB 磁盘。本项目只需要：

```
sdkmanager（装平台与构建工具） + gradle wrapper（编 APK） + adb（装机）
```

这三样都是命令行，装完就能编。代价：没有可视化布局预览、没有图形化断点调试。**改 UI 时靠改代码 + 重装看效果**，对本项目这种「四页 + 两三个接口」的规模够用。

## 3. 版本组合（这是经过验证能一起工作的组合，不要随手升级）

| 组件 | 版本 | 备注 |
|---|---|---|
| Gradle | 8.9 | |
| Android Gradle Plugin | 8.7.3 | |
| Kotlin | 2.0.21 | 与 Compose 编译器插件同版本，必须一致 |
| Compose BOM | 2024.09.00 | 统一管理 compose 各库版本 |
| compileSdk / targetSdk | 35 | |
| minSdk | 26 | Android 8.0+；覆盖绝大多数在用手机 |
| JDK 目标 | 17 | 用 JDK 21 编译、产出 17 字节码 |

## 4. 四个网络坑（都实测过，按这个顺序踩）

1. **`maven.google.com` 不通**（HTTP 000）。所有 `google()` 依赖必须走镜像。
   → `android/settings.gradle.kts` 里把 `maven("https://maven.aliyun.com/repository/google")` 放在 `google()` **前面**。
2. **`services.gradle.org` SSL 失败**（curl exit 35），但 `downloads.gradle.org` 会 307 跳转。
   → wrapper 的 `distributionUrl` 用 `https://mirrors.cloud.tencent.com/gradle/gradle-8.9-bin.zip`。
3. **坑：wrapper 的 Java 下载会被代理截断**，下回来是 987 字节的 HTML，报 `zip END header not found`。
   → 用 `curl` 手动下一个完整包（130MB），放进
   `%USERPROFILE%\.gradle\wrapper\dists\gradle-8.9-bin\<hash>\gradle-8.9-bin.zip`，再跑 `gradlew`。
   校验：`unzip -t gradle-8.9-bin.zip` 无报错。
4. **坑：`sdkmanager.bat` 的包名里带分号会被 cmd 当命令分隔符切开**（报 `Package platforms not found`）。
   → 改用新的原生 CLI：`cmdline-tools/latest/bin/android.exe sdk install "platforms/android-35"`（用斜杠，不用分号）。

## 5. 从零到 APK 的四条命令

```bash
# ① 装 SDK 组件（只需一次）
SDK="$LOCALAPPDATA/Android/Sdk"
"$SDK/cmdline-tools/latest/bin/android.exe" sdk install "platforms/android-35" "build-tools/35.0.1"

# ② 写 local.properties（只需一次，不进版本库）
#    sdk.dir=<你的 SDK 绝对路径；Windows 下反斜杠要写成双反斜杠>

# ③ 编译
cd android && ./gradlew.bat assembleDebug

# ④ 产物
#    android/app/build/outputs/apk/debug/app-debug.apk
```

实测：首次 2 分 36 秒（含下载依赖），产物 **9.1 MB**。

## 6. 装到手机上（两种，任选）

**A. 数据线 + adb（推荐，能顺带解决连后端）**

```bash
adb install -r android/app/build/outputs/apk/debug/app-debug.apk
adb reverse tcp:8080 tcp:8080      # 手机访问 127.0.0.1:8080 就转到电脑
```

`adb reverse` 的好处：**不需要手机和电脑在同一 WiFi**，也不用改后端监听地址，一根线就够了。拔线后失效。

**B. 直接把 APK 传到手机装**（微信/QQ 传文件、网盘、数据线拷都行）
- 需要手机允许「安装未知来源应用」。
- 这种装法**连不上后端**（除非手机和电脑同一个局域网且后端绑了 `0.0.0.0`），演示时用「离线样例」模式。

## 6.5 荣耀手机 USB 调试的实测记录（2026-11-20）

设备：荣耀 400 Pro（VID `339B`）。开启「USB 调试」后，设备管理器里的复合设备变成三个接口：

| 接口 | 类/子类/协议 | 驱动 | 说明 |
|---|---|---|---|
| MI_00 | MTP | WUDFWpdMtp | 文件传输，正常 |
| MI_01 | `ff / 42 / 01` | WINUSB | **ADB Interface**，这正是 adb 要的接口 |
| MI_02 | `ff / 48 / 01` | 缺驱动（Error） | HDB，HiSuite 通道，与 adb 无关 |

**结果：`adb devices` 仍然是空列表。** platform-tools 37.0.3 与 33.0.3 都试过，都不认。

**已排除**：数据线（MTP 正常）、USB 调试开关（ADB 接口已出现）、adb 版本（新旧都试）、`kill-server` 重启、`reconnect offline`。

**判断**（未逐条验证，标为待确认）：MI_01 绑的是 Windows 自带的通用 `WinUsb Device` 驱动，它只注册 WinUSB 自己的设备接口 GUID；adb 枚举时按 USB 设备接口 GUID 找节点，通用驱动不注册这个 GUID，所以 adb 看不见。Google 的 `android_winusb.inf` 会额外声明该 GUID，因此官方驱动下能用。

**如果要走 USB，两条路**（都需要管理员权限，尚未执行）：

1. 给 MI_01 的 `Device Parameters\DeviceInterfaceGUIDs` 追加 `{A5DCBF10-6530-11D2-901F-00C04FB951ED}`，然后拔插一次；
2. 或用 `android_winusb.inf` 加一行 `VID_339B&PID_107D&MI_01` 后安装（要处理驱动签名）。

**当前采用的绕法：无线调试**（`adb pair` + `adb connect`），完全不依赖 USB 驱动。

## 6.6 模拟器（开发期首选，2026-11-20 实测可用）

真机只用于最终验收；界面开发与截图回归一律走模拟器，理由是能自己截图看、能反复装、不依赖 USB 驱动。

**前置条件（本机已满足）**：Hyper-V 在运行（`HypervisorPresent=True`，存在 `C:\Windows\System32\WinHvPlatform.dll`）⇒ 可用 WHPX 硬件加速。

**装（注意锁坑）**：

```bash
SDK="$LOCALAPPDATA/Android/Sdk"
rm -f "$HOME/.android/cli/bundles/lock"     # 每次安装前必删，否则报 os error 5
"$SDK/cmdline-tools/latest/bin/android.exe" sdk install "emulator"
rm -f "$HOME/.android/cli/bundles/lock"
"$SDK/cmdline-tools/latest/bin/android.exe" sdk install "system-images/android-35/google_apis/x86_64"
```

`android.exe sdk install` **每装完一个包就留一个未释放的 `lock` 文件**，下一次安装直接 `拒绝访问 (os error 5)`。这不是权限问题，删掉即可。

**建与启动**：

```bash
echo no | "$SDK/cmdline-tools/latest/bin/avdmanager.bat" create avd \
  -n arena35 -k "system-images;android-35;google_apis;x86_64" -d pixel_6 --force

"$SDK/emulator/emulator.exe" -avd arena35 -no-window -no-audio -no-boot-anim \
  -no-snapshot -gpu swiftshader_indirect -accel on
```

实测冷启动 58 秒。`-no-window` 不弹窗，不干扰桌面。

**装包 + 截图（必须一条命令内做完）**：

```bash
ADB="$SDK/platform-tools/adb.exe"
"$ADB" start-server
"$ADB" -s emulator-5554 install -r app/build/outputs/apk/debug/app-debug.apk
"$ADB" -s emulator-5554 shell am start -n com.arena.companion/.MainActivity
sleep 6
"$ADB" -s emulator-5554 exec-out screencap -p > /tmp/shot.png
```

两个坑：

1. **adb 服务会随 shell 退出被杀**，下一次调用要重新 `start-server`；期间设备会短暂显示 `offline`，所以要轮询 `get-state` 到 `device` 再操作。
2. **`/sdcard/...` 会被 Git Bash 改写成 Windows 路径**，别用 `screencap` + `pull`，用 `exec-out` 直接重定向。
3. 截图后 6 秒内可能还停在启动图（Android 12+ 的 splash），第二次截才拿到真实界面。

当前报告页截图存档：`docs/images/android-report-page.png`。

## 6.7 模拟器把应用冻结：所有 `tap` 无响应（2026-11-21 实测）

**症状**：应用在前台、界面正常、`input swipe` 能滚动页面，但 `input tap` 全部没反应——
按钮不开菜单、`BACK` 也不回上一页。`logcat` 里只有一行
`IPCThreadState: Sending oneway calls to frozen process`，没有 ANR。

**原因**：Android 11+ 的 cached app freezer 把这个进程冻住了。自动化（uiautomator）能读到界面，
触摸事件却送不进被冻结的进程。

**处置**（一次性写进系统设置，重启模拟器后仍有效）：

```bash
adb -s emulator-5554 shell settings put global cached_apps_freezer disabled
adb -s emulator-5554 shell settings get global cached_apps_freezer   # 应输出 disabled
```

**顺带的两条操作经验**：

1. 每次 `force-stop` + `am start` 后应用要 30–60 秒才渲染完（debug 包 + 模拟器无 GPU 加速），
   这期间截图只拍到启动图。不要据此判断"崩了"。
2. 取点击坐标别拿截图比例换算——用 `uiautomator dump` 读 `bounds` 再点，最稳：

```bash
adb shell "uiautomator dump /sdcard/u.xml" && adb shell "cat /sdcard/u.xml" > u.xml
# 在里面找 text="复述这题" 的 bounds="[x1,y1][x2,y2]"，点中心
```

## 7. 怎么算这一步做完了（判据）

1. `./gradlew.bat assembleDebug` 输出 `BUILD SUCCESSFUL`，且 `app-debug.apk` 存在。
2. **模拟器**上装得上、打得开，报告页渲染出真实字段（得分率 0.25、有效得分 1/权重合计 4、2 个技能缺口、5 条断言）。截图见 `docs/images/android-report-page.png`。
3. 三页都能进：报告 →「下一步」两个按钮分别进训练单 / 费曼复述；费曼页能走完「提交前（对照锁着）→ 提交确认 → 提交后（清单解锁）」三步，提示弹层在提交前只有一条、提交后四条。存档见 `docs/images/feynman-*.png`。
4. **真机**（荣耀 400 Pro）能装上、点开不崩 —— 这一步与第 3 条分开：界面正确性由模拟器证明，安装可行性由真机证明。
5. 可选（**命令行为准**）：电脑侧 `adb reverse tcp:8080 tcp:8080` 后 `curl http://127.0.0.1:8080/healthz` 返回 200（离线演示不依赖它）。App 内不设探活按钮。

状态（2026-11-21）：第 1、2、3 条已过（模拟器 arena35，实拍见 `docs/images/`）；
第 4 条因荣耀 WinUSB 驱动问题暂无法用 adb 装，改用「直接传 APK 手动安装」验收；
第 5 条待后端联调时验。语音与手写已删，不再有待验项。
