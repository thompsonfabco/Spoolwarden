package com.thompsonfabco.filamentkit.ui.add

import android.os.Build
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import com.thompsonfabco.filamentkit.drafts.DraftStore
import com.thompsonfabco.filamentkit.net.ApiError
import com.thompsonfabco.filamentkit.net.ShareState
import com.thompsonfabco.filamentkit.ui.theme.Appearance
import com.thompsonfabco.filamentkit.ui.theme.Look
import com.thompsonfabco.filamentkit.ui.theme.SpoolwardenTheme
import java.io.File
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.junit.After
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/** The Share corrections line of Check the spools (05-android-app 10.6): only while it is on, only for spools read from photos. */
@OptIn(ExperimentalCoroutinesApi::class)
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [Build.VERSION_CODES.S], qualifiers = "w411dp-h800dp")
class AddShareLineTest {
    @get:Rule val rule = createComposeRule()
    private val dispatcher = UnconfinedTestDispatcher()
    private lateinit var dir: File
    private val api = FakeIntakeApi()
    private val line = "Share corrections is on: fields you change here will wait on the Hub to be shared once the spools are added."

    @Before fun setUp() {
        Dispatchers.setMain(dispatcher)
        dir = kotlin.io.path.createTempDirectory("drafts").toFile()
    }

    @After fun tearDown() {
        Dispatchers.resetMain()
        dir.deleteRecursively()
    }

    private fun vm() = AddViewModel(
        api = api, store = DraftStore(dir), taps = MutableSharedFlow(extraBufferCapacity = 8), draftId = null, initialTag = null,
        prepare = { it }, io = dispatcher, clock = { 1_000_000L },
    )

    private fun item() = buildJsonObject {
        put("photos", JsonArray(emptyList())); put("existing_filament_id", JsonNull); put("vendor", "SUNLU"); put("name", "PLA+ Grey")
        put("material", "PLA"); put("color_hex", "808080"); put("net", 1000); put("tare", 213); put("density", 1.24); put("count", 1)
        put("sealed", false)
    }

    private fun show(vm: AddViewModel, state: AddUiState) {
        rule.setContent {
            SpoolwardenTheme(Look.Console, Appearance.Light) {
                ReviewScreen(state, vm, onPairAgain = {}, onAddByHand = {}, onAddCatalogLine = { _, _, _ -> })
            }
        }
        rule.waitForIdle()
    }

    @Test fun theViewModelKnowsWhetherSharingIsOnAndAHubWithoutItIsNotOn() {
        val vm = vm()
        assertFalse(vm.state.value.shareOn)
        api.share = ShareState(saved = true)
        vm.loadShare()
        assertTrue(vm.state.value.shareOn)
        api.share = ShareState(saved = false)
        vm.loadShare()
        assertFalse(vm.state.value.shareOn)
        api.share = null
        vm.loadShare()
        assertFalse(vm.state.value.shareOn)
        api.share = ShareState(saved = true)
        api.shareFailure = ApiError(0, "offline", "down")
        vm.loadShare()
        assertFalse(vm.state.value.shareOn)
    }

    @Test fun theLineStandsUnderTheSummaryWhileSharingIsOn() {
        val vm = vm()
        show(vm, AddUiState(stage = Stage.Review, items = listOf(item()), shareOn = true))
        rule.onNodeWithText(line).assertExists()
    }

    @Test fun noLineWhileSharingIsOffOrTheHubHasNoSharing() {
        val vm = vm()
        show(vm, AddUiState(stage = Stage.Review, items = listOf(item()), shareOn = false))
        rule.onNodeWithText(line).assertDoesNotExist()
    }

    @Test fun noLineWhenTheSpoolsWereNotReadFromPhotos() {
        val vm = vm()
        show(vm, AddUiState(stage = Stage.Review, items = listOf(item()), shareOn = true, manual = true))
        rule.onNodeWithText(line).assertDoesNotExist()
    }
}
