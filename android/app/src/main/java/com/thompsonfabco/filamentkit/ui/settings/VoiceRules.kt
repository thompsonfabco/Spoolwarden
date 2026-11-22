package com.thompsonfabco.filamentkit.ui.settings

import com.thompsonfabco.filamentkit.net.ApiError
import com.thompsonfabco.filamentkit.net.VoiceSettings
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject

/** The rules of the Weigh Station voice group, free of the screen. */
internal const val SPEED_MIN = 50
internal const val SPEED_MAX = 200
internal const val SPEED_STEP = 10
internal const val VOLUME_MIN = 0
internal const val VOLUME_MAX = 100

/** The slider's stops between its ends: 50, 60 ... 200 is 16 positions, so 14 in between. */
internal const val SPEED_SLIDER_STEPS = (SPEED_MAX - SPEED_MIN) / SPEED_STEP - 1

/** What the Voice group knows: the Hub's last answer (null: none, or no such route) and what is going on. */
internal data class VoiceUi(
    val settings: VoiceSettings? = null,
    /** The Hub could not be reached for the last call: the controls wait for it (marked as needing the Hub). */
    val offline: Boolean = false,
    val testing: Boolean = false,
    /** Counts up when the controls must show [settings] again (a save that did not take). */
    val revision: Int = 0,
) {
    /** The group exists only while the Hub says it has a Weigh Station. */
    val shown: Boolean get() = settings?.station == true
}

/** What the controls show and Test sends: the values on the screen, saved or not. */
internal data class VoiceEdit(val speech: Boolean, val speaker: Int, val speed: Int, val volume: Int) {
    companion object {
        fun of(s: VoiceSettings) = VoiceEdit(s.speech, s.speaker, s.speed, s.volume)
    }
}

/** The nearest step of 10 in 50 to 200 for a slider position. */
internal fun snapSpeed(raw: Float): Int =
    (Math.round(raw / SPEED_STEP) * SPEED_STEP).coerceIn(SPEED_MIN, SPEED_MAX)

internal fun snapVolume(raw: Float): Int = Math.round(raw).coerceIn(VOLUME_MIN, VOLUME_MAX)

/** The line under the read-only Voice row: where the voice is chosen. */
internal fun voiceHint(s: VoiceSettings): String = if (s.remote) VoiceTexts.VOICE_HINT_REMOTE else VoiceTexts.VOICE_HINT

/** The Speaker row exists only for a voice with more than one speaker. */
internal fun speakerShown(s: VoiceSettings): Boolean = s.speakerRange != null

/** The speaker typed, or null when it is not a whole number inside [range]. */
internal fun speakerFrom(text: String, range: IntRange?): Int? =
    text.trim().toIntOrNull()?.takeIf { range != null && it in range }

/** `PATCH /api/voice` carries only what differs from what the Hub has; empty means nothing to save. */
internal fun voicePatch(saved: VoiceSettings, edit: VoiceEdit): JsonObject = buildJsonObject {
    if (edit.speech != saved.speech) put("speech", JsonPrimitive(edit.speech))
    if (speakerShown(saved) && edit.speaker != saved.speaker) put("speaker", JsonPrimitive(edit.speaker))
    if (edit.speed != saved.speed) put("speed", JsonPrimitive(edit.speed))
    if (edit.volume != saved.volume) put("volume", JsonPrimitive(edit.volume))
}

/** What Test asks for: the values on the screen; the speaker only where the voice has more than one. */
internal class VoiceTestRequest(val speaker: Int?, val speed: Int, val volume: Int)

internal fun voiceTestRequest(saved: VoiceSettings, edit: VoiceEdit) =
    VoiceTestRequest(edit.speaker.takeIf { speakerShown(saved) }, edit.speed, edit.volume)

/** The sentence a failed Test shows: the Hub's own, else ours for its three refusals. */
internal fun testFailureText(e: ApiError): String = when {
    !e.notFromHub -> e.message
    e.code == "busy" -> VoiceTexts.TEST_BUSY
    e.code == "off" -> VoiceTexts.TEST_OFF
    else -> VoiceTexts.TEST_NO_ANSWER
}

/** The Hub could not be reached: the controls are marked as needing the Hub. */
internal fun ApiError.isOffline(): Boolean = status == 0 && (code == "offline" || code == "local_network")
