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

    @Test fun theThreeSizesAskTheHubByTheirWireNames() {
        assertEquals(listOf("62x29", "50x30", "sheet24"), LabelSize.entries.map { it.wire })
        assertEquals(listOf("62 x 29 mm", "50 x 30 mm", "Sheet of 24"), LabelSize.entries.map { it.title })
    }

    @Test fun aLabelIsPrintedAtItsOwnSizeInThousandthsOfAnInchAndTheSheetOnA4() {
        val small = labelMedia(LabelSize.Small)
        assertEquals(2441, small.widthMils)
        assertEquals(1142, small.heightMils)
        assertEquals(1969, labelMedia(LabelSize.Medium).widthMils)
        assertEquals(android.print.PrintAttributes.MediaSize.ISO_A4.widthMils, labelMedia(LabelSize.Sheet).widthMils)
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
        rule.onNodeWithText("50 x 30 mm").assertExists()
        rule.onNodeWithText("Sheet of 24").assertExists()
        rule.onNodeWithContentDescription("Label of spool 214: Polymaker PolyTerra PLA Charcoal Black, PLA, 620 g").assertExists()
        rule.onNodeWithText(
            "The code holds this Hub's address, hub.local:8710. If the Hub is renamed or moves to another port, " +
                "old labels still open in this app's scanner but not from a phone's camera.",
        ).assertExists()
        rule.onNodeWithText("Print").assertExists()
        assertEquals(listOf("62x29"), asked)
        rule.onNodeWithText("50 x 30 mm").performClick()
        rule.waitForIdle()
        assertEquals(listOf("62x29", "50x30"), asked)
        rule.onNodeWithText("Sheet of 24").performClick()
        rule.waitForIdle()
        assertEquals(listOf("62x29", "50x30", "sheet24"), asked)
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
