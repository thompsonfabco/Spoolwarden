package com.thompsonfabco.filamentkit.ui.spool

import android.os.Build
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import com.thompsonfabco.filamentkit.net.ApiError
import com.thompsonfabco.filamentkit.net.FingerprintMismatch
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

/** Print a label (05-android-app 7.10): the two roll sizes, the page handed to print, the answers, and the sheet. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [Build.VERSION_CODES.S], qualifiers = "w411dp-h8000dp")
class LabelSheetTest {
    @get:Rule val rule = createComposeRule()

    private val spool = SpoolSummary(214, "Polymaker", "PolyTerra PLA Charcoal Black", "PLA", "#333333", "Shelf B", 620.0)

    @Test fun thePhoneOffersTheTwoRollSizesAndNoSheetSize() {
        val context = org.robolectric.RuntimeEnvironment.getApplication()
        assertEquals(listOf("62x29", "50x30"), LabelSize.entries.map { it.wire })
        assertEquals(listOf("62 x 29 mm", "50 x 30 mm"), LabelSize.entries.map { context.getString(it.titleRes) })
    }

    @Test fun aLabelIsPrintedAtItsOwnSizeInThousandthsOfAnInch() {
        val context = org.robolectric.RuntimeEnvironment.getApplication()
        fun mils(size: LabelSize) = labelMedia(context, size).let { it.widthMils to it.heightMils }
        assertEquals(2441 to 1142, mils(LabelSize.Small))
        assertEquals(1969 to 1181, mils(LabelSize.Medium))
    }

    @Test fun thePrintedPageHoldsTheDrawingAsAnImageWithNoGapUnderIt() {
        val html = labelHtml("<svg/>".toByteArray())
        assertTrue(html, html.contains("src=\"data:image/svg+xml;base64,PHN2Zy8+\""))
        assertTrue(html.contains("display:block"))
        assertTrue(html.contains("@page{margin:0}"))
        assertTrue(!html.contains("<svg"))
    }

    @Test fun theHubsAnswerIsTheDrawingOrItsText() = runBlocking {
        assertEquals("x", String((loadLabel { "x".toByteArray() } as LabelLoad.Loaded).svg))
        assertEquals(LabelLoad.Failed("No such spool."), loadLabel { throw ApiError(404, "not_found", "No such spool.") })
    }

    @Test fun aChangedCertificateAndAnyOtherFailureAreToldNotThrown() = runBlocking {
        assertEquals(LabelLoad.CertificateChanged, loadLabel { throw FingerprintMismatch("aa", "bb") })
        assertEquals(LabelLoad.Failed("boom"), loadLabel { throw IllegalStateException("boom") })
    }

    @Test fun theTextsAreWholeSentences() {
        val context = org.robolectric.RuntimeEnvironment.getApplication()
        assertEquals("Could not draw the label.", context.getString(com.thompsonfabco.filamentkit.R.string.label_print_error))
        assertEquals("Could not draw the label. No such spool.", context.getString(com.thompsonfabco.filamentkit.R.string.label_print_error_reason, "No such spool."))
        assertEquals("Label of spool 7: A, PLA, 620 g", context.getString(com.thompsonfabco.filamentkit.R.string.label_print_preview_left, 7, "A", "PLA", "620"))
        assertEquals("Label of spool 7: A, PLA", context.getString(com.thompsonfabco.filamentkit.R.string.label_print_preview, 7, "A", "PLA"))
    }

    private fun show(source: LabelSource) = rule.setContent {
        SpoolwardenTheme(Look.Console, Appearance.Light) { PrintLabelSheet(spool, source) {} }
    }

    @Test fun theSheetShowsTheSizeChoiceThePreviewTheNoteTheAddressLineAndPrint() {
        val asked = mutableListOf<String>()
        show(LabelSource { size -> asked += size; "<svg/>".toByteArray() })
        rule.waitForIdle()
        rule.onNodeWithText("Print a label").assertExists()
        rule.onNodeWithText("Size").assertExists()
        rule.onNodeWithText("62 x 29 mm").assertExists()
        rule.onNodeWithText("50 x 30 mm").assertExists()
        rule.onAllNodesWithText("Sheet of 24").assertCountEquals(0)
        rule.onNodeWithText("Sheets of labels are printed from the Hub page or the web app.").assertExists()
        rule.onNodeWithContentDescription("Label of spool 214: Polymaker PolyTerra PLA Charcoal Black, PLA, 620 g").assertExists()
        rule.onNodeWithText(
            "The code holds this Hub's home address. If the Hub is renamed or moves to another port, " +
                "old labels still open in this app's scanner but not from a phone's camera.",
        ).assertExists()
        rule.onNodeWithText("Print").assertExists()
        assertEquals(listOf("62x29"), asked)
        rule.onNodeWithText("50 x 30 mm").performClick()
        rule.waitForIdle()
        assertEquals(listOf("62x29", "50x30"), asked)
    }

    @Test fun aBadSizeRefusalShowsTheHubsOwnMessage() {
        val refusal = "The size must be 62x29, 50x30, a4-63x34, a4-70x37 or letter-66x25."
        show(LabelSource { throw ApiError(400, "bad_size", refusal) })
        rule.waitForIdle()
        rule.onNodeWithText("Could not draw the label. $refusal").assertExists()
    }

    @Test fun aRefusalShowsTheHubsTextAndTryAgainAsksAgain() {
        var calls = 0
        show(LabelSource { calls++; throw ApiError(404, "not_found", "No such spool.") })
        rule.waitForIdle()
        rule.onNodeWithText("Could not draw the label. No such spool.").assertExists()
        rule.onNodeWithText("Print").assertIsNotEnabled()
        rule.onNodeWithText("Try again").performClick()
        rule.waitForIdle()
        assertEquals(2, calls)
    }

    @Test fun aChangedCertificateShowsTheCertificateNoticeNotACrash() {
        show(LabelSource { throw FingerprintMismatch("aa", "bb") })
        rule.waitForIdle()
        rule.onNodeWithText("The Hub's certificate has changed. This phone stopped talking to it to stay safe.").assertExists()
        rule.onNodeWithText("Print").assertIsNotEnabled()
    }

    @Test fun printWaitsForTheDrawing() {
        show(LabelSource { throw ApiError(0, "offline", "down") })
        rule.waitForIdle()
        rule.onAllNodesWithText("Print").assertCountEquals(1)
        rule.onNodeWithText("Print").assertIsNotEnabled()
    }
}
