package com.arena.companion

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import java.net.HttpURLConnection
import java.net.URL
import kotlin.concurrent.thread

/**
 * 第 1 步（2026-11-20）：只做"能编译、能装、能跑、能证明后端可达"。
 * 刻意不写业务界面——先拿到一个可真机验证的最小体，再逐页加。
 */
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme {
                Scaffold(modifier = Modifier.fillMaxSize()) { inner ->
                    HomeScreen(Modifier.padding(inner))
                }
            }
        }
    }
}

/** 演示期用 adb reverse tcp:8080 tcp:8080，手机会把 127.0.0.1:8080 转到电脑。 */
private const val DEFAULT_BASE = "http://127.0.0.1:8080"

@Composable
fun HomeScreen(modifier: Modifier = Modifier) {
    var status by remember { mutableStateOf("未检测：点下面按钮探一次后端") }
    var busy by remember { mutableStateOf(false) }

    Column(
        modifier = modifier.fillMaxSize().padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp)
    ) {
        Text("Agent 演武场 · 学习伴侣", style = MaterialTheme.typography.headlineSmall)
        Text(
            "第 1 步验证：应用能起来、能连上电脑上的后端。",
            style = MaterialTheme.typography.bodyMedium
        )

        Card(modifier = Modifier.fillMaxWidth()) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("后端地址", style = MaterialTheme.typography.labelMedium)
                Text(DEFAULT_BASE, style = MaterialTheme.typography.bodySmall)
                Text("探活结果", style = MaterialTheme.typography.labelMedium)
                Text(status, style = MaterialTheme.typography.bodyMedium)
            }
        }

        Button(
            enabled = !busy,
            onClick = {
                busy = true
                status = "正在请求 /healthz …"
                thread {
                    val r = probeHealth(DEFAULT_BASE)
                    status = r
                    busy = false
                }
            }
        ) { Text(if (busy) "请求中…" else "探活后端") }
    }
}

/** 返回人类可读结论；任何异常都原样写出来，不吞掉。 */
private fun probeHealth(base: String): String = try {
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
