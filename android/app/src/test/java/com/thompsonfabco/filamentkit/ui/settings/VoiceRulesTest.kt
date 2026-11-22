package com.thompsonfabco.filamentkit.ui.settings

import com.thompsonfabco.filamentkit.net.ApiError
import com.thompsonfabco.filamentkit.net.VoiceSettings
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The Weigh Station voice rules (R88 2026-11-22 01:35, point 4). */
class VoiceRulesTest {
    private val many = VoiceSettings(station = true, speech = true, voice = "Piper, many speakers", speaker = 5, speakerRange = 0..903, speed = 100, volume = 50)
    private val one = many.copy(speakerRange = null, speaker = 0)

    @Test fun theGroupExistsOnlyWhenTheHubHasAWeighStation() {
        assertFalse("nothing read yet", VoiceUi().shown)
        assertFalse("no such route (404) or a Hub without a Weigh Station", VoiceUi(settings = null, offline = false).shown)
        assertFalse(VoiceUi(settings = VoiceSettings(station = false)).shown)
        assertTrue(VoiceUi(settings = many).shown)
        assertTrue("unreachable keeps the last answer on screen", VoiceUi(settings = many, offline = true).shown)
    }

    @Test fun theListShowsTheGroupAfterAlertsOnlyWhenAsked() {
        assertFalse(SettingsGroup.Voice in settingsGroups(developer = false))
        assertFalse(SettingsGroup.Voice in settingsGroups(developer = true, voice = false))
        val groups = settingsGroups(developer = false, voice = true)
        assertEquals(SettingsGroup.Alerts, groups[groups.indexOf(SettingsGroup.Voice) - 1])
        assertEquals(VoiceTexts.GROUP, "Weigh Station voice")
    }

    @Test fun theVoiceLineSaysWhereTheVoiceIsChosen() {
        assertEquals("Change the voice on the Hub page.", voiceHint(many))
        assertEquals("Chosen on the Weigh Station's own computer: spoolwarden config.", voiceHint(many.copy(remote = true)))
    }

    @Test fun theSpeakerRowNeedsARange() {
        assertTrue(speakerShown(many))
        assertFalse("a voice with one speaker", speakerShown(one))
        assertEquals(5, speakerFrom(" 5 ", many.speakerRange))
        assertEquals(903, speakerFrom("903", many.speakerRange))
        assertNull(speakerFrom("904", many.speakerRange))
        assertNull(speakerFrom("-1", many.speakerRange))
        assertNull(speakerFrom("", many.speakerRange))
        assertNull(speakerFrom("3", null))
    }

    @Test fun theHintCountsTheSpeakersFromTheRange() {
        assertEquals("This voice has 904 speakers. Try a few with Test.", VoiceTexts.speakerHint(many.speakerRange!!.last + 1))
    }

    @Test fun speedGoesInStepsOfTenFrom50To200() {
        assertEquals(14, SPEED_SLIDER_STEPS)
        assertEquals(50, snapSpeed(0f))
        assertEquals(50, snapSpeed(54f))
        assertEquals(60, snapSpeed(55f))
        assertEquals(100, snapSpeed(103f))
        assertEquals(200, snapSpeed(400f))
    }

    @Test fun volumeIsAWholeNumberFrom0To100() {
        assertEquals(0, snapVolume(-5f))
        assertEquals(37, snapVolume(36.6f))
        assertEquals(100, snapVolume(250f))
    }

    @Test fun thePatchCarriesOnlyWhatChanged() {
        val same = VoiceEdit.of(many)
        assertTrue(voicePatch(many, same).isEmpty())
        assertEquals(buildJsonObject { put("speed", JsonPrimitive(120)) }, voicePatch(many, same.copy(speed = 120)))
        assertEquals(
            buildJsonObject { put("speech", JsonPrimitive(false)); put("volume", JsonPrimitive(10)) },
            voicePatch(many, same.copy(speech = false, volume = 10)),
        )
        assertEquals(buildJsonObject { put("speaker", JsonPrimitive(9)) }, voicePatch(many, same.copy(speaker = 9)))
    }

    @Test fun aSpeakerIsNeverSentForAVoiceWithOne() {
        assertTrue(voicePatch(one, VoiceEdit.of(one).copy(speaker = 4)).isEmpty())
        assertNull(voiceTestRequest(one, VoiceEdit.of(one)).speaker)
    }

    @Test fun testUsesTheValuesOnTheScreen() {
        val r = voiceTestRequest(many, VoiceEdit(speech = true, speaker = 77, speed = 150, volume = 20))
        assertEquals(77, r.speaker)
        assertEquals(150, r.speed)
        assertEquals(20, r.volume)
    }

    @Test fun theHubsSentenceIsShownAsItIsSent() {
        assertEquals("Busy now.", testFailureText(ApiError(409, "busy", "Busy now.", noHubMessage = false)))
    }

    @Test fun aReplyWithoutAHubSentenceFallsBackToOurs() {
        fun bare(status: Int, code: String) = ApiError(status, code, "The Hub returned an error ($status)", noHubMessage = true)
        assertEquals(VoiceTexts.TEST_BUSY, testFailureText(bare(409, "busy")))
        assertEquals(VoiceTexts.TEST_OFF, testFailureText(bare(409, "off")))
        assertEquals(VoiceTexts.TEST_NO_ANSWER, testFailureText(bare(502, "no_answer")))
    }

    @Test fun offlineIsTheHubNotAnsweringAtAll() {
        assertTrue(ApiError(0, "offline", "x").isOffline())
        assertTrue(ApiError(0, "local_network", "x").isOffline())
        assertFalse(ApiError(502, "no_answer", "x").isOffline())
    }
}
