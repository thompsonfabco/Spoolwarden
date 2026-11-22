package com.thompsonfabco.filamentkit.ui.history

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
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/** Corrections waiting and the Share corrections lines (05-android-app 10.6), on fake data. */
@OptIn(ExperimentalCoroutinesApi::class)
class CorrectionsTest {
    private val dispatcher = StandardTestDispatcher()

    @Before fun setUp() = Dispatchers.setMain(dispatcher)
    @After fun tearDown() = Dispatchers.resetMain()

    private fun entry(id: String, record: String?, source: String? = "local", corrected: JsonObject = buildJsonObject { put("vendor", "Polymaker") }) =
        CorrectionEntry(
            id, "2026-11-15T12:00:00Z", buildJsonObject { put("vendor", "Polymakr") }, corrected, "brand is Polymaker", 2, record, source,
            "/9j/4AAQ",
        )

    private class Fake : CorrectionsBackend {
        var list = CorrectionsWaiting(0, 0, emptyList(), emptyList())
        var share: ShareState? = ShareState(saved = true, count = 0)
        var waitingError: Exception? = null
        var removeError: Exception? = null
        val removed = mutableListOf<String>()
        override suspend fun waiting(): CorrectionsWaiting {
            waitingError?.let { throw it }
            return list
        }
        override suspend fun remove(id: String) {
            removeError?.let { throw it }
            removed += id
            list = list.copy(entries = list.entries.filter { it.id != id }, count = list.count - 1, bytes = list.bytes / 2)
        }
        override suspend fun share() = share
    }

    private val fake = Fake()

    private fun loaded(vararg entries: CorrectionEntry, bytes: Long = 3_565_000): CorrectionsViewModel {
        fake.list = CorrectionsWaiting(entries.size, bytes, entries.toList(), emptyList())
        return CorrectionsViewModel(fake).also { it.refresh(); dispatcher.scheduler.runCurrent() }
    }

    // ---- the rows ----

    @Test fun aRowNamesTheFilamentTheReaderAndTheChangedFieldsInTheRecordsOrder() {
        val read = buildJsonObject {
            put("color_hex", "2B2B2E"); put("vendor", "Polymakr"); put("name", "PolyTerra PLA"); put("density", 1.24)
        }
        val now = buildJsonObject {
            put("color_hex", "1F1F22"); put("vendor", "Polymaker"); put("name", "PolyTerra PLA"); put("density", 1.24)
        }
        val e = entry("fb-1", "0123456789ab", "cloud", now).copy(predicted = read)
        val row = rowOf(e)
        assertEquals("Polymaker PolyTerra PLA", row.filament)
        assertEquals(Engine.Cloud, row.engine)
        // brand before colour (the record's order); the name and the density did not change
        assertEquals(listOf("vendor", "color_hex"), row.changes.map { it.field })
        assertEquals(JsonPrimitive("Polymakr"), row.changes[0].read)
        assertEquals(JsonPrimitive("Polymaker"), row.changes[0].now)
        assertEquals("brand is Polymaker", row.note)
        assertEquals(2, row.photos)
        assertNotNull(row.thumbnail)
        assertEquals(true, row.recordKept)
    }

    @Test fun twoValuesThatReadTheSameAreNoChange() {
        val e = entry("fb-1", "r1").copy(
            predicted = buildJsonObject { put("tare", 140.0); put("vendor", "Polymaker") },
            corrected = buildJsonObject { put("tare", 140); put("vendor", "Polymaker Ltd") },
        )
        assertEquals(listOf("vendor"), rowOf(e).changes.map { it.field })
    }

    @Test fun aLaterItemsKeyBelongsToItsRecordAndARecordNotKeptIsNoLink() {
        assertEquals("0123456789ab", recordIdOf("0123456789ab.1"))
        assertEquals("0123456789ab", recordIdOf("0123456789ab"))
        assertNull(recordIdOf(null))
        assertNull(recordIdOf(""))
        assertEquals(true, rowOf(entry("a", "r1.2")).recordKept)
    }

    @Test fun anEntryWithNoRecordIsOneWhoseRecordIsGone() {
        // the Hub leaves `record` out and sends `photos` 0 when the record is gone
        val row = rowOf(entry("a", null).copy(photos = 0))
        assertEquals(false, row.recordKept)
        assertNull(row.record)
        assertEquals(0, row.photos)
        assertNotNull(row.thumbnail)
    }

    @Test fun anEntryWithoutAnEngineOrAThumbnailStillMakesARow() {
        val row = rowOf(entry("a", "r1", source = null).copy(thumbnail = null, photos = 0, note = " "))
        assertNull(row.engine)
        assertNull(row.thumbnail)
        assertNull(row.note)
    }

    // ---- the screen's state ----

    @Test fun theListShowsTheCountTheSizeAndTheRows() = runTest(dispatcher) {
        val vm = loaded(entry("fb-1", "r1"), entry("fb-2", "r2"))
        val s = vm.state.value
        assertTrue(s.loaded)
        assertEquals(listOf("fb-1", "fb-2"), s.rows.map { it.id })
        assertEquals(2, s.count)
        assertEquals("3.4", RecentValues.megabytes(s.bytes))
        assertFalse(s.sharingOff)
    }

    @Test fun anEmptyListIsLoadedAndEmpty() = runTest(dispatcher) {
        val s = loaded().state.value
        assertTrue(s.loaded && s.rows.isEmpty() && s.error == null && !s.readerDown)
    }

    @Test fun withSharingOffAndCorrectionsLeftTheHeadSaysSo() = runTest(dispatcher) {
        fake.share = ShareState(saved = false, actual = false, count = 1)
        assertTrue(loaded(entry("fb-1", "r1")).state.value.sharingOff)
    }

    @Test fun theReaderNotAnsweringIsItsOwnStateWithTheCorrectionsKept() = runTest(dispatcher) {
        fake.waitingError = ApiError(503, "unreachable", "The reader does not answer, so the corrections waiting cannot be reached. They are kept.")
        val vm = CorrectionsViewModel(fake)
        vm.refresh()
        runCurrent()
        val s = vm.state.value
        assertTrue(s.readerDown)
        assertNull(s.error)
        assertFalse(s.loaded)
        // Try again
        fake.waitingError = null
        fake.list = CorrectionsWaiting(1, 10, listOf(entry("fb-1", "r1")), emptyList())
        vm.refresh()
        runCurrent()
        assertFalse(vm.state.value.readerDown)
        assertEquals(1, vm.state.value.rows.size)
    }

    @Test fun otherFailuresAreTheHubsWordsOrTheStatesOf32() = runTest(dispatcher) {
        fake.waitingError = ApiError(409, "no_reader", "The Label Reader: On this Hub is not on, so there are no corrections waiting.")
        val vm = CorrectionsViewModel(fake)
        vm.refresh()
        runCurrent()
        assertEquals(RecentFailure.Hub("The Label Reader: On this Hub is not on, so there are no corrections waiting."), vm.state.value.error)
        fake.waitingError = ApiError(0, "offline", "no route")
        vm.refresh()
        runCurrent()
        assertTrue(vm.state.value.offline)
        fake.waitingError = ApiError(401, "unauthorized", "no")
        vm.refresh()
        runCurrent()
        assertTrue(vm.state.value.unauthorized)
    }

    @Test fun aReaderThatRefusesTheHubsTokenIsTheHubsSentenceNotAnUnpairedPhone() = runTest(dispatcher) {
        val said = "The reader did not accept the Hub's token. Set the token again in Settings > Label Reader."
        fake.waitingError = ApiError(502, "unauthorized", said)
        val vm = CorrectionsViewModel(fake)
        vm.refresh()
        runCurrent()
        assertFalse(vm.state.value.unauthorized)
        assertEquals(RecentFailure.Hub(said), vm.state.value.error)
    }

    @Test fun aFailedRemoveFromAReaderThatRefusesTheTokenOrIsDownSaysTheHubsWords() = runTest(dispatcher) {
        val vm = loaded(entry("fb-1", "r1"))
        val said = "The reader did not accept the Hub's token. Set the token again in Settings > Label Reader."
        fake.removeError = ApiError(502, "unauthorized", said)
        vm.remove("fb-1")
        runCurrent()
        assertFalse(vm.state.value.unauthorized)
        assertEquals(RecentFailure.Hub(said), vm.state.value.removeFailure)
        val down = "The reader does not answer, so the corrections waiting cannot be reached. They are kept."
        fake.removeError = ApiError(503, "unreachable", down)
        vm.remove("fb-1")
        runCurrent()
        // not "the cloud service is down"
        assertEquals(RecentFailure.Hub(down), vm.state.value.removeFailure)
    }

    @Test fun theHubsOwn401IsThePhonesPairingForRemove() = runTest(dispatcher) {
        val vm = loaded(entry("fb-1", "r1"))
        fake.removeError = ApiError(401, "unauthorized", "no")
        vm.remove("fb-1")
        runCurrent()
        assertTrue(vm.state.value.unauthorized)
        assertNull(vm.state.value.removeFailure)
    }

    @Test fun aRemoveFailureClearsWhenTheListLoadsAgain() = runTest(dispatcher) {
        val vm = loaded(entry("fb-1", "r1"))
        fake.removeError = ApiError(502, "reader_error", "The reader refused: no")
        vm.remove("fb-1")
        runCurrent()
        assertNotNull(vm.state.value.removeFailure)
        vm.refresh()
        runCurrent()
        assertNull(vm.state.value.removeFailure)
    }

    @Test fun aRefreshWhileARemoveRunsKeepsItsButtonBusyAndItIsNotSentTwice() = runTest(dispatcher) {
        val gate = kotlinx.coroutines.CompletableDeferred<Unit>()
        val slow = object : CorrectionsBackend by fake {
            override suspend fun remove(id: String) {
                gate.await()
                fake.remove(id)
            }
        }
        fake.list = CorrectionsWaiting(1, 10, listOf(entry("fb-2", "r2")), emptyList())
        val vm = CorrectionsViewModel(slow).also { it.refresh(); runCurrent() }
        vm.remove("fb-2")
        vm.refresh()
        runCurrent()
        assertTrue(vm.state.value.rows.single().removing)
        vm.remove("fb-2")
        gate.complete(Unit)
        runCurrent()
        assertEquals(listOf("fb-2"), fake.removed)
    }

    @Test fun theListAndTheRecordShowBeforeTheReaderHasAnsweredAboutSharing() = runTest(dispatcher) {
        val gate = kotlinx.coroutines.CompletableDeferred<ShareState?>()
        val backend = object : HistoryBackend {
            override suspend fun list() = IntakeHistory(20, 0, null, true)
            override suspend fun record(id: String) = record()
            override suspend fun photo(id: String, n: Int) = ByteArray(0)
            override suspend fun revise(id: String, note: String, item: Int?): RevisionProposal = throw UnsupportedOperationException()
            override suspend fun applyProposal(id: String, proposal: String) = record()
            override suspend fun applyValues(id: String, item: Int, values: JsonObject) = record()
            override suspend fun undo(id: String, revision: Int) = record()
            override suspend fun share() = gate.await()
        }
        val list = HistoryListViewModel(backend)
        list.refresh()
        runCurrent()
        assertNotNull(list.state.value.history)
        assertNull(list.state.value.share)
        val rec = RecordViewModel(backend, "a1")
        rec.load()
        runCurrent()
        assertNotNull(rec.state.value.record)
        assertNull(rec.state.value.share)
        gate.complete(ShareState(saved = true, count = 1))
        runCurrent()
        assertEquals(true, list.state.value.share?.on)
        assertEquals(true, rec.state.value.share?.on)
    }

    @Test fun theRecordsTheReaderDidNotTakeAreListedAndNotCounted() = runTest(dispatcher) {
        fake.list = CorrectionsWaiting(
            0, 0, emptyList(),
            listOf(NotQueuedCorrection("r9", "2026-11-15T11:00:00-05:00", "SUNLU PLA+ Grey", "the Label Reader did not answer: down")),
        )
        val vm = CorrectionsViewModel(fake)
        vm.refresh()
        runCurrent()
        val n = vm.state.value.notTaken.single()
        assertEquals("SUNLU PLA+ Grey", n.filament)
        assertEquals("the Label Reader did not answer: down", n.reason)
        assertEquals(0, vm.state.value.count)
    }

    // ---- Remove ----

    @Test fun removeTakesTheRowOutShowsRemovingAndAsksForTheToast() = runTest(dispatcher) {
        val vm = loaded(entry("fb-1", "r1"), entry("fb-2", "r2"))
        vm.remove("fb-1")
        assertTrue(vm.state.value.rows.first { it.id == "fb-1" }.removing)
        runCurrent()
        assertEquals(listOf("fb-1"), fake.removed)
        val s = vm.state.value
        assertEquals(listOf("fb-2"), s.rows.map { it.id })
        assertEquals(1, s.count)
        assertTrue(s.removed)
        vm.removedShown()
        assertFalse(vm.state.value.removed)
    }

    @Test fun removeWhileItRunsIsNotSentTwice() = runTest(dispatcher) {
        val vm = loaded(entry("fb-1", "r1"))
        vm.remove("fb-1")
        vm.remove("fb-1")
        runCurrent()
        assertEquals(listOf("fb-1"), fake.removed)
    }

    @Test fun aFailedRemoveKeepsTheRowAndSaysWhy() = runTest(dispatcher) {
        val vm = loaded(entry("fb-1", "r1"))
        fake.removeError = ApiError(502, "reader_error", "The reader refused: no")
        vm.remove("fb-1")
        runCurrent()
        val s = vm.state.value
        assertEquals(listOf("fb-1"), s.rows.map { it.id })
        assertFalse(s.rows.single().removing)
        assertEquals(RecentFailure.Hub("The reader refused: no"), s.removeFailure)
        assertFalse(s.removed)
    }

    @Test fun aCorrectionGoneAlreadyIsSaidAndTheListReloads() = runTest(dispatcher) {
        val vm = loaded(entry("fb-1", "r1"), entry("fb-2", "r2"))
        fake.list = CorrectionsWaiting(1, 10, listOf(entry("fb-2", "r2")), emptyList())
        fake.removeError = ApiError(404, "not_found", "That correction is not waiting any more.")
        vm.remove("fb-1")
        runCurrent()
        val s = vm.state.value
        assertEquals(listOf("fb-2"), s.rows.map { it.id })
        assertEquals(RecentFailure.Hub("That correction is not waiting any more."), s.removeFailure)
    }

    // ---- the lines in the other places ----

    private val spool = HistorySpool(214, 41, "Polymaker", "PolyTerra PLA", "PLA")
    private val values = buildJsonObject { put("vendor", "Polymaker"); put("name", "PolyTerra PLA"); put("material", "PLA") }

    private fun record(photos: Int = 1, share: RecordShare? = null) = HistoryRecord(
        id = "a1", photos = photos, spools = listOf(spool), saved = listOf(HistorySaved(0, listOf(214), values)), share = share,
    )

    @Test fun theResultLineFollowsWhatTheHubDidWithTheCorrection() {
        val on = ShareState(saved = true)
        assertEquals(ShareResult.Waiting, shareResultOf(on, record(share = RecordShare("waiting", null, 1))))
        assertEquals(
            ShareResult.NotWaiting("the Label Reader did not answer: down"),
            shareResultOf(on, record(share = RecordShare("not_queued", "the Label Reader did not answer: down.", 0))),
        )
        assertEquals(ShareResult.NoPhoto, shareResultOf(on, record(photos = 0, share = RecordShare("none", null, 0))))
        // nothing differs from the reading any more: no line
        assertNull(shareResultOf(on, record(share = RecordShare("none", null, 0))))
        // never while sharing is off, and never from a Hub that has none
        assertNull(shareResultOf(ShareState(saved = false), record(share = RecordShare("waiting", null, 1))))
        assertNull(shareResultOf(null, record(share = RecordShare("waiting", null, 1))))
        assertNull(shareResultOf(on, record(share = null)))
    }

    private class RecordFake(var rec: HistoryRecord, var share: ShareState?) : HistoryBackend {
        var reads = 0
        val after = ArrayDeque<HistoryRecord>()
        override suspend fun list() = IntakeHistory(20, 0, null, true)
        override suspend fun record(id: String): HistoryRecord {
            reads++
            return after.removeFirstOrNull() ?: rec
        }
        override suspend fun photo(id: String, n: Int) = ByteArray(0)
        override suspend fun revise(id: String, note: String, item: Int?): RevisionProposal = error("not used")
        override suspend fun applyProposal(id: String, proposal: String): HistoryRecord = rec
        override suspend fun applyValues(id: String, item: Int, values: JsonObject): HistoryRecord = rec
        override suspend fun undo(id: String, revision: Int): HistoryRecord = rec
        override suspend fun share() = share
    }

    @Test fun theListAndTheRecordKnowWhetherSharingIsOn() = runTest(dispatcher) {
        val f = RecordFake(record(), ShareState(saved = true, count = 3))
        val list = HistoryListViewModel(f)
        list.refresh()
        runCurrent()
        assertEquals(3, list.state.value.share?.count)
        assertTrue(list.state.value.share!!.on)
        val rec = RecordViewModel(f, "a1")
        rec.load()
        runCurrent()
        assertTrue(rec.state.value.share!!.on)
    }

    @Test fun aHubWithoutShareCorrectionsHasNoShareStateAnywhere() = runTest(dispatcher) {
        // what an older Hub's backend gives: the route is missing, so there is no state
        val f = RecordFake(record(), null)
        val list = HistoryListViewModel(f)
        list.refresh()
        val rec = RecordViewModel(f, "a1")
        rec.load()
        runCurrent()
        assertNull(list.state.value.share)
        assertNull(rec.state.value.share)
        assertNotNull(list.state.value.history)
    }

    @Test fun aShareRouteThatFailsDoesNotFailTheList() = runTest(dispatcher) {
        val f = object : HistoryBackend by RecordFake(record(), null) {
            override suspend fun share(): ShareState? = throw ApiError(500, "internal", "boom")
        }
        val list = HistoryListViewModel(f)
        list.refresh()
        runCurrent()
        assertNotNull(list.state.value.history)
        assertNull(list.state.value.error)
        assertNull(list.state.value.share)
    }

    @Test fun afterASaveTheRecordIsReadAgainUntilTheHubHasQueuedTheCorrection() = runTest(dispatcher) {
        // the Hub queues in the background: the reply to the save still says none
        val saved = record(share = RecordShare("none", null, 0))
        val f = RecordFake(saved, ShareState(saved = true))
        val vm = RecordViewModel(f, "a1", settleMs = 100)
        vm.load()
        runCurrent()
        f.after += saved
        f.after += record(share = RecordShare("waiting", null, 1))
        vm.startEdit(0)
        vm.setText(RecentField.Vendor, "Polymaker Ltd")
        vm.saveEdit {}
        runCurrent()
        assertEquals("none", vm.state.value.record!!.share!!.state)
        advanceTimeBy(101)
        runCurrent()
        advanceTimeBy(101)
        runCurrent()
        assertEquals("waiting", vm.state.value.record!!.share!!.state)
        assertEquals(ShareResult.Waiting, shareResultOf(vm.state.value.share, vm.state.value.record!!))
    }

    @Test fun theRecordIsNotReadAgainWhenSharingIsOffOrNoPhotoWasKept() = runTest(dispatcher) {
        for ((share, photos) in listOf(ShareState(saved = false) to 1, ShareState(saved = true) to 0, null to 1)) {
            val f = RecordFake(record(photos = photos, share = RecordShare("none", null, 0)), share)
            val vm = RecordViewModel(f, "a1", settleMs = 100)
            vm.load()
            runCurrent()
            vm.startEdit(0)
            vm.setText(RecentField.Vendor, "Polymaker Ltd")
            vm.saveEdit {}
            runCurrent()
            val before = f.reads
            advanceTimeBy(1_000)
            runCurrent()
            assertEquals("share=$share photos=$photos", before, f.reads)
        }
    }
}
