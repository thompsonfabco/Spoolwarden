package com.thompsonfabco.filamentkit.ui.search

import android.os.Build
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import com.thompsonfabco.filamentkit.ui.theme.Appearance
import com.thompsonfabco.filamentkit.ui.theme.Look
import com.thompsonfabco.filamentkit.ui.theme.SpoolwardenTheme
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/** The Scan a label button of the Spools screen (05-android-app 7.1): under the tag hint, off while searching. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [Build.VERSION_CODES.S], qualifiers = "w411dp-h8000dp")
class ScanLabelButtonTest {
    @get:Rule val rule = createComposeRule()

    private fun show(vm: SearchViewModel, onScan: (() -> Unit)?) = rule.setContent {
        SpoolwardenTheme(Look.Console, Appearance.Light) {
            SearchScreen(vm = vm, onOpenSpool = {}, onPairAgain = {}, nfc = NfcState.Ready, onScanLabel = onScan)
        }
    }

    @Test fun theButtonOpensTheScanner() {
        var opened = 0
        show(SearchViewModel { _, _ -> emptyList() }) { opened++ }
        rule.onNodeWithText("Scan a label").performClick()
        assertEquals(1, opened)
    }

    @Test fun noCallbackNoButton() {
        show(SearchViewModel { _, _ -> emptyList() }, null)
        rule.onAllNodesWithText("Scan a label").assertCountEquals(0)
    }

    @Test fun typingAQueryHidesItWithTheTagHint() {
        val vm = SearchViewModel { _, _ -> emptyList() }
        vm.setQuery("pla")
        show(vm) {}
        rule.onAllNodesWithText("Scan a label").assertCountEquals(0)
    }
}
