package com.arena.companion

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Scaffold
import androidx.compose.ui.Modifier
import androidx.core.view.WindowCompat
import java.net.HttpURLConnection
import java.net.URL

/**
 * 入口。第 2 步做的是诊断报告页；第 3 步起会在这里加底部导航，
 * 把「训练 / 提醒」两页挂进来。
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

/** 演示期用 adb reverse tcp:8080 tcp:8080，手机会把 127.0.0.1:8080 转到电脑。 */
internal const val DEFAULT_BASE = "http://127.0.0.1:8080"

/** 返回人类可读结论；任何异常都原样写出来，不吞掉。 */
internal fun probeHealth(base: String): String = try {
    val conn = (URL("$base/healthz").openConnection() as HttpURLConnection).apply {
        requestMethod = "GET"
        connectTimeout = 4000
        readTimeout = 4000
    }
    try {
        val code = conn.responseCode
        val body = (if (code in 200..299) conn.inputStream else conn.errorStream)
            ?.bufferedReader()?.use { it.readText() } ?: ""
        "HTTP $code\n${body.take(300)}"
    } finally {
        conn.disconnect()
    }
} catch (e: Exception) {
    "连接失败：${e.javaClass.simpleName}: ${e.message}"
}
