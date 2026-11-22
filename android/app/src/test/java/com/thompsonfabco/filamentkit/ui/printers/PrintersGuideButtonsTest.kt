package com.thompsonfabco.filamentkit.ui.printers

import android.os.Build
import androidx.activity.ComponentActivity
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import com.thompsonfabco.filamentkit.net.ApiError
import com.thompsonfabco.filamentkit.net.CommandResult
import com.thompsonfabco.filamentkit.net.KlipperFound
import com.thompsonfabco.filamentkit.net.Printer
import com.thompsonfabco.filamentkit.net.PrinterDetail
import com.thompsonfabco.filamentkit.net.UnloadResult
import com.thompsonfabco.filamentkit.ui.theme.Appearance
import com.thompsonfabco.filamentkit.ui.theme.Look
import com.thompsonfabco.filamentkit.ui.theme.SpoolwardenTheme
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/** The add guide has two address buttons, each naming its family, Bambu Lab first. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [Build.VERSION_CODES.S], qualifiers = "w411dp-h800dp")
class PrintersGuideButtonsTest {
    @get:Rule val rule = createAndroidComposeRule<ComponentActivity>()

    private class Backend : PrinterBackend {
        override suspend fun printers() = emptyList<Printer>()
        override suspend fun addPrinter(serial: String, accessCode: String, name: String?): Printer = error("not used")
        override suspend fun addKlipperPrinter(host: String, port: Int, name: String?): Printer = error("not used")
        override suspend fun scanKlipper(subnet: String?) = emptyList<KlipperFound>()
        override suspend fun setApiKey(id: String, key: String) = Unit
        override suspend fun printer(id: String): PrinterDetail = throw ApiError(404, "not_found", "x")
        override suspend fun command(id: String, action: String): CommandResult = error("not used")
        override suspend fun unload(id: String, label: String, dryRun: Boolean): UnloadResult = error("not used")
    }

    private val bambu = "Type a Bambu Lab printer's address"
    private val klipper = "Type a Klipper printer's address"

    private fun guide(): PrintersViewModel {
        val vm = PrintersViewModel(Backend())
        rule.setContent {
            SpoolwardenTheme(Look.Console, Appearance.Light) { PrintersScreen(vm, onOpen = {}, onPairAgain = {}) }
        }
        rule.waitForIdle()
        vm.openGuide()
        rule.waitForIdle()
        return vm
    }

    @Test fun bothButtonsAreShownBambuLabFirstWithNoFamilyChip() {
        guide()
        rule.onNodeWithText("None found. Type its address in the next step.").assertIsDisplayed()
        val b = rule.onNodeWithText(bambu).apply { performScrollTo() }.fetchSemanticsNode().boundsInRoot
        val k = rule.onNodeWithText(klipper).apply { performScrollTo() }.fetchSemanticsNode().boundsInRoot
        assertTrue("Bambu Lab comes first", b.top < k.top)
        assertEquals("no button of the old name", 0, rule.onAllNodesWithText("Type its address").fetchSemanticsNodes().size)
    }

    @Test fun theBambuLabButtonOpensTheBambuLabForm() {
        val vm = guide()
        rule.onNodeWithText(bambu).performScrollTo().performClick()
        rule.waitForIdle()
        assertNotNull(vm.state.value.dialog)
        assertEquals("", vm.state.value.dialog!!.serial)
        assertTrue(vm.state.value.dialog!!.guided)
        assertNull(vm.state.value.klipper)
        assertNull(vm.state.value.guide)
    }

    @Test fun theKlipperButtonOpensTheKlipperForm() {
        val vm = guide()
        rule.onNodeWithText(klipper).performScrollTo().performClick()
        rule.waitForIdle()
        assertNotNull(vm.state.value.klipper)
        assertTrue(vm.state.value.klipper!!.guided)
        assertNull(vm.state.value.dialog)
        assertNull(vm.state.value.guide)
    }
}
