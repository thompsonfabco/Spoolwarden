package com.thompsonfabco.filamentkit.net

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.doubleOrNull

/**
 * The Weigh Station's voice as `GET /api/voice` and `PATCH /api/voice` answer it. [station] is
 * false when the Hub has no Weigh Station (the app then shows nothing); [speakerRange] is null when the chosen voice has
 * one speaker. The server is not built yet, so a missing or odd field takes its default instead of failing the read.
 */
data class VoiceSettings(
    val station: Boolean,
    /** The Weigh Station runs on another computer than the Hub: its voice file is chosen there. Absent: false. */
    val remote: Boolean = false,
    val speech: Boolean = true,
    /** The name the Hub shows for the voice; read-only here. */
    val voice: String = "",
    val speaker: Int = 0,
    val speakerRange: IntRange? = null,
    val speed: Int = 100,
    val volume: Int = 50,
)

object VoiceParsing {
    fun settings(body: JsonElement): VoiceSettings {
        val o = body as? JsonObject ?: return VoiceSettings(station = false)
        return VoiceSettings(
            station = o.bool("station") ?: false,
            remote = o.bool("remote") ?: false,
            speech = o.bool("speech") ?: true,
            voice = (o["voice"] as? JsonPrimitive)?.contentOrNull.orEmpty(),
            speaker = o.int("speaker") ?: 0,
            speakerRange = range(o["speaker_range"]),
            speed = o.int("speed") ?: 100,
            volume = o.int("volume") ?: 50,
        )
    }

    /** `[0, 903]` is 0..903; null, a wrong shape or a backwards pair is no range. */
    private fun range(e: JsonElement?): IntRange? {
        val a = (e as? JsonArray)?.takeIf { it.size == 2 } ?: return null
        val from = number(a[0]) ?: return null
        val to = number(a[1]) ?: return null
        return if (to >= from) from..to else null
    }

    private fun JsonObject.bool(key: String): Boolean? = (this[key] as? JsonPrimitive)?.booleanOrNull
    private fun JsonObject.int(key: String): Int? = number(this[key])
    private fun number(e: JsonElement?): Int? = (e as? JsonPrimitive)?.doubleOrNull?.toInt()
}
