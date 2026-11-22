package com.thompsonfabco.filamentkit.ui.pair

import android.os.Build
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithText
import com.thompsonfabco.filamentkit.net.ApiError
import com.thompsonfabco.filamentkit.net.FingerprintMismatch
import com.thompsonfabco.filamentkit.ui.theme.Appearance
import com.thompsonfabco.filamentkit.ui.theme.Look
import com.thompsonfabco.filamentkit.ui.theme.SpoolwardenTheme
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/** Scan a label (05-android-app 4.4 label mode): which codes are labels, what the Hub's answer means, the label texts. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [Build.VERSION_CODES.S], qualifiers = "w411dp-h8000dp")
class LabelScanTest {
    @get:Rule val rule = createComposeRule()

    @Test fun onlyTheNumberAfterSlashSCounts() {
        assertEquals(214, labelSpoolNumber("https://hub.local:8710/s/214"))
        assertEquals("the host is ignored", 214, labelSpoolNumber("https://192.0.2.10/s/214"))
        assertEquals(7, labelSpoolNumber("  http://other.example/s/7/ \n"))
        assertEquals(7, labelSpoolNumber("HTTPS://HUB/S/7?x=1"))
        assertEquals(12, labelSpoolNumber("https://[fe80::1]:8710/s/12"))
    }

    @Test fun anythingElseIsNotALabel() {
        assertNull(labelSpoolNumber(""))
        assertNull(labelSpoolNumber("214"))
        assertNull(labelSpoolNumber("/s/214"))
        assertNull(labelSpoolNumber("https://hub.local/s/"))
        assertNull(labelSpoolNumber("https://hub.local/s/abc"))
        assertNull(labelSpoolNumber("https://hub.local/s/214x"))
        assertNull(labelSpoolNumber("https://hub.local/s/0"))
        assertNull(labelSpoolNumber("https://hub.local/s/-3"))
        assertNull(labelSpoolNumber("https://hub.local/s/1234567890123"))
        assertNull(labelSpoolNumber("https://hub.local/x/s/214"))
        assertNull(labelSpoolNumber("spoolwarden://pair?host=hub.local"))
        assertNull(labelSpoolNumber("https://hub.local/s/214 and more"))
    }

    @Test fun theHubsAnswerIsFoundNotFoundOrItsOwnText() = runBlocking {
        assertEquals(LabelLookup.Found, lookupLabelSpool { })
        assertEquals(LabelLookup.NotFound, lookupLabelSpool { throw ApiError(404, "not_found", "No such spool.") })
        assertEquals(LabelLookup.Failed("down"), lookupLabelSpool { throw ApiError(0, "offline", "down") })
        assertEquals(LabelLookup.Failed("Nope."), lookupLabelSpool { throw ApiError(403, "forbidden", "Nope.") })
    }

    @Test fun aChangedCertificateAndAnyOtherFailureAreToldNotThrown() = runBlocking {
        assertEquals(LabelLookup.CertificateChanged, lookupLabelSpool { throw FingerprintMismatch("aa", "bb") })
        assertEquals(LabelLookup.Failed("boom"), lookupLabelSpool { throw IllegalStateException("boom") })
    }

    private fun frame(state: ScannerState, labelMode: Boolean, notice: String? = null) {
        rule.setContent {
            SpoolwardenTheme(Look.Console, Appearance.Dark) {
                ScannerFrame(state, onClose = {}, onTryAgain = {}, onEnterAddress = {}, labelMode = labelMode, notice = notice) {
                    Box(Modifier.fillMaxSize())
                }
            }
        }
    }

    @Test fun labelModeHasItsOwnHintAndNoticeOverThePreview() {
        frame(ScannerState.Scanning, labelMode = true, notice = LabelScan.NOT_LABEL_TEXT)
        rule.onNodeWithText("Point the camera at the spool's label").assertExists()
        rule.onNodeWithText("This is not a Spoolwarden spool label.").assertExists()
        rule.onAllNodesWithText("Point the camera at the QR code").assertCountEquals(0)
    }

    @Test fun pairingModeKeepsItsHint() {
        frame(ScannerState.Scanning, labelMode = false)
        rule.onNodeWithText("Point the camera at the QR code").assertExists()
        rule.onAllNodesWithText("Point the camera at the spool's label").assertCountEquals(0)
    }

    @Test fun labelModeProblemsHaveTheLabelTextsAndNoAddressStep() {
        frame(ScannerState.Denied, labelMode = true)
        rule.onNodeWithText(LabelScan.DENIED).assertExists()
        rule.onNodeWithText("Try again").assertExists()
        rule.onAllNodesWithText("Enter the address instead").assertCountEquals(0)
    }

    @Test fun labelModeCameraFailedIsTextOnly() {
        frame(ScannerState.CameraFailed, labelMode = true)
        rule.onNodeWithText(LabelScan.FAILED).assertExists()
        rule.onAllNodesWithText("Enter the address instead").assertCountEquals(0)
        rule.onAllNodesWithText("Try again").assertCountEquals(0)
    }
}
