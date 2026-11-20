package com.arena.companion

import android.content.Context
import android.content.Intent
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import java.util.Locale

/**
 * 语音输入：走系统语音识别（`RecognizerIntent`），识别结果回填到文本框。
 *
 * 为什么不自己录音再上传转写：这一页的产品承诺是「不发网络请求」，
 * 而端上离线 ASR 要么体积大到不像个伴读 App，要么许可不清。
 * 交给系统识别器还有个好处——用户在系统里换语言、换引擎，这里自动跟着走。
 *
 * 可用性必须**先问再显示**：模拟器/精简系统上常常没有识别服务，
 * 按钮点下去只会闪一下就回来。`isRecognitionAvailable` 为 false 时，
 * 页面直接说明"本机没有语音识别服务"，而不是给一个按了没反应的麦克风。
 */
object VoiceInput {

    /** 识别服务是否在这台机器上可用。抛异常按不可用处理——宁可少一个功能，不给死按钮。 */
    fun isAvailable(ctx: Context): Boolean = try {
        SpeechRecognizer.isRecognitionAvailable(ctx)
    } catch (e: Exception) {
        false
    }

    fun intent(): Intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
        putExtra(
            RecognizerIntent.EXTRA_LANGUAGE_MODEL,
            RecognizerIntent.LANGUAGE_MODEL_FREE_FORM,
        )
        putExtra(RecognizerIntent.EXTRA_LANGUAGE, Locale.getDefault().toLanguageTag())
        putExtra(RecognizerIntent.EXTRA_PROMPT, "讲一遍这题：要交什么、卡在哪、下次怎么改")
        putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, false)
        putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
    }

    /** 取第一条识别结果。空串按"没听清"处理，返回 null，由页面提示重说。 */
    fun firstResult(data: Intent?): String? {
        val list = data?.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS)
        return list?.firstOrNull()?.trim()?.takeIf { it.isNotEmpty() }
    }
}
