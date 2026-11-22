package com.thompsonfabco.filamentkit.ui.demo

import android.os.Build
import androidx.compose.runtime.Composable
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import com.thompsonfabco.filamentkit.net.ApiError
import com.thompsonfabco.filamentkit.ui.theme.Appearance
import com.thompsonfabco.filamentkit.ui.theme.Look
import com.thompsonfabco.filamentkit.ui.theme.SpoolwardenTheme
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/** The demo notice (05-android-app 3.7): when it shows, and what Leave demo does with each answer. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [Build.VERSION_CODES.S], qualifiers = "w411dp-h8000dp")
class DemoNoticeTest {
    @get:Rule val rule = createComposeRule()

    @Test fun theNoticeShowsOnlyInDemoOncePairedOutsidePairingAndNotAfterLeaving() {
        assertTrue(demoNoticeShown(demo = true, paired = true, inPairing = false, left = false))
        assertFalse("not in demo", demoNoticeShown(demo = false, paired = true, inPairing = false, left = false))
        assertFalse("not paired", demoNoticeShown(demo = true, paired = false, inPairing = false, left = false))
        assertFalse("pairing screens", demoNoticeShown(demo = true, paired = true, inPairing = true, left = false))
        assertFalse("left", demoNoticeShown(demo = true, paired = true, inPairing = false, left = true))
    }

    @Test fun leavingGoneWhenTheHubSaysDemoIsOff() = runBlocking {
        assertEquals(LeaveOutcome.Gone, leaveDemo { false })
    }

    @Test fun aHubStillInDemoIsARefusalWithNoText() = runBlocking {
        assertEquals(LeaveOutcome.Refused(""), leaveDemo { true })
    }

    @Test fun a409NotInDemoJustRemovesTheNotice() = runBlocking {
        assertEquals(LeaveOutcome.Gone, leaveDemo { throw ApiError(409, NOT_IN_DEMO, "Not in the demo.") })
    }

    @Test fun anotherRefusalIsTheHubsText() = runBlocking {
        assertEquals(LeaveOutcome.Refused("Not allowed."), leaveDemo { throw ApiError(403, "forbidden", "Not allowed.") })
        assertEquals(LeaveOutcome.Refused("x"), leaveDemo { throw ApiError(409, "other", "x") })
        assertEquals(LeaveOutcome.Refused("down"), leaveDemo { throw ApiError(0, "offline", "down") })
    }

    @Composable
    private fun Themed(content: @Composable () -> Unit) = SpoolwardenTheme(Look.Console, Appearance.Light) { content() }

    @Test fun theNoticeHasNoDismissAsksFirstAndLeavesOnConfirm() {
        var calls = 0
        var gone = false
        rule.setContent { Themed { DemoNotice(leave = { calls++; false }, onGone = { gone = true }) } }
        rule.onNodeWithText("Demo data. Nothing here is real.").assertExists()
        rule.onAllNodesWithText("Dismiss").assertCountEquals(0)
        rule.onNodeWithText("Leave demo").performClick()
        rule.onNodeWithText("Leave the demo?").assertExists()
        rule.onNodeWithText("Everything in it is deleted.").assertExists()
        assertEquals("asking does not call", 0, calls)
        // the dialog's confirming button carries the same words as the notice's
        rule.onAllNodesWithText("Leave demo")[1].performClick()
        rule.waitForIdle()
        assertEquals(1, calls)
        assertTrue(gone)
    }

    @Test fun cancelLeavesTheDemoAlone() {
        var calls = 0
        rule.setContent { Themed { DemoNotice(leave = { calls++; false }, onGone = {}) } }
        rule.onNodeWithText("Leave demo").performClick()
        rule.onNodeWithText("Cancel").performClick()
        rule.waitForIdle()
        assertEquals(0, calls)
        rule.onAllNodesWithText("Leave the demo?").assertCountEquals(0)
    }

    @Test fun aRefusalShowsTheHubsTextInTheDialog() {
        rule.setContent { Themed { DemoNotice(leave = { throw ApiError(403, "forbidden", "Not allowed.") }, onGone = {}) } }
        rule.onNodeWithText("Leave demo").performClick()
        rule.onAllNodesWithText("Leave demo")[1].performClick()
        rule.waitForIdle()
        rule.onNodeWithText("Not allowed.").assertExists()
    }
}
