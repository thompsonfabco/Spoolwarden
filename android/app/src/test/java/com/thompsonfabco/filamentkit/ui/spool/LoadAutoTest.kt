package com.thompsonfabco.filamentkit.ui.spool

import com.thompsonfabco.filamentkit.net.AmsSlot
import com.thompsonfabco.filamentkit.net.AmsUnit
import com.thompsonfabco.filamentkit.net.ApiError
import com.thompsonfabco.filamentkit.net.AutoLoadResult
import com.thompsonfabco.filamentkit.net.AutoReplaces
import com.thompsonfabco.filamentkit.net.LoadResult
import com.thompsonfabco.filamentkit.net.Printer
import com.thompsonfabco.filamentkit.net.PrinterDetail
import com.thompsonfabco.filamentkit.net.PrinterStatus
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.resetMain
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

/** Automatic in the load steps (R83 addition, A5): offered and preselected only where the Hub says it is possible. */
@OptIn(ExperimentalCoroutinesApi::class)
class LoadAutoTest {
    private val dispatcher = StandardTestDispatcher()
    private val spool = spoolJson(214).toSpoolSummary()

    private val slots = AmsUnit(0, "A", "ams", listOf(AmsSlot(0, "A1", "PETG", watchable = true), AmsSlot(1, "A2", watchable = true)))
    private val auto = Printer(id = "X1A", name = "Workshop X1", serial = "X1A", configured = true, state = "connected", autoPossible = true)
    private val plain = auto.copy(autoPossible = false)

    private class Fake(var detail: PrinterDetail) : LoadBackend {
        val calls = mutableListOf<String>()
        var dry = AutoLoadResult(watching = listOf("A1", "A2"), dryRun = true)
        var real = AutoLoadResult(pending = true, expiresIn = 900, message = "Waiting")
        var failure: ApiError? = null

        override suspend fun printers(): List<Printer> = listOf(detail.printer)
        override suspend fun printer(id: String): PrinterDetail = detail
        override suspend fun load(id: String, label: String, spoolId: Int, dryRun: Boolean): LoadResult {
            calls += "load:$id:$label:$spoolId:dry=$dryRun"
            return LoadResult(slot = com.thompsonfabco.filamentkit.net.LoadSlot(label), confirmed = true)
        }
        override suspend fun autoLoad(id: String, spoolId: Int, dryRun: Boolean): AutoLoadResult {
            calls += "auto:$id:$spoolId:dry=$dryRun"
            failure?.let { throw it }
            return if (dryRun) dry else real
        }
    }

    private fun backend(printer: Printer) = Fake(PrinterDetail(printer, PrinterStatus(ams = listOf(slots))))

    @Before fun setUp() = Dispatchers.setMain(dispatcher)

    @After fun tearDown() = Dispatchers.resetMain()

    @Test fun automaticIsPreselectedOnlyWhenTheHubSaysAutoIsPossible() = runTest(dispatcher) {
        val with = LoadSlotsViewModel("X1A", backend(auto))
        advanceUntilIdle()
        assertTrue(with.state.value.auto)
        assertEquals(AUTO_SLOT, with.state.value.selected)

        val without = LoadSlotsViewModel("X1A", backend(plain))
        advanceUntilIdle()
        assertFalse(without.state.value.auto)
        assertNull(without.state.value.selected)
    }

    @Test fun aPrinterFromAnOldHubIsAsBefore() {
        // an old Hub sends no auto_possible (the decoding is in ApiAutoSlotTest): the field's default
        val old = Printer(id = "P", name = "W", serial = "P")
        assertFalse(autoOffered(old))
        assertNull(preselected(old))
        assertNull(preselected(null))
    }

    @Test fun pickingASlotMovesTheChoiceAndAReloadKeepsIt() = runTest(dispatcher) {
        val vm = LoadSlotsViewModel("X1A", backend(auto))
        advanceUntilIdle()
        vm.select("A2")
        assertEquals("A2", vm.state.value.selected)
        vm.load()
        advanceUntilIdle()
        assertEquals("A2", vm.state.value.selected)
    }

    @Test fun theDryRunSaysWhatItWatchesAndNothingWhenNothingWasWaiting() = runTest(dispatcher) {
        val b = backend(auto)
        val vm = LoadPreviewViewModel(spool, "X1A", AUTO_SLOT, b)
        advanceUntilIdle()
        val ui = vm.state.value
        assertTrue(ui.ready)
        assertNull(ui.plan)
        assertEquals(listOf("A1", "A2"), ui.autoPlan!!.watching)
        assertNull(ui.autoPlan!!.replaces)
        assertEquals(listOf("auto:X1A:214:dry=true"), b.calls)
    }

    @Test fun theDryRunNamesTheSpoolItReplaces() = runTest(dispatcher) {
        val b = backend(auto)
        b.dry = b.dry.copy(replaces = AutoReplaces(7, "Acme PLA"))
        val vm = LoadPreviewViewModel(spool, "X1A", AUTO_SLOT, b)
        advanceUntilIdle()
        assertEquals("Acme PLA", vm.state.value.autoPlan!!.replaces!!.name)
        assertEquals("Replaces Acme PLA, which was waiting.", LoadTexts.replaces("Acme PLA"))
    }

    @Test fun theRealLoadIsOneWriteAndTheResultSaysItIsWaiting() = runTest(dispatcher) {
        val b = backend(auto)
        val vm = LoadPreviewViewModel(spool, "X1A", AUTO_SLOT, b)
        advanceUntilIdle()
        vm.load()
        advanceUntilIdle()
        val result = vm.state.value.finished as SpoolResult.Waiting
        assertEquals(SpoolResult.Waiting("Workshop X1", AUTO_SLOT, "Polymaker PolyTerra PLA Charcoal Black"), result)
        assertEquals(
            "Waiting for Polymaker PolyTerra PLA Charcoal Black to be loaded into any slot of Workshop X1.",
            LoadTexts.waiting(result),
        )
        assertEquals(listOf("auto:X1A:214:dry=true", "auto:X1A:214:dry=false"), b.calls)
        assertEquals("it survives the spool screen's saved state", result, SpoolResult.decode(result.encode()))
    }

    @Test fun aNamedLoadStillGoesToTheNamedCall() = runTest(dispatcher) {
        val b = backend(auto)
        val vm = LoadPreviewViewModel(spool, "X1A", "A2", b)
        advanceUntilIdle()
        vm.load()
        advanceUntilIdle()
        assertEquals(listOf("load:X1A:A2:214:dry=true", "load:X1A:A2:214:dry=false"), b.calls)
        assertEquals(SpoolResult.Loaded("Workshop X1", "A2"), vm.state.value.finished)
        assertEquals("Waiting for the spool to load in A2.", LoadTexts.waiting(SpoolResult.Waiting("P", "A2")))
    }

    @Test fun aWaitingResultWithoutASpoolNameStillReads() {
        assertEquals(
            "Waiting for the spool to be loaded into any slot of Workshop X1.",
            LoadTexts.waiting(SpoolResult.Waiting("Workshop X1", AUTO_SLOT)),
        )
        assertEquals(SpoolResult.Waiting("P", "A1"), SpoolResult.decode(SpoolResult.Waiting("P", "A1").encode()))
    }

    @Test fun aPrinterThatCannotWatchRefusesInTheHubsWordsOrTheRuledOnes() = runTest(dispatcher) {
        val b = backend(auto)
        b.failure = ApiError(409, "auto_not_possible", "", JsonObject(mapOf("text" to kotlinx.serialization.json.JsonPrimitive("No sensors."))))
        val vm = LoadPreviewViewModel(spool, "X1A", AUTO_SLOT, b)
        advanceUntilIdle()
        assertFalse(vm.state.value.ready)
        assertEquals(HubFailure.Message("No sensors."), vm.state.value.failure)

        b.failure = ApiError(409, "auto_not_possible", "")
        vm.check()
        advanceUntilIdle()
        assertEquals(HubFailure.Message("This printer has no slots the Hub can watch. Choose a slot."), vm.state.value.failure)
    }

    @Test fun aRefusalAtTheWriteIsShownAndNotOfferedAgain() = runTest(dispatcher) {
        val b = backend(auto)
        val vm = LoadPreviewViewModel(spool, "X1A", AUTO_SLOT, b)
        advanceUntilIdle()
        b.failure = ApiError(409, "auto_not_possible", "", JsonObject(mapOf("text" to kotlinx.serialization.json.JsonPrimitive("No sensors."))))
        vm.load()
        advanceUntilIdle()
        assertEquals(HubFailure.Message("No sensors."), vm.state.value.failure)
        assertTrue(vm.state.value.failedWrite)
        assertNull(vm.state.value.finished)
    }
}
