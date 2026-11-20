package com.arena.companion

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
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
 * 两页：诊断报告 → 训练单（点"看训练单"进，返回键回）。三页起（费曼 / 提醒）
 * 再换真正的导航组件；现在两页用状态切换就够，先不为将来的页面付抽象成本。
 *
 * 页面只读 `assets/` 下两个 JSON，不发任何网络请求。
 * 联调后端在电脑侧完成（端口转发 + `curl`），不在学习者界面里留调试入口——
 * 页面上出现本机回环地址，评委看到的就是「没做完」。
 */
class MainActivity : ComponentActivity() {
    private enum class Screen { Report, Training }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // 浅底 + 深色状态栏图标，避免状态栏文字在灰白背景上糊掉
        WindowCompat.getInsetsController(window, window.decorView).isAppearanceLightStatusBars = true
        setContent {
            ArenaTheme {
                val ctx = LocalContext.current
                val report = remember { loadSampleReport(ctx) }
                val plan = remember { loadTrainingPlan(ctx) }
                var screen by remember { mutableStateOf(Screen.Report) }

                BackHandler(enabled = screen == Screen.Training) { screen = Screen.Report }

                Scaffold(
                    modifier = Modifier.fillMaxSize(),
                    containerColor = Bg,
                ) { inner ->
                    when (screen) {
                        Screen.Report -> ReportScreen(
                            modifier = Modifier.padding(inner),
                            plan = plan,
                            onOpenTraining = { screen = Screen.Training },
                        )
                        Screen.Training -> TrainingScreen(
                            modifier = Modifier.padding(inner),
                            plan = plan,
                            reportTaskId = report?.taskId,
                            onBack = { screen = Screen.Report },
                        )
                    }
                }
            }
        }
    }
}
