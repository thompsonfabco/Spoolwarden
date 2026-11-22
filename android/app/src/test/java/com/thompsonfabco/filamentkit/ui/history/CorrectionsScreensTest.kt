package com.thompsonfabco.filamentkit.ui.history

import android.os.Build
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.onAllNodesWithContentDescription
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import com.thompsonfabco.filamentkit.net.ApiError
import com.thompsonfabco.filamentkit.net.CorrectionEntry
import com.thompsonfabco.filamentkit.net.CorrectionsWaiting
import com.thompsonfabco.filamentkit.net.HistoryRecord
import com.thompsonfabco.filamentkit.net.HistorySaved
import com.thompsonfabco.filamentkit.net.HistorySpool
import com.thompsonfabco.filamentkit.net.IntakeHistory
import com.thompsonfabco.filamentkit.net.NotQueuedCorrection
import com.thompsonfabco.filamentkit.net.RecordShare
import com.thompsonfabco.filamentkit.net.RevisionProposal
import com.thompsonfabco.filamentkit.net.ShareState
import com.thompsonfabco.filamentkit.ui.theme.Appearance
import com.thompsonfabco.filamentkit.ui.theme.Look
import com.thompsonfabco.filamentkit.ui.theme.SpoolwardenTheme
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/** The Share corrections lines and Corrections waiting (05-android-app 10.6) as the person reads them, with fake data. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [Build.VERSION_CODES.S], qualifiers = "w411dp-h8000dp")
class CorrectionsScreensTest {
    @get:Rule val rule = createComposeRule()

    private val spool = HistorySpool(214, 41, "Polymaker", "PolyTerra PLA", "PLA")
    private val values = buildJsonObject { put("vendor", "Polymaker"); put("name", "PolyTerra PLA"); put("material", "PLA") }
    private fun record(photos: Int = 1, share: RecordShare? = null) = HistoryRecord(
        id = "a1", photos = photos, spools = listOf(spool), saved = listOf(HistorySaved(0, listOf(214), values)), share = share,
    )

    private class History(var rec: HistoryRecord, var share: ShareState?) : HistoryBackend {
        override suspend fun list() = IntakeHistory(20, 0, null, true)
        override suspend fun record(id: String) = rec
        override suspend fun photo(id: String, n: Int) = ByteArray(0)
        override suspend fun revise(id: String, note: String, item: Int?) = RevisionProposal(
            "p-1", 0, buildJsonObject { put("vendor", "Polymaker") }, buildJsonObject { put("vendor", "Polymaker Ltd") }, listOf("vendor"),
        )
        override suspend fun applyProposal(id: String, proposal: String) = rec
        override suspend fun applyValues(id: String, item: Int, values: JsonObject) = rec
        override suspend fun undo(id: String, revision: Int) = rec
        override suspend fun share() = share
    }

    private fun theme(content: @androidx.compose.runtime.Composable () -> Unit) =
        rule.setContent { SpoolwardenTheme(Look.Console, Appearance.Light) { content() } }

    private val on = ShareState(saved = true, count = 3)
    private val willWait = "Share corrections is on: this correction will wait on the Hub to be shared."

    // ---- Recently added ----

    @Test fun recentlyAddedShowsTheLineAndOpensTheScreenWhileSharingIsOn() {
        var opened = 0
        val vm = HistoryListViewModel(History(record(), on))
        theme { HistoryListScreen(vm, null, onOpen = {}, onPairAgain = {}, onOpenCorrections = { opened++ }) }
        rule.waitForIdle()
        rule.onNodeWithText("Share corrections is on · 3 waiting").performClick()
        assertEquals(1, opened)
    }

    private fun recentlyAddedWith(share: ShareState?) {
        val vm = HistoryListViewModel(History(record(), share))
        theme { HistoryListScreen(vm, null, onOpen = {}, onPairAgain = {}) }
        rule.waitForIdle()
        rule.onNodeWithText("Share corrections is on", substring = true).assertDoesNotExist()
    }

    @Test fun recentlyAddedShowsNothingWhileSharingIsOff() = recentlyAddedWith(ShareState(saved = false))

    @Test fun recentlyAddedShowsNothingWhileTheReaderCannotKeepCorrections() = recentlyAddedWith(ShareState(saved = true, reason = "off"))

    @Test fun recentlyAddedShowsNothingFromAHubThatHasNone() = recentlyAddedWith(null)

    // ---- Edit by hand and a proposal ----

    @Test fun editByHandSaysTheCorrectionWillWaitOnlyWhileSharingIsOn() {
        val vm = RecordViewModel(History(record(), on), "a1")
        theme { EditScreen(vm, item = 0, onClose = {}, onPairAgain = {}) }
        rule.waitForIdle()
        rule.onNodeWithText(willWait).assertExists()
    }

    @Test fun editByHandSaysNothingWhileSharingIsOff() {
        val vm = RecordViewModel(History(record(), ShareState(saved = false)), "a1")
        theme { EditScreen(vm, item = 0, onClose = {}, onPairAgain = {}) }
        rule.waitForIdle()
        rule.onNodeWithText("Save changes").assertExists()
        rule.onNodeWithText(willWait).assertDoesNotExist()
    }

    private fun recordScreen(fake: History, onOpenCorrections: () -> Unit = {}): RecordViewModel {
        val list = HistoryListViewModel(fake)
        val vm = RecordViewModel(fake, "a1", settleMs = 1)
        theme {
            RecordScreen(
                vm, list, null, onBack = {}, onOpenRecord = {}, onEdit = {}, onOpenPhoto = {}, onOpenSpool = {}, onPairAgain = {},
                onOpenCorrections = onOpenCorrections,
            )
        }
        rule.waitForIdle()
        return vm
    }

    @Test fun aProposalSaysItWillWaitAboveAcceptAndTheAcceptedResultSaysItIsWaiting() {
        var opened = 0
        val vm = recordScreen(History(record(share = RecordShare("waiting", null, 1)), on)) { opened++ }
        vm.ask("brand is Polymaker")
        rule.waitForIdle()
        rule.onNodeWithText("Accept").assertExists()
        rule.onNodeWithText(willWait).assertExists()
        vm.accept()
        rule.waitForIdle()
        rule.onNodeWithText("This correction is waiting to be shared.").assertExists()
        rule.onNodeWithText("Corrections waiting").performClick()
        assertEquals(1, opened)
    }

    @Test fun aProposalSaysNothingAboutSharingWhileItIsOff() {
        val vm = recordScreen(History(record(share = RecordShare("none", null, 0)), ShareState(saved = false)))
        vm.ask("brand is Polymaker")
        rule.waitForIdle()
        rule.onNodeWithText("Accept").assertExists()
        rule.onNodeWithText(willWait).assertDoesNotExist()
        vm.accept()
        rule.waitForIdle()
        rule.onNodeWithText("This correction is waiting to be shared.").assertDoesNotExist()
        rule.onNodeWithText("Corrections waiting").assertDoesNotExist()
    }

    @Test fun aSavedEditWithNoKeptPhotoSaysItWasNotShared() {
        val vm = recordScreen(History(record(photos = 0, share = RecordShare("none", null, 0)), on))
        vm.startEdit(0)
        vm.setText(RecentField.Vendor, "Polymaker Ltd")
        vm.saveEdit {}
        rule.waitForIdle()
        rule.onNodeWithText("Not shared: no photo was kept for this record.").assertExists()
    }

    @Test fun aSavedEditThatTheReaderDidNotTakeSaysWhyInTheHubsWords() {
        val reason = "the Label Reader did not answer: down"
        val vm = recordScreen(History(record(share = RecordShare("not_queued", reason, 0)), on))
        vm.startEdit(0)
        vm.setText(RecentField.Vendor, "Polymaker Ltd")
        vm.saveEdit {}
        rule.waitForIdle()
        rule.onNodeWithText("Saved. The correction is not waiting to be shared: $reason.").assertExists()
    }

    // ---- Corrections waiting ----

    private class Corrections(var waiting: () -> CorrectionsWaiting, var share: ShareState? = null) : CorrectionsBackend {
        val removed = mutableListOf<String>()
        override suspend fun waiting() = waiting.invoke()
        override suspend fun remove(id: String) { removed += id }
        override suspend fun share() = share
    }

    private fun entry(id: String, record: String?, source: String = "local", photos: Int = 2) = CorrectionEntry(
        id, "2026-11-12T14:09:00+00:00",
        buildJsonObject { put("vendor", "Polymakr"); put("name", "PolyTerra PLA"); put("color_hex", "2B2B2E") },
        buildJsonObject { put("vendor", "Polymaker"); put("name", "PolyTerra PLA"); put("color_hex", "1F1F22") },
        "brand is Polymaker", photos, record, source, null,
    )

    private fun screen(
        fake: Corrections,
        onOpenRecord: (String) -> Unit = {},
        onOpenPhoto: (String, Int) -> Unit = { _, _ -> },
    ): CorrectionsViewModel {
        val vm = CorrectionsViewModel(fake)
        theme { CorrectionsScreen(vm, null, onOpenRecord, onOpenPhoto, onPairAgain = {}) }
        rule.waitForIdle()
        return vm
    }

    @Test fun theScreenShowsTheHeadTheRowsRemoveAndTheFoot() {
        val opened = mutableListOf<String>()
        screen(
            Corrections({ CorrectionsWaiting(2, 3_565_000, listOf(entry("fb-1", "r1"), entry("fb-2", null, "cloud", photos = 0)), emptyList()) }, on),
            opened::add,
        )
        rule.onNodeWithText("2 waiting · 3.4 MB").assertExists()
        rule.onNodeWithText("Nothing is sent until Send now is pressed on the Hub page. Remove any you do not want to share.").assertExists()
        rule.onAllNodesWithContentDescription("Brand, Polymakr, becomes Polymaker").assertCountEquals(2)
        rule.onAllNodesWithContentDescription("Colour, #2B2B2E, becomes #1F1F22").assertCountEquals(2)
        rule.onAllNodesWithText("Note: brand is Polymaker").assertCountEquals(2)
        // the time is shown in the phone's zone, so only the engine in the spoken headline is checked here
        rule.onNodeWithContentDescription("read by On this Hub", substring = true).assertExists()
        rule.onNodeWithContentDescription("read by Cloud (Claude)", substring = true).assertExists()
        rule.onAllNodesWithContentDescription("Remove the correction for Polymaker PolyTerra PLA").assertCountEquals(2)
        rule.onNodeWithText("Sending is done on the Hub page, under Settings > Label Reader.").assertExists()
        // the record that is still kept opens; the one the Hub sent no record for is gone, and says so with its photos
        rule.onNodeWithText("This record is no longer kept.").assertExists()
        rule.onNodeWithText("The photos of this correction are no longer on the Hub.").assertExists()
    }

    @Test fun theHeadlineOpensTheRecordThatIsKept() {
        val opened = mutableListOf<String>()
        screen(Corrections({ CorrectionsWaiting(1, 10, listOf(entry("fb-1", "r1.1")), emptyList()) }, on), opened::add)
        rule.onNodeWithText("Polymaker PolyTerra PLA").performClick()
        assertEquals(listOf("r1"), opened)
    }

    @Test fun thePhotosOfAKeptRecordOpenTheViewerOnThatRecord() {
        val opened = mutableListOf<Pair<String, Int>>()
        screen(
            Corrections({ CorrectionsWaiting(1, 10, listOf(entry("fb-1", "r1.1")), emptyList()) }, on),
            onOpenPhoto = { id, n -> opened += id to n },
        )
        rule.onNodeWithContentDescription("Photos of the correction for Polymaker PolyTerra PLA: 2").performClick()
        assertEquals(listOf("r1" to 1), opened)
    }

    @Test fun aRecordThatIsGoneKeepsTheThumbnailOnlyAndSaysThePhotosAreGone() {
        val opened = mutableListOf<Pair<String, Int>>()
        screen(
            Corrections({ CorrectionsWaiting(1, 10, listOf(entry("fb-9", null, photos = 0)), emptyList()) }, on),
            onOpenPhoto = { id, n -> opened += id to n },
        )
        rule.onNodeWithText("The photos of this correction are no longer on the Hub.").assertExists()
        rule.onNodeWithContentDescription("Photos of the correction for Polymaker PolyTerra PLA: 2").assertDoesNotExist()
        assertEquals(emptyList<Pair<String, Int>>(), opened)
    }

    @Test fun removeSendsTheRemoveAndTheRowLeaves() {
        val fake = Corrections({ CorrectionsWaiting(1, 10, listOf(entry("fb-1", "r1")), emptyList()) }, on)
        screen(fake)
        rule.onNodeWithText("Remove").performClick()
        rule.waitForIdle()
        assertEquals(listOf("fb-1"), fake.removed)
    }

    @Test fun withSharingOffTheHeadSaysNoNewCorrectionsAreAdded() {
        screen(Corrections({ CorrectionsWaiting(1, 10, listOf(entry("fb-1", "r1")), emptyList()) }, ShareState(saved = false)))
        rule.onNodeWithText("Share corrections is off, so no new corrections are added. These are still waiting.").assertExists()
    }

    @Test fun theEmptyState() {
        screen(Corrections({ CorrectionsWaiting(0, 0, emptyList(), emptyList()) }, on))
        rule.onNodeWithText("No corrections waiting").assertExists()
        rule.onNodeWithText("Corrections you make in Recently added, or while adding spools, appear here.").assertExists()
        rule.onNodeWithText("Remove").assertDoesNotExist()
    }

    @Test fun theReaderNotAnsweringShowsItsNoticeAndTryAgainAsksAgain() {
        var calls = 0
        screen(Corrections({
            calls++
            throw ApiError(503, "unreachable", "The reader does not answer, so the corrections waiting cannot be reached. They are kept.")
        }, on))
        rule.onNodeWithText("The reader does not answer, so the corrections waiting cannot be shown. They are kept.").assertExists()
        val before = calls
        rule.onNodeWithText("Try again").performClick()
        rule.waitForIdle()
        assertEquals(before + 1, calls)
    }

    @Test fun aRecordTheReaderDidNotTakeIsShownWithTheWarningAndNoRemove() {
        screen(
            Corrections({
                CorrectionsWaiting(
                    0, 0, emptyList(),
                    listOf(NotQueuedCorrection("r9", "2026-11-15T11:00:00+00:00", "SUNLU PLA+ Grey", "the Label Reader did not answer: down")),
                )
            }, on),
        )
        rule.onNodeWithText("Not waiting: the reader did not take this correction. the Label Reader did not answer: down").assertExists()
        rule.onNodeWithText("SUNLU PLA+ Grey").assertExists()
        rule.onNodeWithText("Remove").assertDoesNotExist()
        // not the empty state: something is listed
        rule.onNodeWithText("No corrections waiting").assertDoesNotExist()
    }

    @Test fun aRecordTheReaderDidNotTakeOpensItsRecord() {
        val opened = mutableListOf<String>()
        screen(
            Corrections({
                CorrectionsWaiting(
                    0, 0, emptyList(),
                    listOf(NotQueuedCorrection("r9.1", "2026-11-15T11:00:00+00:00", "SUNLU PLA+ Grey", "down")),
                )
            }, on),
            opened::add,
        )
        rule.onNodeWithText("SUNLU PLA+ Grey").performClick()
        assertEquals(listOf("r9"), opened)
    }

    @Test fun aRowWithoutAnEngineIsSpokenWithoutReadBy() {
        screen(
            Corrections({
                CorrectionsWaiting(1, 10, listOf(entry("fb-1", "r1").copy(source = null)), emptyList())
            }, on),
        )
        rule.onNodeWithContentDescription("read by", substring = true).assertDoesNotExist()
        rule.onNodeWithContentDescription("(empty)", substring = true).assertDoesNotExist()
    }
}
