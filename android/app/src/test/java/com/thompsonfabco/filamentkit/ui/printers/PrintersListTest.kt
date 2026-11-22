package com.thompsonfabco.filamentkit.ui.printers

import com.thompsonfabco.filamentkit.R
import com.thompsonfabco.filamentkit.net.AmsSlot
import com.thompsonfabco.filamentkit.net.AmsUnit
import com.thompsonfabco.filamentkit.net.ApiError
import com.thompsonfabco.filamentkit.net.CommandResult
import com.thompsonfabco.filamentkit.net.FingerprintMismatch
import com.thompsonfabco.filamentkit.net.KlipperFound
import com.thompsonfabco.filamentkit.net.Printer
import com.thompsonfabco.filamentkit.net.PrinterDetail
import com.thompsonfabco.filamentkit.net.PrinterStatus
import com.thompsonfabco.filamentkit.net.PrinterSummary
import com.thompsonfabco.filamentkit.net.SlotPending
import com.thompsonfabco.filamentkit.net.SlotSpool
import com.thompsonfabco.filamentkit.net.UnloadResult
import com.thompsonfabco.filamentkit.ui.components.StatusKind
import kotlinx.coroutines.CompletableDeferred
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

/** The Printers list (05-android-app 9.1 to 9.3): what a row says, the slots, the Klipper form and the search. */
@OptIn(ExperimentalCoroutinesApi::class)
class PrintersListTest {
    private val dispatcher = StandardTestDispatcher()

    @Before fun setUp() = Dispatchers.setMain(dispatcher)
    @After fun tearDown() = Dispatchers.resetMain()

    private class Fake : PrinterBackend {
        var list: List<Printer> = emptyList()
        var listError: Exception? = null
        var details = mutableMapOf<String, PrinterDetail>()
        val detailCalls = mutableListOf<String>()
        val added = mutableListOf<Triple<String, Int, String?>>()
        var addResult: Printer? = null
        var addError: Exception? = null
        var addGate: CompletableDeferred<Unit>? = null
        val keys = mutableListOf<Pair<String, String>>()
        var keyError: Exception? = null
        val scans = mutableListOf<String?>()
        var scanResult: List<KlipperFound> = emptyList()
        var scanError: Exception? = null

        override suspend fun printers(): List<Printer> {
            listError?.let { throw it }
            return list
        }
        val addedBambu = mutableListOf<String>()
        override suspend fun addPrinter(serial: String, accessCode: String, name: String?): Printer {
            addedBambu += serial
            addGate?.await()
            addError?.let { throw it }
            return addResult!!
        }
        /** serial, address, name of each add by address */
        val addedByAddress = mutableListOf<Triple<String?, String, String?>>()
        override suspend fun addPrinterByAddress(serial: String?, accessCode: String, name: String?, address: String): Printer {
            addedByAddress += Triple(serial, address, name)
            addGate?.await()
            addError?.let { throw it }
            return addResult!!
        }
        override suspend fun addKlipperPrinter(host: String, port: Int, name: String?): Printer {
            added += Triple(host, port, name)
            addGate?.await()
            addError?.let { throw it }
            return addResult!!
        }
        val addKeys = mutableListOf<String?>()
        override suspend fun addKlipperPrinter(host: String, port: Int, name: String?, apiKey: String?): Printer {
            addKeys += apiKey
            return addKlipperPrinter(host, port, name)
        }
        val tests = mutableListOf<Triple<String, Int, String?>>()
        var testResult: Boolean = true
        var testError: Exception? = null
        override suspend fun testKlipperPrinter(host: String, port: Int, apiKey: String?): Boolean {
            tests += Triple(host, port, apiKey)
            testError?.let { throw it }
            return testResult
        }
        override suspend fun scanKlipper(subnet: String?): List<KlipperFound> {
            scans += subnet
            scanError?.let { throw it }
            return scanResult
        }
        override suspend fun setApiKey(id: String, key: String) {
            keys += id to key
            keyError?.let { throw it }
        }
        override suspend fun printer(id: String): PrinterDetail {
            detailCalls += id
            return details[id] ?: throw ApiError(404, "not_found", "no such printer")
        }
        override suspend fun command(id: String, action: String): CommandResult = error("not used")
        override suspend fun unload(id: String, label: String, dryRun: Boolean): UnloadResult = error("not used")
    }

    private val backend = Fake()

    private fun printer(
        id: String = "01S00A",
        state: String = "connected",
        configured: Boolean = true,
        family: String = "bambu",
    ) = Printer(
        id = id, name = "Workshop X1", model = "X1C", serial = id, ip = "192.0.2.40", configured = configured,
        state = state, family = family, summary = PrinterSummary("RUNNING", 42, 72),
    )

    private fun detail(p: Printer, vararg slots: AmsSlot) =
        PrinterDetail(p, PrinterStatus(ams = listOf(AmsUnit(unit = 0, slots = slots.toList()))))

    // --- what a row says ---

    @Test fun everyStateOfTheVocabularyHasItsBadgeAndFix() {
        assertEquals(StatusKind.Connected, statusKind("connected"))
        assertEquals(StatusKind.Offline, statusKind("offline"))
        assertEquals(StatusKind.NotConfigured, statusKind("not_configured"))
        for (s in listOf("wrong_code", "needs_developer_mode", "cert_changed")) assertEquals(s, StatusKind.NeedsLook, statusKind(s))
        assertNull("a connected printer needs nothing", fixLine("connected"))
        assertEquals(R.string.printers_fix_offline, fixLine("offline"))
        assertEquals(R.string.printers_fix_wrong_code, fixLine("wrong_code"))
        assertEquals(R.string.printers_fix_developer_mode, fixLine("needs_developer_mode"))
        assertEquals(R.string.printers_fix_cert_changed, fixLine("cert_changed"))
        assertEquals(R.string.printers_fix_not_configured, fixLine("not_configured"))
    }

    @Test fun theJobWordFollowsTheGcodeState() {
        assertEquals(JobWord.Printing, jobWord("RUNNING"))
        assertEquals(JobWord.Paused, jobWord("PAUSE"))
        assertEquals(JobWord.Preparing, jobWord("PREPARE"))
        assertEquals(JobWord.Slicing, jobWord("SLICING"))
        assertEquals(JobWord.Finished, jobWord("FINISH"))
        assertEquals(JobWord.Failed, jobWord("FAILED"))
        assertEquals(JobWord.Idle, jobWord("IDLE"))
        assertNull(jobWord("UNKNOWN"))
        assertEquals(setOf("Printing", "Paused", "Preparing", "Slicing"), JobWord.entries.filter { it.progress }.map { it.name }.toSet())
    }

    @Test fun slotsShowUpToEightThenHowManyMore() {
        val twelve = List(12) { SlotLook("2B2B2E", empty = false) }
        val (shown, more) = shownSlots(twelve)
        assertEquals(8, shown.size)
        assertEquals(4, more)
        assertEquals(0, shownSlots(twelve.take(8)).second)
        assertEquals(0, shownSlots(emptyList()).second)
    }

    @Test fun timeLeftIsHoursAndMinutes() {
        assertEquals(1 to 12, hoursAndMinutes(72))
        assertEquals(0 to 31, hoursAndMinutes(31))
        assertEquals(2 to 0, hoursAndMinutes(120))
    }

    // --- the list ---

    @Test fun printersSplitIntoSetUpAndFoundOnTheNetwork() = runTest(dispatcher) {
        backend.list = listOf(printer("A"), printer("B", state = "not_configured", configured = false))
        val vm = PrintersViewModel(backend) { 5_000L }
        assertFalse(vm.state.value.loaded)
        vm.refresh(); runCurrent()
        val s = vm.state.value
        assertEquals(listOf("A"), s.configured.map { it.id })
        assertEquals(listOf("B"), s.found.map { it.id })
        assertTrue(s.loaded)
        assertFalse(s.loading)
        assertEquals(5_000L, s.loadedAt)
    }

    @Test fun slotSwatchesComeFromEachConnectedPrintersOwnScreenData() = runTest(dispatcher) {
        val up = printer("A")
        backend.list = listOf(up, printer("B", state = "offline"), printer("C", state = "not_configured", configured = false))
        backend.details["A"] = detail(
            up, AmsSlot(0, "A1", "PLA", "2B2B2E"), AmsSlot(1, "A2", "", "", present = false),
        )
        val vm = PrintersViewModel(backend)
        vm.refresh(); runCurrent()
        assertEquals(listOf("A"), backend.detailCalls)
        assertEquals(listOf(SlotLook("2B2B2E", false), SlotLook("", true)), vm.state.value.swatches["A"])
    }

    @Test fun aWaitingSlotKeepsItsColourAndIsMarked() = runTest(dispatcher) {
        val up = printer("A")
        backend.list = listOf(up)
        backend.details["A"] = detail(
            up, AmsSlot(0, "A1", "", "", present = false, pending = SlotPending(7)),
            AmsSlot(1, "A2", "", "", present = false, spool = SlotSpool(id = 7, colorHex = "E0A030"), pending = SlotPending(7)),
        )
        val vm = PrintersViewModel(backend)
        vm.refresh(); runCurrent()
        val looks = vm.state.value.swatches["A"].orEmpty()
        assertEquals(listOf(true, true), looks.map { it.waiting })
        assertEquals("no colour known: the dashed ring stays", true, looks[0].drawnEmpty)
        assertEquals("colour known: drawn in it", false, looks[1].drawnEmpty)
        assertEquals("E0A030", looks[1].hex)
        assertEquals(true, SlotLook("", true).drawnEmpty)
    }

    @Test fun aPrinterThatDoesNotAnswerHasNoSwatchesAndTheListStays() = runTest(dispatcher) {
        backend.list = listOf(printer("A"))
        val vm = PrintersViewModel(backend)
        vm.refresh(); runCurrent()
        assertEquals(1, vm.state.value.printers.size)
        assertTrue(vm.state.value.swatches.isEmpty())
        assertNull(vm.state.value.problem)
    }

    @Test fun failuresAreWordedBySortAndTheDataStays() = runTest(dispatcher) {
        backend.list = listOf(printer("A"))
        val vm = PrintersViewModel(backend)
        vm.refresh(); runCurrent()
        backend.listError = ApiError(0, "offline", "Could not reach the Hub: x")
        vm.refresh(); runCurrent()
        assertEquals(Problem.Unreachable, vm.state.value.problem)
        assertEquals("the printers already loaded stay", 1, vm.state.value.printers.size)
        backend.listError = ApiError(500, "boom", "The Hub's own words")
        vm.refresh(); runCurrent()
        assertEquals(Problem.HubSaid("The Hub's own words"), vm.state.value.problem)
        backend.listError = ApiError(200, "bad_response", "The Hub sent a reply the app could not read")
        vm.refresh(); runCurrent()
        assertEquals(Problem.Unexpected, vm.state.value.problem)
        backend.listError = null
        vm.refresh(); runCurrent()
        assertNull(vm.state.value.problem)
    }

    @Test fun anUnpairedPhoneIsAHubNoticeNotAProblemText() = runTest(dispatcher) {
        backend.listError = ApiError(401, "unauthorized", "x")
        val vm = PrintersViewModel(backend)
        vm.refresh(); runCurrent()
        assertTrue(vm.state.value.unauthorized)
        assertNull(vm.state.value.problem)
        assertFalse(vm.state.value.loading)
    }

    @Test fun aFoundPrinterOpensTheAddFormAndReplacingTheCodeLivesInPrinterSettings() = runTest(dispatcher) {
        val vm = PrintersViewModel(backend)
        vm.openAdd(printer("B", state = "not_configured", configured = false))
        assertEquals("B", vm.state.value.dialog!!.serial)
    }

    // --- Add a Bambu Lab printer by its address ---

    @Test fun aFoundPrintersAddressFillsTheFormAndAnUntouchedOneAddsBySerial() = runTest(dispatcher) {
        backend.addResult = printer("B")
        val vm = PrintersViewModel(backend)
        vm.openAdd(printer("B", state = "not_configured", configured = false))
        assertEquals("192.0.2.40", vm.state.value.dialog!!.address)
        vm.submitAdd("12345678", "", "192.0.2.40")
        runCurrent()
        assertEquals(listOf("B"), backend.addedBambu)
        assertTrue(backend.addedByAddress.isEmpty())
    }

    @Test fun anEditedAddressAddsByAddressWithTheSerialAlong() = runTest(dispatcher) {
        backend.addResult = printer("B")
        val vm = PrintersViewModel(backend)
        vm.openAdd(printer("B", state = "not_configured", configured = false))
        vm.submitAdd("12345678", "Garage", " 192.0.2.99 ")
        runCurrent()
        assertTrue(backend.addedBambu.isEmpty())
        assertEquals(Triple("B" as String?, "192.0.2.99", "Garage" as String?), backend.addedByAddress.single())
        assertNull(vm.state.value.dialog)
    }

    @Test fun aPrinterThatWasNotFoundIsAddedByTheAddressTyped() = runTest(dispatcher) {
        backend.addResult = printer("B")
        val vm = PrintersViewModel(backend)
        vm.openGuide(); runCurrent()
        vm.addBambuByAddress()
        val d = vm.state.value.dialog!!
        assertEquals("", d.serial)
        assertTrue(d.guided)
        assertNull(vm.state.value.guide)
        vm.submitAdd("12345678", "", "192.0.2.77")
        runCurrent()
        assertEquals(Triple(null as String?, "192.0.2.77", null as String?), backend.addedByAddress.single())
        assertEquals("Workshop X1", vm.state.value.added)
        assertNull(vm.state.value.dialog)
    }

    @Test fun noAddressOrNoCodeAsksBeforeAnyRequest() = runTest(dispatcher) {
        val vm = PrintersViewModel(backend)
        vm.openAddByAddress()
        vm.submitAdd("", "", "  ")
        runCurrent()
        val d = vm.state.value.dialog!!
        assertTrue(d.addressMissing)
        assertTrue(d.codeMissing)
        assertFalse(d.busy)
        assertTrue(backend.addedByAddress.isEmpty())
    }

    @Test fun aRefusalByAddressShowsTheHubsMessageAndField() = runTest(dispatcher) {
        backend.addError = ApiError(400, "bad_request", "That address is not valid", JsonObject(mapOf("field" to JsonPrimitive("address"))))
        val vm = PrintersViewModel(backend)
        vm.openAddByAddress()
        vm.submitAdd("12345678", "", "not an address")
        runCurrent()
        val d = vm.state.value.dialog!!
        assertEquals(Problem.HubSaid("That address is not valid", "address"), d.problem)
        assertFalse(d.busy)
    }

    @Test fun anExistingPrinterIsTheHubsOwnMessage() = runTest(dispatcher) {
        backend.addError = ApiError(409, "exists", "This printer is already set up")
        val vm = PrintersViewModel(backend)
        vm.openAddByAddress()
        vm.submitAdd("12345678", "", "192.0.2.77")
        runCurrent()
        assertEquals(Problem.HubSaid("This printer is already set up"), vm.state.value.dialog!!.problem)
    }

    // --- Add a Klipper printer ---

    @Test fun theFormOpensWithAFoundAddressAndPort() {
        val vm = PrintersViewModel(backend)
        vm.openAddKlipper("192.0.2.57", 7126)
        assertEquals(KlipperDialog(host = "192.0.2.57", port = 7126), vm.state.value.klipper)
        vm.openAddKlipper()
        assertEquals(KlipperDialog(host = "", port = 7125), vm.state.value.klipper)
    }

    @Test fun theApiKeyGoesOnTheTestAndOnTheAddAndIsNeverKept() = runTest(dispatcher) {
        backend.addResult = printer("K1", family = "klipper")
        val vm = PrintersViewModel(backend)
        vm.openAddKlipper()
        vm.submitAddKlipper("voron.example", "7125", "Voron", " s3cret ")
        runCurrent()
        assertEquals(listOf(Triple("voron.example", 7125, "s3cret" as String?)), backend.tests)
        assertEquals(listOf("s3cret" as String?), backend.addKeys)
        assertTrue("the key is no longer a separate request", backend.keys.isEmpty())
        assertNull(vm.state.value.klipper)
        assertEquals("Workshop X1", vm.state.value.added)
        assertFalse("the key is never kept in state", vm.state.value.toString().contains("s3cret"))
    }

    @Test fun noKeyTypedMeansNoKeyOnTheTestOrTheAdd() = runTest(dispatcher) {
        backend.addResult = printer("K1", family = "klipper")
        val vm = PrintersViewModel(backend)
        vm.openAddKlipper()
        vm.submitAddKlipper("voron.example", "", "", "  ")
        runCurrent()
        assertEquals(listOf(Triple("voron.example", 7125, null as String?)), backend.tests)
        assertEquals(listOf(null as String?), backend.addKeys)
        assertEquals(Triple("voron.example", 7125, null as String?), backend.added.single())
    }

    @Test fun aTestThatFindsNothingSavesNothingAndOffersTryAgainAndAddAnyway() = runTest(dispatcher) {
        backend.addResult = printer("K1", family = "klipper")
        backend.testResult = false
        val vm = PrintersViewModel(backend)
        vm.openAddKlipper()
        vm.submitAddKlipper("voron.example", "7126", "", "")
        runCurrent()
        val d = vm.state.value.klipper!!
        assertEquals("voron.example:7126", d.notReached)
        assertFalse(d.busy)
        assertTrue("nothing is added after a failed test", backend.added.isEmpty())
        // Try again tests again; this time a printer answers and is added
        backend.testResult = true
        vm.submitAddKlipper("voron.example", "7126", "", "")
        runCurrent()
        assertEquals(2, backend.tests.size)
        assertEquals(1, backend.added.size)
        assertNull(vm.state.value.klipper)
    }

    @Test fun addAnywayIsTheAddWithoutTheTest() = runTest(dispatcher) {
        backend.addResult = printer("K1", family = "klipper")
        backend.testResult = false
        val vm = PrintersViewModel(backend)
        vm.openAddKlipper()
        vm.submitAddKlipper("voron.example", "7125", "", "k")
        runCurrent()
        assertNotNull(vm.state.value.klipper!!.notReached)
        vm.addKlipperAnyway("voron.example", "7125", "Shed", "k")
        runCurrent()
        assertEquals(1, backend.tests.size)
        assertEquals(Triple("voron.example", 7125, "Shed" as String?), backend.added.single())
        assertEquals(listOf("k" as String?), backend.addKeys)
        assertNull(vm.state.value.klipper)
        assertEquals("Workshop X1", vm.state.value.added)
    }

    @Test fun aRefusedTestStaysInTheFormWithTheHubsMessageAndField() = runTest(dispatcher) {
        backend.testError = ApiError(
            400, "bad_request", "That address is not valid", JsonObject(mapOf("field" to JsonPrimitive("host"))),
        )
        val vm = PrintersViewModel(backend)
        vm.openAddKlipper()
        vm.submitAddKlipper("bad host", "7125", "", "")
        runCurrent()
        val d = vm.state.value.klipper!!
        assertEquals(Problem.HubSaid("That address is not valid", "host"), d.problem)
        assertNull(d.notReached)
        assertFalse(d.busy)
        assertTrue(backend.added.isEmpty())
    }

    @Test fun aHubWithoutTheTestRouteAddsAsBefore() = runTest(dispatcher) {
        backend.addResult = printer("K1", family = "klipper")
        backend.testError = ApiError(404, "not_found", "no such route")
        val vm = PrintersViewModel(backend)
        vm.openAddKlipper()
        vm.submitAddKlipper("voron.example", "7125", "", "")
        runCurrent()
        assertEquals(1, backend.added.size)
        assertNull(vm.state.value.klipper)
    }

    @Test fun aRefusedAddKeepsTheFormWithTheHubsMessage() = runTest(dispatcher) {
        backend.addResult = printer("K1", family = "klipper")
        backend.addError = ApiError(400, "bad_request", "That API key is not valid", JsonObject(mapOf("field" to JsonPrimitive("api_key"))))
        val vm = PrintersViewModel(backend)
        vm.openAddKlipper()
        vm.submitAddKlipper("voron.example", "7125", "", "bad key")
        runCurrent()
        assertEquals(Problem.HubSaid("That API key is not valid", "api_key"), vm.state.value.klipper!!.problem)
        assertFalse(vm.state.value.klipper!!.busy)
    }

    // --- the Hub problems (05-android-app 2.7, 3.4) ---

    @Test fun unauthorizedClearsAfterRepair() = runTest(dispatcher) {
        backend.listError = ApiError(401, "unauthorized", "x")
        val vm = PrintersViewModel(backend)
        vm.refresh(); runCurrent()
        assertTrue(vm.state.value.unauthorized)
        backend.listError = null
        backend.list = listOf(printer("A"))
        vm.refresh(); runCurrent()
        assertFalse(vm.state.value.unauthorized)
    }

    @Test fun mismatchClearsAfterTrust() = runTest(dispatcher) {
        backend.listError = FingerprintMismatch("old", "new")
        val vm = PrintersViewModel(backend)
        vm.refresh(); runCurrent()
        assertEquals("old" to "new", vm.state.value.mismatch)
        backend.listError = null
        vm.refresh(); runCurrent()
        assertNull(vm.state.value.mismatch)
    }

    @Test fun aHubProblemInsideTheAddFormFreesItAndIsTheHubNotice() = runTest(dispatcher) {
        backend.addError = ApiError(401, "unauthorized", "x")
        val vm = PrintersViewModel(backend)
        vm.openAdd(printer("B", state = "not_configured", configured = false))
        vm.submitAdd("12345678", "")
        runCurrent()
        val d = vm.state.value.dialog!!
        assertFalse("Cancel and Back work again", d.busy)
        assertEquals(Problem.NoLongerPaired, d.problem)
        assertTrue(vm.state.value.unauthorized)
        backend.addError = FingerprintMismatch("a", "b")
        vm.submitAdd("12345678", "")
        runCurrent()
        assertEquals(Problem.CertificateChanged, vm.state.value.dialog!!.problem)
        assertFalse(vm.state.value.dialog!!.busy)
    }

    @Test fun aHubProblemInsideTheKlipperFormAndTheSearchFreesThem() = runTest(dispatcher) {
        backend.addError = ApiError(401, "unauthorized", "x")
        backend.scanError = FingerprintMismatch("a", "b")
        val vm = PrintersViewModel(backend)
        vm.openAddKlipper()
        vm.submitAddKlipper("voron.example", "7125", "")
        runCurrent()
        assertEquals(Problem.NoLongerPaired, vm.state.value.klipper!!.problem)
        assertFalse(vm.state.value.klipper!!.busy)
        vm.openFind()
        vm.search(""); runCurrent()
        assertEquals(Problem.CertificateChanged, vm.state.value.find!!.problem)
        assertFalse(vm.state.value.find!!.searching)
    }

    @Test fun aHubNoticeInTheFormGoesAfterRepair() = runTest(dispatcher) {
        backend.addError = ApiError(401, "unauthorized", "x")
        val vm = PrintersViewModel(backend)
        vm.openAddKlipper()
        vm.submitAddKlipper("voron.example", "7125", "")
        runCurrent()
        assertEquals(Problem.NoLongerPaired, vm.state.value.klipper!!.problem)
        backend.addError = null
        vm.refresh(); runCurrent()
        assertNull("the notice goes with the good load", vm.state.value.klipper!!.problem)
    }

    @Test fun cancelWhileTheKlipperAddRunsDoesNothingAndTheAddStillFinishes() = runTest(dispatcher) {
        backend.addResult = printer("K1", family = "klipper")
        backend.addGate = CompletableDeferred()
        val vm = PrintersViewModel(backend)
        vm.openAddKlipper()
        vm.submitAddKlipper("voron.example", "7125", "", "k")
        runCurrent()
        assertTrue(vm.state.value.klipper!!.busy)
        vm.cancelAddKlipper()
        assertTrue("the form stays: an add cannot be taken back", vm.state.value.klipper!!.busy)
        backend.addGate!!.complete(Unit)
        runCurrent()
        assertNull(vm.state.value.klipper)
        assertEquals(listOf("k" as String?), backend.addKeys)
        assertEquals("Workshop X1", vm.state.value.added)
    }

    @Test fun cancelWhileTheBambuAddRunsDoesNothing() = runTest(dispatcher) {
        backend.addResult = printer("A")
        backend.addGate = CompletableDeferred()
        val vm = PrintersViewModel(backend)
        vm.openAdd(printer("B", state = "not_configured", configured = false))
        vm.submitAdd("12345678", "")
        runCurrent()
        vm.cancelAdd()
        assertTrue(vm.state.value.dialog!!.busy)
        backend.addGate!!.complete(Unit)
        runCurrent()
        assertNull(vm.state.value.dialog)
        assertEquals("Workshop X1", vm.state.value.added)
    }

    @Test fun theListIsReadAgainOnceTheFirstConnectionHasSettled() = runTest(dispatcher) {
        backend.addResult = printer("A")
        backend.list = listOf(printer("A", state = "offline"))
        val vm = PrintersViewModel(backend)
        vm.openAdd(printer("B", state = "not_configured", configured = false))
        vm.submitAdd("12345678", "")
        runCurrent()
        assertEquals("the list read right after the add shows the first, unsettled state", "offline", vm.state.value.printers.single().state)
        backend.list = listOf(printer("A"))
        advanceTimeBy(PrintersViewModel.SETTLE_MS - 1); runCurrent()
        assertEquals("offline", vm.state.value.printers.single().state)
        advanceTimeBy(2); runCurrent()
        assertEquals("connected", vm.state.value.printers.single().state)
    }

    @Test fun theKlipperFeatureBeingOffIsNamedByItsModule() = runTest(dispatcher) {
        backend.addError = ApiError(
            409, "module_off", "the klipper module is turned off on this server", buildJsonObject { put("module", "klipper") },
        )
        val vm = PrintersViewModel(backend)
        vm.openAddKlipper()
        vm.submitAddKlipper("voron.example", "7125", "")
        runCurrent()
        assertEquals(Problem.FeatureOff("klipper"), vm.state.value.klipper!!.problem)
    }

    // --- the guided add ---

    @Test fun theGuideLooksForKlipperPrintersAndListsTheFoundOnesFirst() = runTest(dispatcher) {
        val bambu = printer("B", state = "not_configured", configured = false)
        backend.list = listOf(printer("A"), bambu)
        backend.scanResult = listOf(
            KlipperFound("a", "192.0.2.57", 7125, "voron"),
            KlipperFound("b", "192.0.2.58", 7125, "done", configured = true),
        )
        val vm = PrintersViewModel(backend)
        vm.openGuide()
        assertTrue("Looking while the search runs", vm.state.value.guide!!.searching)
        runCurrent()
        val g = vm.state.value.guide!!
        assertFalse(g.searching)
        assertEquals(listOf<String?>(null), backend.scans)
        val rows = guideRows(vm.state.value.found, g.scanned)
        assertEquals(listOf(GuideRow.Known(bambu), GuideRow.Scanned(KlipperFound("a", "192.0.2.57", 7125, "voron"))), rows)
    }

    @Test fun aSearchAnswerAlreadyInTheHubsFoundListIsNotListedTwice() {
        val klipper = printer("K", state = "not_configured", configured = false, family = "klipper").copy(host = "192.0.2.57")
        val rows = guideRows(listOf(klipper), listOf(KlipperFound("a", "192.0.2.57", 7125), KlipperFound("c", "192.0.2.60", 7125)))
        assertEquals(listOf(GuideRow.Known(klipper), GuideRow.Scanned(KlipperFound("c", "192.0.2.60", 7125))), rows)
    }

    @Test fun nothingFoundLeavesTheAddressByHand() = runTest(dispatcher) {
        val vm = PrintersViewModel(backend)
        vm.openGuide(); runCurrent()
        assertTrue(guideRows(vm.state.value.found, vm.state.value.guide!!.scanned).isEmpty())
        vm.addByAddress()
        assertNull(vm.state.value.guide)
        assertEquals(KlipperDialog(host = "", port = 7125, guided = true), vm.state.value.klipper)
    }

    @Test fun aKlipperFeatureThatIsOffIsNotAnErrorOfTheGuide() = runTest(dispatcher) {
        backend.scanError = ApiError(409, "module_off", "off", kotlinx.serialization.json.JsonObject(mapOf("module" to kotlinx.serialization.json.JsonPrimitive("klipper"))))
        val vm = PrintersViewModel(backend)
        vm.openGuide(); runCurrent()
        val g = vm.state.value.guide!!
        assertFalse(g.searching)
        assertNull(g.problem)
        assertEquals(emptyList<KlipperFound>(), g.scanned)
    }

    @Test fun useThisOpensTheDetailsAndBackReturnsToTheGuide() = runTest(dispatcher) {
        backend.scanResult = listOf(KlipperFound("a", "192.0.2.57", 7126, "voron"))
        val vm = PrintersViewModel(backend)
        vm.openGuide(); runCurrent()
        vm.useGuideRow(guideRows(vm.state.value.found, vm.state.value.guide!!.scanned).single())
        assertNull(vm.state.value.guide)
        assertEquals(KlipperDialog(host = "192.0.2.57", port = 7126, guided = true), vm.state.value.klipper)
        vm.cancelAddKlipper(); runCurrent()
        assertNull(vm.state.value.klipper)
        assertNotNull("Back is the guide again", vm.state.value.guide)
    }

    @Test fun aBambuRowAsksForTheAccessCodeInTheSameStepAndBackReturns() = runTest(dispatcher) {
        val found = printer("B", state = "not_configured", configured = false)
        backend.list = listOf(found)
        val vm = PrintersViewModel(backend)
        vm.openGuide(); runCurrent()
        vm.useGuideRow(GuideRow.Known(found))
        assertTrue(vm.state.value.dialog!!.guided)
        vm.cancelAdd(); runCurrent()
        assertNull(vm.state.value.dialog)
        assertNotNull(vm.state.value.guide)
    }

    @Test fun anAddedPrinterOffersToMapItsSlotsOnceAndLaterDropsTheOffer() = runTest(dispatcher) {
        backend.addResult = printer("K1", family = "klipper")
        val vm = PrintersViewModel(backend)
        vm.openAddKlipper("voron.example", 7125, guided = true)
        vm.submitAddKlipper("voron.example", "7125", "Voron", "key")
        runCurrent()
        assertEquals(MapOffer("K1", "Workshop X1"), vm.state.value.mapOffer)
        assertEquals(listOf("key" as String?), backend.addKeys)
        vm.dismissMapOffer()
        assertNull(vm.state.value.mapOffer)
    }

    @Test fun aFailedAddOffersNoMapping() = runTest(dispatcher) {
        backend.addError = ApiError(400, "bad", "no")
        val vm = PrintersViewModel(backend)
        vm.openAddKlipper("voron.example", 7125, guided = true)
        vm.submitAddKlipper("voron.example", "7125", "", "")
        runCurrent()
        assertNull(vm.state.value.mapOffer)
        assertNotNull(vm.state.value.klipper)
    }

    // --- Find Klipper printers ---

    @Test fun searchSendsTheTypedNetworkOrNoneAndKeepsOnlyPrintersNotAddedYet() = runTest(dispatcher) {
        backend.scanResult = listOf(
            KlipperFound("a", "192.0.2.57", 7125, "voron"),
            KlipperFound("b", "192.0.2.58", 7125, "done", configured = true),
        )
        val vm = PrintersViewModel(backend)
        vm.openFind()
        vm.search(" 192.0.2.0/24 ")
        assertTrue(vm.state.value.find!!.searching)
        runCurrent()
        val f = vm.state.value.find!!
        assertFalse(f.searching)
        assertEquals(listOf<String?>("192.0.2.0/24"), backend.scans)
        assertEquals(listOf("192.0.2.57"), f.results!!.map { it.host })
        assertEquals("192.0.2.0/24", f.network)
        vm.search("  "); runCurrent()
        assertEquals(listOf<String?>("192.0.2.0/24", null), backend.scans)
        assertEquals("", vm.state.value.find!!.network)
    }

    @Test fun noAnswerIsAnEmptyListNotAProblem() = runTest(dispatcher) {
        val vm = PrintersViewModel(backend)
        vm.openFind()
        vm.search(""); runCurrent()
        assertEquals(emptyList<KlipperFound>(), vm.state.value.find!!.results)
        assertNull(vm.state.value.find!!.problem)
    }

    @Test fun theHubsRefusalOfASearchIsShownAsGiven() = runTest(dispatcher) {
        backend.scanError = ApiError(400, "bad_request", "scan at most a /24 (256 addresses)")
        val vm = PrintersViewModel(backend)
        vm.openFind()
        vm.search("192.0.2.0/16"); runCurrent()
        val f = vm.state.value.find!!
        assertEquals(Problem.HubSaid("scan at most a /24 (256 addresses)"), f.problem)
        assertFalse(f.searching)
        assertNull(f.results)
    }

    @Test fun addingAFoundPrinterOpensTheFormWithItsAddressAndClosesTheSheet() {
        val vm = PrintersViewModel(backend)
        vm.openFind()
        vm.openAddKlipper("192.0.2.57", 7125)
        assertNull(vm.state.value.find)
        assertEquals("192.0.2.57", vm.state.value.klipper!!.host)
    }
}
