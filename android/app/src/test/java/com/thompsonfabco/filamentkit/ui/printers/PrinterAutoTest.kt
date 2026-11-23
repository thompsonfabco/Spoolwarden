package com.thompsonfabco.filamentkit.ui.printers

import com.thompsonfabco.filamentkit.R
import com.thompsonfabco.filamentkit.net.ApiError
import com.thompsonfabco.filamentkit.net.AutoSpool
import com.thompsonfabco.filamentkit.net.AutoWait
import com.thompsonfabco.filamentkit.net.CommandResult
import com.thompsonfabco.filamentkit.net.KlipperFound
import com.thompsonfabco.filamentkit.net.LoadResult
import com.thompsonfabco.filamentkit.net.LoadSlot
import com.thompsonfabco.filamentkit.net.Printer
import com.thompsonfabco.filamentkit.net.PrinterDetail
import com.thompsonfabco.filamentkit.net.PrinterStatus
import com.thompsonfabco.filamentkit.net.UnloadResult
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import kotlinx.serialization.json.JsonObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/** The Auto notice on the printer's screen (A4): Stop waiting and the choice of two slots. */
@OptIn(ExperimentalCoroutinesApi::class)
class PrinterAutoTest {
    private val dispatcher = StandardTestDispatcher()

    @Before fun setUp() = Dispatchers.setMain(dispatcher)

    @After fun tearDown() = Dispatchers.resetMain()

    private val printer = Printer(id = "P1", name = "Workshop X1", serial = "P1", configured = true, state = "connected", autoPossible = true)
    private val spool = AutoSpool(12, "Acme PLA Red", "PLA", "FF0000")
    private fun waiting() = AutoWait(12, spool, AutoWait.STATE_WAITING, emptyList(), "Waiting", "2026-11-18T01:00:00Z")
    private fun choosing() = AutoWait(12, spool, AutoWait.STATE_CHOOSE, listOf("A1", "A3"), "Two", "2026-11-18T01:00:00Z")

    private class Fake(var detail: PrinterDetail) : PrinterBackend {
        val calls = mutableListOf<String>()
        var failure: ApiError? = null
        /** What the printer says after the call: null keeps [detail] as it is. */
        var afterCall: PrinterDetail? = null
        var chosen = LoadResult(slot = LoadSlot("A1"), confirmed = true)

        override suspend fun printers(): List<Printer> = emptyList()
        override suspend fun addPrinter(serial: String, accessCode: String, name: String?): Printer = TODO()
        override suspend fun addKlipperPrinter(host: String, port: Int, name: String?): Printer = TODO()
        override suspend fun scanKlipper(subnet: String?): List<KlipperFound> = TODO()
        override suspend fun setApiKey(id: String, key: String) = TODO()
        override suspend fun printer(id: String): PrinterDetail = detail
        override suspend fun command(id: String, action: String): CommandResult = TODO()
        override suspend fun unload(id: String, label: String, dryRun: Boolean): UnloadResult = TODO()

        override suspend fun autoCancel(id: String): Boolean {
            calls += "cancel:$id"
            failure?.let { throw it }
            afterCall?.let { detail = it }
            return true
        }

        override suspend fun autoChoose(id: String, slot: String): LoadResult {
            calls += "choose:$id:$slot"
            failure?.let { throw it }
            afterCall?.let { detail = it }
            return chosen
        }
    }

    private fun detail(auto: AutoWait?) = PrinterDetail(printer, PrinterStatus(), auto)
    private fun conflict(code: String) = ApiError(409, code, "The Hub says so.")

    @Test fun theNoticeNamesTheSpoolAndThePrinterAndHasNoChoices() {
        val n = PrinterTexts.autoNotice(waiting(), "Workshop X1")
        assertEquals("Waiting for Acme PLA Red to be loaded into any slot of Workshop X1.", n.text)
        assertTrue(n.choices.isEmpty())
        assertEquals("Stop waiting", PrinterTexts.STOP_WAITING)
    }

    @Test fun inTheStateChooseTheNoticeAsksWhichSlotAndOffersThem() {
        val n = PrinterTexts.autoNotice(choosing(), "Workshop X1")
        assertEquals("Two slots were loaded. Which one got Acme PLA Red?", n.text)
        assertEquals(listOf("A1", "A3"), n.choices)
    }

    @Test fun aSpoolWithoutANameIsNamedByItsNumberOrAsTheSpool() {
        assertEquals("Spool 12", PrinterTexts.autoSpool(waiting().copy(spool = null)))
        assertEquals("Spool 12", PrinterTexts.autoSpool(waiting().copy(spool = spool.copy(name = " "))))
        assertEquals("the spool", PrinterTexts.autoSpool(waiting().copy(spool = null, spoolId = null)))
    }

    @Test fun thePollReadsTheAutoAndItGoesWhenTheHubStopsSendingIt() = runTest(dispatcher) {
        val backend = Fake(detail(waiting()))
        val vm = PrinterViewModel(backend, "P1")
        vm.retry(); runCurrent()
        assertEquals(12, vm.state.value.auto!!.spoolId)
        backend.detail = detail(null)
        vm.retry(); runCurrent()
        assertNull("an old Hub, or an ended Auto, shows no notice", vm.state.value.auto)
    }

    @Test fun stopWaitingCancelsAndReadsThePrinterAgain() = runTest(dispatcher) {
        val backend = Fake(detail(waiting()))
        backend.afterCall = detail(null)
        val vm = PrinterViewModel(backend, "P1")
        vm.retry(); runCurrent()
        vm.stopWaiting(); runCurrent()
        assertEquals(listOf("cancel:P1"), backend.calls)
        assertNull(vm.state.value.auto)
        assertFalse(vm.state.value.autoBusy)
        assertNull(vm.state.value.slotNote)
    }

    @Test fun notWaitingJustRefreshes() = runTest(dispatcher) {
        val backend = Fake(detail(waiting()))
        backend.failure = conflict("not_waiting")
        val vm = PrinterViewModel(backend, "P1")
        vm.retry(); runCurrent()
        backend.detail = detail(null)
        vm.stopWaiting(); runCurrent()
        assertNull("it already ended", vm.state.value.auto)
        assertNull("and says nothing about it", vm.state.value.slotNote)
        assertFalse(vm.state.value.autoBusy)
    }

    @Test fun stopWaitingWithNothingWaitingSendsNothing() = runTest(dispatcher) {
        val backend = Fake(detail(null))
        val vm = PrinterViewModel(backend, "P1")
        vm.retry(); runCurrent()
        vm.stopWaiting(); runCurrent()
        assertTrue(backend.calls.isEmpty())
    }

    @Test fun choosingASlotSendsItAndShowsTheLoadsAnswer() = runTest(dispatcher) {
        val backend = Fake(detail(choosing()))
        backend.afterCall = detail(null)
        val vm = PrinterViewModel(backend, "P1")
        vm.retry(); runCurrent()
        vm.chooseAutoSlot("A3"); runCurrent()
        assertEquals(listOf("choose:P1:A3"), backend.calls)
        assertEquals(Say.Words(R.string.spool_result_loaded, listOf("Workshop X1", "A3")), vm.state.value.slotNote!!.say)
        assertNull(vm.state.value.auto)
    }

    @Test fun theAnswerOfAChoiceIsShownAsALoadResult() {
        val loaded = PrinterViewModel.chooseResult(LoadResult(slot = LoadSlot("A3"), confirmed = true), "Workshop X1", "A3")
        assertEquals(Say.Words(R.string.spool_result_loaded, listOf("Workshop X1", "A3")), loaded)
        val unconfirmed = PrinterViewModel.chooseResult(LoadResult(slot = LoadSlot("A3")), "Workshop X1", "A3")
        assertEquals(Say.Words(R.string.spool_load_unconfirmed), unconfirmed)
        val waiting = PrinterViewModel.chooseResult(LoadResult(slot = LoadSlot("A3"), pending = true), "Workshop X1", "A3")
        assertEquals(Say.Words(R.string.live_load_waiting, listOf("A3")), waiting)
    }

    @Test fun choosingOnlyWorksInTheStateChoose() = runTest(dispatcher) {
        val backend = Fake(detail(waiting()))
        val vm = PrinterViewModel(backend, "P1")
        vm.retry(); runCurrent()
        vm.chooseAutoSlot("A1"); runCurrent()
        assertTrue(backend.calls.isEmpty())
    }

    @Test fun nothingToChooseRefreshesWithoutAnError() = runTest(dispatcher) {
        val backend = Fake(detail(choosing()))
        backend.failure = conflict("nothing_to_choose")
        val vm = PrinterViewModel(backend, "P1")
        vm.retry(); runCurrent()
        backend.detail = detail(null)
        vm.chooseAutoSlot("A1"); runCurrent()
        assertNull(vm.state.value.auto)
        assertNull(vm.state.value.slotNote)
    }

    @Test fun aRefusalIsShownInTheHubsWordsOrTheRuledOnes() = runTest(dispatcher) {
        val backend = Fake(detail(choosing()))
        backend.failure = conflict("auto_not_possible")
        val vm = PrinterViewModel(backend, "P1")
        vm.retry(); runCurrent()
        vm.chooseAutoSlot("A1"); runCurrent()
        assertEquals(Note(Say.FromHub("The Hub says so."), ok = false), vm.state.value.slotNote)

        backend.failure = ApiError(409, "auto_not_possible", "", JsonObject(emptyMap()))
        vm.chooseAutoSlot("A1"); runCurrent()
        assertEquals(
            Note(Say.Words(R.string.spool_load_auto_refused), ok = false),
            vm.state.value.slotNote,
        )
    }

    @Test fun anyOtherHubRefusalKeepsItsTextAndTheNoticeStays() = runTest(dispatcher) {
        val backend = Fake(detail(waiting()))
        backend.failure = conflict("something_else")
        val vm = PrinterViewModel(backend, "P1")
        vm.retry(); runCurrent()
        vm.stopWaiting(); runCurrent()
        assertEquals(Note(Say.FromHub("The Hub says so."), ok = false), vm.state.value.slotNote)
        assertEquals(12, vm.state.value.auto!!.spoolId)
        assertFalse(vm.state.value.autoBusy)
    }

    @Test fun aBackendWithoutAutoAnswersLikeAnOlderHub() = runTest(dispatcher) {
        val old = object : PrinterBackend {
            override suspend fun printers(): List<Printer> = emptyList()
            override suspend fun addPrinter(serial: String, accessCode: String, name: String?): Printer = TODO()
            override suspend fun addKlipperPrinter(host: String, port: Int, name: String?): Printer = TODO()
            override suspend fun scanKlipper(subnet: String?): List<KlipperFound> = TODO()
            override suspend fun setApiKey(id: String, key: String) = TODO()
            override suspend fun printer(id: String): PrinterDetail = detail(waiting())
            override suspend fun command(id: String, action: String): CommandResult = TODO()
            override suspend fun unload(id: String, label: String, dryRun: Boolean): UnloadResult = TODO()
        }
        val vm = PrinterViewModel(old, "P1")
        vm.retry(); runCurrent()
        vm.stopWaiting(); runCurrent()
        val e = runCatching { old.autoCancel("P1") }.exceptionOrNull() as ApiError
        assertEquals(404, e.status)
        assertFalse(vm.state.value.autoBusy)
    }
}
