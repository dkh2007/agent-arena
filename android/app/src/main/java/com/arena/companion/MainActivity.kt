package com.arena.companion

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Scaffold
import androidx.compose.ui.Modifier
import androidx.core.view.WindowCompat

/**
 * 入口。第 2 步做的是诊断报告页；第 3 步起会在这里加底部导航，
 * 把「训练 / 提醒」两页挂进来。
 *
 * 页面只读 `assets/sample-report.json`，不发任何网络请求。
 * 联调后端（`adb reverse tcp:8080 tcp:8080` + `curl`）在电脑侧完成，
 * 不在学习者界面里留调试入口——评委看到 `127.0.0.1` 是硬伤。
 */
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // 浅底 + 深色状态栏图标，避免状态栏文字在灰白背景上糊掉
        WindowCompat.getInsetsController(window, window.decorView).isAppearanceLightStatusBars = true
        setContent {
            ArenaTheme {
                Scaffold(
                    modifier = Modifier.fillMaxSize(),
                    containerColor = Bg,
                ) { inner ->
                    ReportScreen(Modifier.padding(inner))
                }
            }
        }
    }
}
