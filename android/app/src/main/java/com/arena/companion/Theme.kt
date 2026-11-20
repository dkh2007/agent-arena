package com.arena.companion

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * 设计参数集中在这里 —— 全项目唯一的视觉真相源。
 *
 * 2026-11-20 风格已定稿（浅色 / 白卡细描边 / 语义色只用红绿琥珀）。
 * 后续换肤、改字号、调圆角，**只改本文件**；页面布局不因换肤而改。
 * 约束：本文件里不允许出现任何来自 `sample-report.json` 的数值。
 * 交接说明见 `docs/android-ui-handoff.md`。
 */

// ---- 底色与文字 ----
val Bg = Color(0xFFF4F6F9)
val CardBg = Color(0xFFFFFFFF)
val Line = Color(0xFFE6EAF1)
val LineSoft = Color(0xFFF0F3F8)
val Ink = Color(0xFF141821)      // 主文字
val Ink2 = Color(0xFF5B6475)     // 次要说明
val Ink3 = Color(0xFF8D95A6)     // 弱化标记
val Brand = Color(0xFF2B4BD8)    // 强调色（骨架期克制使用）

// ---- 语义色 ----
val Pass = Color(0xFF12775A)
val Fail = Color(0xFFC0392B)
val Warn = Color(0xFF9A6A0B)
val Track = Color(0xFFEDF1F6)    // 进度条/环形底槽

// ---- 形状 ----
// 注意 PillShape 用 Int(50) 是"按高度取半"的百分比圆角，不是 50dp 固定半径。
val CardShape = RoundedCornerShape(18.dp)
val BannerShape = RoundedCornerShape(14.dp)
val InnerShape = RoundedCornerShape(12.dp)
val PillShape = RoundedCornerShape(50)

// ---- 间距 ----
val PagePadH = 18.dp
val PagePadTop = 24.dp
val PagePadBottom = 30.dp
val ListGap = 14.dp
val CardPad = 16.dp

// ---- 文字样式 ----
val TDisplay = TextStyle(fontSize = 38.sp, fontWeight = FontWeight.Bold, color = Ink, letterSpacing = (-0.8).sp)
val TH1 = TextStyle(fontSize = 22.sp, fontWeight = FontWeight.SemiBold, color = Ink)
val TH2 = TextStyle(fontSize = 15.sp, fontWeight = FontWeight.SemiBold, color = Ink)
val TEyebrow = TextStyle(fontSize = 11.sp, fontWeight = FontWeight.Medium, color = Ink3, letterSpacing = 1.2.sp)
val TBody = TextStyle(fontSize = 14.sp, fontWeight = FontWeight.Normal, color = Ink, lineHeight = 21.sp)
val TSmall = TextStyle(fontSize = 12.5.sp, fontWeight = FontWeight.Normal, color = Ink2, lineHeight = 18.sp)
val TMicro = TextStyle(fontSize = 11.sp, fontWeight = FontWeight.Normal, color = Ink3, lineHeight = 15.sp)
val TNum = TextStyle(fontSize = 17.sp, fontWeight = FontWeight.SemiBold, color = Ink)

/** 环形/条形用的分数色调：不合格就是红的，不给"温柔"的余地。 */
fun ratioTone(ratio: Double, dq: Boolean, voided: Boolean): Color = when {
    dq || voided -> Fail
    ratio >= 0.8 -> Pass
    ratio >= 0.5 -> Warn
    else -> Fail
}

@Composable
fun ArenaTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        // 每个槽位都要给值：只写 primary/background 时，Material 组件（下拉菜单、底部弹层、
        // 输入框光标）会退回库里自带的紫粉默认色。2026-11-20 实测：输入方式菜单弹出是淡紫色，
        // 在整页浅灰白里像一块没上色的控件。这里是唯一可以一次性堵住的口子。
        colorScheme = lightColorScheme(
            primary = Brand,
            onPrimary = CardBg,
            primaryContainer = Brand.copy(alpha = 0.08f),
            onPrimaryContainer = Brand,
            background = Bg,
            onBackground = Ink,
            surface = CardBg,
            onSurface = Ink,
            surfaceVariant = LineSoft,
            onSurfaceVariant = Ink2,
            surfaceContainerLowest = CardBg,
            surfaceContainerLow = CardBg,
            surfaceContainer = CardBg,
            surfaceContainerHigh = CardBg,
            surfaceContainerHighest = LineSoft,
            outline = Line,
            outlineVariant = LineSoft,
            error = Fail,
            onError = CardBg,
        ),
        typography = Typography(bodyLarge = TBody, bodyMedium = TBody, bodySmall = TSmall),
        content = content,
    )
}
