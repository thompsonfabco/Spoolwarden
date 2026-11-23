package com.thompsonfabco.filamentkit.ui

import androidx.annotation.StringRes
import com.thompsonfabco.filamentkit.R

/** A weight in grams the app will send or store: finite and above zero (no NaN, no Infinity). */
fun isValidWeight(grams: Double): Boolean = grams.isFinite() && grams > 0

/** Parses typed grams; null for blank, junk, zero, negative or non-finite text ("NaN", "Infinity", "1e400"). */
fun parseWeight(text: String): Double? = text.trim().toDoubleOrNull()?.takeIf(::isValidWeight)

/** Why Update weight can't be tapped yet; null when it can. */
@StringRes
fun weightHint(text: String): Int? = when {
    parseWeight(text) != null -> null
    text.isBlank() -> R.string.spool_weight_hint_empty
    else -> R.string.spool_weight_hint_invalid
}

/** The inline message for a typed empty-spool weight (tare); null when blank (unknown) or above 0 g. */
@StringRes
fun tareProblem(text: String): Int? = if (text.isBlank() || parseWeight(text) != null) null else R.string.spool_tare_invalid
