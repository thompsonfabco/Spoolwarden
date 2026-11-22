package com.thompsonfabco.filamentkit.ui.spool

import android.os.Build
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import com.thompsonfabco.filamentkit.net.ApiError
import com.thompsonfabco.filamentkit.ui.theme.Appearance
import com.thompsonfabco.filamentkit.ui.theme.Look
import com.thompsonfabco.filamentkit.ui.theme.SpoolwardenTheme
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/** Print a label (05-android-app 7.10): the sizes, the page handed to print, the answers, and the sheet. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [Build.VERSION_CODES.S], qualifiers = "w411dp-h8000dp")
class LabelSheetTest {
    @get:Rule val rule = createComposeRule()

    private val spool = SpoolSummary(214, "Polymaker", "PolyTerra PLA Charcoal Black", "PLA", "#333333", "Shelf B", 620.0)

    @Test fun theFiveSizesAskTheHubByTheirWireNamesTheRollFirst() {
        assertEquals(listOf("62x29", "50x30", "a4-63x34", "a4-70x37", "letter-66x25"), LabelSize.entries.map { it.wire })
        assertEquals(
            listOf(
                "62 x 29 mm", "50 x 30 mm", "A4 sheet of 24, 63.5 x 33.9 mm", "A4 sheet of 24, 70 x 37 mm",
                "Letter sheet of 30, 2-5/8 x 1 in",
            ),
            LabelSize.entries.map { it.title },
        )
    }

    @Test fun aLabelIsPrintedAtItsOwnSizeInThousandthsOfAnInch() {
        fun mils(size: LabelSize) = labelMedia(size).let { it.widthMils to it.heightMils }
        assertEquals(2441 to 1142, mils(LabelSize.Small))
        assertEquals(1969 to 1181, mils(LabelSize.Medium))
        assertEquals(2500 to 1335, mils(LabelSize.A4Small))
        assertEquals(2756 to 1457, mils(LabelSize.A4Large))
        assertEquals(2625 to 1000, mils(LabelSize.Letter))
    }

    @Test fun thePrintedPageHoldsTheDrawingAsAnImage() {
        val html = labelHtml("<svg/>".toByteArray())
        assertTrue(html, html.contains("src=\"data:image/svg+xml;base64,PHN2Zy8+\""))
        assertTrue(!html.contains("<svg"))
    }

    @Test fun theHubsAnswerIsTheDrawingOrItsText() = runBlocking {
        assertEquals("x", String((loadLabel { "x".toByteArray() } as LabelLoad.Loaded).svg))
        assertEquals(LabelLoad.Failed("No such spool."), loadLabel { throw ApiError(404, "not_found", "No such spool.") })
    }

    @Test fun theAddressIsTheBaseUrlWithoutItsScheme() {
        assertEquals("hub.local:8710", hubAddress("https://hub.local:8710"))
        assertEquals("[fe80::1]:8710", hubAddress("https://[fe80::1]:8710"))
    }

    private fun show(source: LabelSource) = rule.setContent {
        SpoolwardenTheme(Look.Console, Appearance.Light) { PrintLabelSheet(spool, source) {} }
    }

    @Test fun theSheetShowsTheSizesThePreviewTheAddressAndPrint() {
        val asked = mutableListOf<String>()
        show(LabelSource({ size -> asked += size; "<svg/>".toByteArray() }, { "hub.local:8710" }))
        rule.waitForIdle()
        rule.onNodeWithText("Print a label").assertExists()
        rule.onNodeWithText("62 x 29 mm").assertExists()
        rule.onNodeWithContentDescription("Label of spool 214: Polymaker PolyTerra PLA Charcoal Black, PLA, 620 g").assertExists()
        rule.onNodeWithText(
            "The code holds this Hub's address, hub.local:8710. If the Hub is renamed or moves to another port, " +
                "old labels still open in this app's scanner but not from a phone's camera.",
        ).assertExists()
        rule.onNodeWithText("Print").assertExists()
        assertEquals(listOf("62x29"), asked)
        var current = "62 x 29 mm"
        for ((title, wire) in listOf(
            "50 x 30 mm" to "50x30", "A4 sheet of 24, 63.5 x 33.9 mm" to "a4-63x34", "A4 sheet of 24, 70 x 37 mm" to "a4-70x37",
            "Letter sheet of 30, 2-5/8 x 1 in" to "letter-66x25",
        )) {
            rule.onNodeWithText(current).performClick()
            rule.onNodeWithText(title).performClick()
            rule.waitForIdle()
            assertEquals(wire, asked.last())
            current = title
        }
        assertEquals(listOf("62x29", "50x30", "a4-63x34", "a4-70x37", "letter-66x25"), asked)
    }

    @Test fun aBadSizeRefusalShowsTheHubsOwnMessage() {
        val refusal = "The size must be 62x29, 50x30, a4-63x34, a4-70x37 or letter-66x25."
        show(LabelSource({ throw ApiError(400, "bad_size", refusal) }, { "hub.local" }))
        rule.waitForIdle()
        rule.onNodeWithText("Could not draw the label. $refusal").assertExists()
    }

    @Test fun aRefusalShowsTheHubsTextAndTryAgainAsksAgain() {
        var calls = 0
        show(LabelSource({ calls++; throw ApiError(404, "not_found", "No such spool.") }, { "hub.local" }))
        rule.waitForIdle()
        rule.onNodeWithText("Could not draw the label. No such spool.").assertExists()
        rule.onNodeWithText("Print").assertIsNotEnabled()
        rule.onNodeWithText("Try again").performClick()
        rule.waitForIdle()
        assertEquals(2, calls)
    }

    @Test fun printWaitsForTheDrawing() {
        show(LabelSource({ throw ApiError(0, "offline", "down") }, { "h" }))
        rule.waitForIdle()
        rule.onAllNodesWithText("Print").assertCountEquals(1)
        rule.onNodeWithText("Print").assertIsNotEnabled()
    }
}
