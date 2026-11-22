package com.thompsonfabco.filamentkit.ui.settings

/**
 * The texts of the Weigh Station voice group (R88 2026-11-22 01:35, VO5) that are not in the string catalogue yet. Only
 * the catalogue step (RG1) opens `strings/en.json`; each text here has a proposed key in the VO5 report and moves
 * there with it. "Needs the Hub. Reconnecting…" is a catalogue text already; the sentence of a refused Test is the
 * Hub's own, shown as it sends it (the three results below are for a reply that carries no `message`).
 */
object VoiceTexts {
    const val GROUP = "Weigh Station voice"

    /** The rows. */
    const val SPEAK = "Speak"
    const val VOICE = "Voice"
    const val VOICE_HINT = "Change the voice on the Hub page."
    const val SPEAKER = "Speaker"
    const val SPEED = "Speed"
    const val VOLUME = "Volume"
    const val TEST = "Test"

    /** Under the Speaker row; [speakers] is how many the voice has (the range's last number plus one). */
    fun speakerHint(speakers: Int) = "This voice has $speakers speakers. Try a few with Test."

    /** The results of Test (R88 2.2). */
    const val TEST_PLAYED = "Played on the Weigh Station."
    const val TEST_NO_ANSWER = "The Weigh Station did not answer."
    const val TEST_BUSY = "The Weigh Station is in use. Try again when it is idle."
    const val TEST_OFF = "Speaking is switched off."

    /** A save the Hub refused without a sentence of its own. */
    const val SAVE_FAILED = "The change was not saved."
}
