package com.thompsonfabco.filamentkit.ui.hub

import android.content.Context
import androidx.annotation.StringRes
import androidx.compose.runtime.Composable
import androidx.compose.ui.platform.LocalContext

/**
 * A text of the Hub screen without the words themselves: a catalogue text and its arguments, words the Hub sent and the
 * app shows as given, or several of these joined with a middle dot. The model holds these so its tests need no
 * resources; the screen turns one into text with [hubText].
 */
sealed interface HubText {
    data class Words(@StringRes val id: Int, val args: List<Any> = emptyList()) : HubText
    data class FromHub(val text: String) : HubText
    data class Joined(val parts: List<HubText>) : HubText
}

fun HubText.resolve(context: Context): String = when (this) {
    is HubText.Words -> context.getString(id, *args.toTypedArray())
    is HubText.FromHub -> text
    is HubText.Joined -> parts.joinToString(" · ") { it.resolve(context) }
}

@Composable
internal fun hubText(text: HubText): String = text.resolve(LocalContext.current)
