package com.arena.companion

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * 跨页面共用的小零件：返回胶囊、提示条、小标签、可勾选行。
 *
 * 为什么单独一个文件：训练单页原来把自己的三份私有实现写在页面里，加第三页时
 * 复制第四份很容易，改颜色时又会漏掉其中两份。共用件的判据是「两个以上页面会用」，
 * 只在一个页面出现的组件仍留在该页文件里，不提前抽象。
 */

@Composable
fun BackRow(label: String, onBack: () -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(
            Modifier
                .clip(PillShape)
                .border(1.dp, Line, PillShape)
                .clickable(onClick = onBack)
                .padding(horizontal = 12.dp, vertical = 6.dp),
        ) {
            Text("← $label", fontSize = 12.5.sp, color = Ink2, fontWeight = FontWeight.Medium)
        }
    }
}

/** 报错/说明条。tone 取 Fail 或 Warn；不用绿色，这一条只在"有问题"时出现。 */
@Composable
fun NoticeCard(title: String, body: String, tone: Color) {
    Column(
        Modifier
            .fillMaxWidth()
            .clip(BannerShape)
            .background(tone.copy(alpha = 0.06f))
            .border(1.dp, tone.copy(alpha = 0.25f), BannerShape)
            .padding(CardPad),
    ) {
        Text(title, style = TH2, color = tone)
        Spacer(Modifier.height(6.dp))
        Text(body, style = TSmall)
    }
}

@Composable
fun Tag(text: String, tone: Color) {
    Box(
        Modifier
            .clip(RoundedCornerShape(6.dp))
            .background(tone.copy(alpha = 0.08f))
            .padding(horizontal = 8.dp, vertical = 3.dp),
    ) {
        Text(text, fontSize = 11.sp, color = tone, fontWeight = FontWeight.Medium)
    }
}

/** 勾选框：方角圆边的 18dp 小方块，勾上是实心 Brand。不引 material 的 Checkbox——那套留白太大。 */
@Composable
fun TickBox(checked: Boolean, onToggle: () -> Unit) {
    Box(
        Modifier
            .size(18.dp)
            .clip(RoundedCornerShape(5.dp))
            .background(if (checked) Brand else Color.Transparent)
            .border(1.5.dp, if (checked) Brand else Line, RoundedCornerShape(5.dp))
            .clickable(onClick = onToggle),
        contentAlignment = Alignment.Center,
    ) {
        if (checked) {
            Text("✓", fontSize = 11.sp, color = CardBg, fontWeight = FontWeight.Bold)
        }
    }
}

/** 小号说明行，统一缩进，避免各页自己数 8dp/10dp。 */
@Composable
fun MicroNote(text: String, modifier: Modifier = Modifier) {
    Text(text, style = TMicro, modifier = modifier.fillMaxWidth())
}
