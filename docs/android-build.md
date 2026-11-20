# Android 端构建说明（2026-11-20 实测）

> 目的：让人在**不装 Android Studio** 的前提下，从零把它编成一个能装到手机上的 APK。
> 所有版本号与路径都是本机实测结果，不是推测；有坑的地方标了「坑」和绕法。

## 0. 这套东西现在是什么状态

- 交付形态（2026-11-20 定）：**Android 原生**，Kotlin + Jetpack Compose。
- 现在只有**第 1 步**：能编译、能装、能跑，界面上有一个「探活后端」按钮，用来证明手机能连到电脑上的服务。
- 业务页面（报告 / 训练单 / 费曼 / 提醒）**还没写**——按"一小步一验证"往下加。

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

## 7. 怎么算这一步做完了（判据）

1. `./gradlew.bat assembleDebug` 输出 `BUILD SUCCESSFUL`，且 `app-debug.apk` 存在。
2. 手机上能看到应用图标，点开显示「Agent 演武场 · 学习伴侣」。
3. 点「探活后端」按钮，在 `adb reverse` 已建立的前提下，显示 `HTTP 200` 与后端返回体。

三条全过，第 1 步才算完成；只过第 1 条就是「能编译但没验过真机」，不算。
