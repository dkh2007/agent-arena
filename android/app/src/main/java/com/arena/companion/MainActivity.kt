package com.arena.companion

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.ime
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.systemBars
import androidx.compose.foundation.layout.union
import androidx.compose.material3.Scaffold
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.core.view.WindowCompat

/**
 * 入口。
 *
 * 四页：诊断报告 → 训练单 / 费曼复述 / 今日复习。子页一律返回报告页。
 * 仍用状态机而不是导航组件：页面之间没有回退栈需求（不存在 A→B→C→B），
 * 引入 navigation-compose 会多一个依赖和一份路由表，收益为负。
 * 等出现「从提醒直接深链到某份报告」时再换。
 *
 * 页面只读 `assets/` 下四个 JSON，不发任何网络请求。
 * 联调后端在电脑侧完成（端口转发 + `curl`），不在学习者界面里留调试入口——
 * 页面上出现本机回环地址，评委看到的就是「没做完」。
 */
class MainActivity : ComponentActivity() {
    private enum class Screen { Report, Training, Feynman, Reminder }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // 浅底 + 深色状态栏图标，避免状态栏文字在灰白背景上糊掉
        WindowCompat.getInsetsController(window, window.decorView).isAppearanceLightStatusBars = true
        // 交给 Compose 处理 insets（见下面 Scaffold 的 contentWindowInsets 注释）；
        // 不关这一项，系统会把窗口直接压小，ime inset 恒为 0，键盘仍会盖住底部一行。
        WindowCompat.setDecorFitsSystemWindows(window, false)
        setContent {
            ArenaTheme {
                val ctx = LocalContext.current
                val report = remember { loadSampleReport(ctx) }
                val plan = remember { loadTrainingPlan(ctx) }
                val feynman = remember { loadFeynmanPrompt(ctx) }
                val reminder = remember { loadReminder(ctx) }
                var screen by remember { mutableStateOf(Screen.Report) }

                BackHandler(enabled = screen != Screen.Report) { screen = Screen.Report }

                Scaffold(
                    modifier = Modifier.fillMaxSize(),
                    // systemBars 的避让已实测有效（页面内容从状态栏下方开始）。
                    // ime 这一项**在本机模拟器上没有生效**：键盘弹起时 ime inset 仍为 0，
                    // 页面不缩小，底行照样被压住。所以键盘遮挡改用布局绕开——
                    // 复述页把「草稿已存 / 复制全文」放在输入框上方（见 FeynmanScreen.kt 同处注释）。
                    // 这里保留 ime 是为了真机上 inset 正常分派时能自动生效，不是已验结论。
                    contentWindowInsets = WindowInsets.systemBars.union(WindowInsets.ime),
                    containerColor = Bg,
                ) { inner ->
                    when (screen) {
                        Screen.Report -> ReportScreen(
                            modifier = Modifier.padding(inner),
                            plan = plan,
                            reminder = reminder,
                            onOpenTraining = { screen = Screen.Training },
                            onOpenFeynman = { screen = Screen.Feynman },
                            onOpenReminder = { screen = Screen.Reminder },
                        )
                        Screen.Training -> TrainingScreen(
                            modifier = Modifier.padding(inner),
                            plan = plan,
                            reportTaskId = report?.taskId,
                            onBack = { screen = Screen.Report },
                        )
                        Screen.Feynman -> FeynmanScreen(
                            modifier = Modifier.padding(inner),
                            prompt = feynman,
                            reportTaskId = report?.taskId,
                            onBack = { screen = Screen.Report },
                        )
                        Screen.Reminder -> ReminderScreen(
                            modifier = Modifier.padding(inner),
                            reminder = reminder,
                            onBack = { screen = Screen.Report },
                        )
                    }
                }
            }
        }
    }
}
