package com.thompsonfabco.filamentkit.ui.search

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.nfc.NfcAdapter
import android.provider.Settings
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.foundation.layout.Box
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.ImeAction
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import com.thompsonfabco.filamentkit.R
import com.thompsonfabco.filamentkit.alerts.LowSpool
import com.thompsonfabco.filamentkit.net.ActivityEvent
import com.thompsonfabco.filamentkit.net.ApiError
import com.thompsonfabco.filamentkit.net.SpoolsAnswer
import com.thompsonfabco.filamentkit.ui.components.DetailPresentation
import com.thompsonfabco.filamentkit.ui.components.LocalToast
import com.thompsonfabco.filamentkit.ui.components.NoticeCard
import com.thompsonfabco.filamentkit.ui.components.NoticeKind
import com.thompsonfabco.filamentkit.ui.components.SwButton
import com.thompsonfabco.filamentkit.ui.components.SwButtonKind
import com.thompsonfabco.filamentkit.ui.pair.LabelScan
import com.thompsonfabco.filamentkit.ui.components.SwCardDetail
import com.thompsonfabco.filamentkit.ui.components.SwCollection
import com.thompsonfabco.filamentkit.ui.components.SwIcon
import com.thompsonfabco.filamentkit.ui.components.ScreenTopBarActions
import com.thompsonfabco.filamentkit.ui.components.SwIconButton
import com.thompsonfabco.filamentkit.ui.components.SwMenu
import com.thompsonfabco.filamentkit.ui.components.SwMenuItem
import com.thompsonfabco.filamentkit.ui.components.SwPaneLayout
import com.thompsonfabco.filamentkit.ui.components.SwSheetDetail
import com.thompsonfabco.filamentkit.ui.components.ScreenState
import com.thompsonfabco.filamentkit.ui.components.rememberDetailPresentation
import com.thompsonfabco.filamentkit.ui.components.requestDetailFocus
import com.thompsonfabco.filamentkit.ui.history.CsvOutcome
import com.thompsonfabco.filamentkit.ui.history.HistoryTexts
import com.thompsonfabco.filamentkit.ui.history.csvFileName
import com.thompsonfabco.filamentkit.ui.history.csvProblemText
import com.thompsonfabco.filamentkit.ui.history.UndoFlow
import com.thompsonfabco.filamentkit.ui.history.UndoOffer
import com.thompsonfabco.filamentkit.ui.history.UndoOutcome
import com.thompsonfabco.filamentkit.ui.history.csvTarget
import com.thompsonfabco.filamentkit.ui.history.showUndoable
import com.thompsonfabco.filamentkit.ui.history.undoHoldMs
import com.thompsonfabco.filamentkit.ui.history.shareCsv
import com.thompsonfabco.filamentkit.ui.spool.CheckedOutRules
import com.thompsonfabco.filamentkit.ui.spool.CheckedOutTexts
import com.thompsonfabco.filamentkit.ui.spool.HubFailure
import com.thompsonfabco.filamentkit.ui.spool.HubFailureNotice
import com.thompsonfabco.filamentkit.ui.spool.HubNoticeOf
import com.thompsonfabco.filamentkit.ui.spool.hubFailureText
import com.thompsonfabco.filamentkit.ui.spool.secondsSince
import com.thompsonfabco.filamentkit.ui.spool.ReturnToast
import com.thompsonfabco.filamentkit.ui.spool.SpoolRow
import com.thompsonfabco.filamentkit.ui.spool.SpoolSummary
import com.thompsonfabco.filamentkit.ui.spool.hubFailure
import com.thompsonfabco.filamentkit.ui.spool.toSpoolSummary
import com.thompsonfabco.filamentkit.ui.theme.SpoolwardenTheme
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject
import java.io.File
import com.thompsonfabco.filamentkit.ui.spool.SpoolTile

/**
 * Spools (05 7.1). [searched] is the query the [results] answer, for "No spools match "petg"."; [busy] is a search
 * running, while the results of the last one stay.
 */
data class SearchUi(
    val query: String = "",
    val sealedOnly: Boolean = false,
    val busy: Boolean = false,
    val searched: String? = null,
    val results: List<SpoolSummary> = emptyList(),
    val failure: HubFailure? = null,
    /** When the results were read (epoch millis), for the offline notice. */
    val loadedAt: Long? = null,
    /** The "Running low" filter (R80 section 7): the list of spools at or under the Hub's threshold replaces the search. */
    val lowOnly: Boolean = false,
    val low: List<LowSpool> = emptyList(),
    val lowLoaded: Boolean = false,
    /** Where Spoolman's own page is, as the Hub gave it; null: the Hub gave none (or is older), so there is no item. */
    val spoolmanUrl: String? = null,
    val csvBusy: Boolean = false,
    /** The downloaded file, waiting for the screen to open the share sheet ([SearchViewModel.csvShown]). */
    val csvFile: File? = null,
    /** Why the download did not happen: the words for an older Hub or a full phone, or a failure the usual notices say. */
    val csvProblem: String? = null,
    val csvFailure: HubFailure? = null,
    /** The "Checked out" filter (R88 section 1.6); the chip's count is the `total` of the answer. */
    val checkedOnly: Boolean = false,
    val checked: List<SpoolSummary> = emptyList(),
    /** The chip's number (the list answer's `total`); null: the Hub does not apply the filter, so there is no chip. */
    val checkedCount: Int? = null,
    val checkedLoaded: Boolean = false,
    /** The spool whose Return is on its way; the Hub's refusal of one (its own `message`) stays under the list. */
    val returningId: Int? = null,
    val returnFailure: HubFailure? = null,
    val returnToast: ReturnToast? = null,
)

/** Spool search; [search] is `api.search(q, sealedOnly)`, asked of the session's current client at each call. */
class SearchViewModel(
    private val pauseMs: Long = TYPING_PAUSE_MS,
    /** `api.lowSpools()`; null: the Hub has no running-low list, so there is no filter. */
    private val loadLow: (suspend () -> List<LowSpool>)? = null,
    /** `api.spools(...).spoolmanUrl`; null: the Hub gives none, so there is no Open Spoolman item. */
    private val loadSpoolmanUrl: (suspend () -> String?)? = null,
    /** `api.exportCsv("spoolman-spools", file)` (R84 6.5); null: no Download as CSV item. */
    private val exportCsv: (suspend (File) -> Unit)? = null,
    /** `api.spools("", "checked_out")`; null: no Checked out filter. */
    private val loadChecked: (suspend () -> SpoolsAnswer)? = null,
    /** `api.returnSpool(id)`; null: no Return action. */
    private val returnSpool: (suspend (Int) -> JsonObject)? = null,
    /** Finds and undoes the event a Return wrote; null: the toast has no Undo. */
    private val undoFlow: UndoFlow? = null,
    private val search: suspend (q: String, sealedOnly: Boolean) -> List<JsonObject>,
) : ViewModel() {
    private val _state = MutableStateFlow(SearchUi())
    val state: StateFlow<SearchUi> = _state.asStateFlow()
    private var job: Job? = null
    private var typing: Job? = null
    private var lowJob: Job? = null

    /** A pause in typing searches by itself once two letters are typed; clearing the box clears the results. */
    fun setQuery(q: String) {
        _state.update { it.copy(query = q) }
        typing?.cancel()
        if (q.isBlank()) {
            job?.cancel()
            _state.update { it.copy(busy = false, searched = null, results = emptyList(), failure = null) }
        } else if (q.trim().length >= MIN_TYPED) {
            typing = viewModelScope.launch {
                delay(pauseMs)
                search()
            }
        }
    }

    fun setSealedOnly(on: Boolean) {
        _state.update { it.copy(sealedOnly = on) }
        if (_state.value.query.isNotBlank()) search()
    }

    /** At once: the keyboard's search key, the chip, Try again. */
    fun search() {
        val s = _state.value
        val q = s.query.trim()
        if (q.isEmpty()) return
        typing?.cancel()
        job?.cancel()
        _state.update { it.copy(busy = true) }
        job = viewModelScope.launch {
            try {
                val found = search(q, s.sealedOnly).map { it.toSpoolSummary() }
                _state.update { it.copy(busy = false, searched = q, results = found, failure = null, loadedAt = System.currentTimeMillis()) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                // the rows of the last answer stay under the notice
                _state.update { it.copy(busy = false, failure = hubFailure(e)) }
            }
        }
    }

    fun clear() = setQuery("")

    /** Asks the Hub where Spoolman's page is, once per screen. Any failure (an older Hub's 404 too) leaves no item. */
    fun loadSpoolmanLink() {
        val load = loadSpoolmanUrl ?: return
        viewModelScope.launch {
            try {
                val url = spoolmanLink(load())
                _state.update { it.copy(spoolmanUrl = url) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _state.update { it.copy(spoolmanUrl = null) }
            }
        }
    }

    /** Whether the screen offers Download as CSV. */
    val canExport: Boolean get() = exportCsv != null

    /** Downloads every spool as CSV (`spoolman-spools`) into [target]; the screen then opens the share sheet on [csvFile]. */
    fun downloadCsv(target: File) {
        val export = exportCsv ?: return
        if (_state.value.csvBusy) return
        _state.update { it.copy(csvBusy = true, csvProblem = null, csvFailure = null, csvFile = null) }
        viewModelScope.launch {
            try {
                export(target)
                _state.update { it.copy(csvBusy = false, csvFile = target) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                val words = (e as? ApiError)?.let(::csvProblemText)
                _state.update { it.copy(csvBusy = false, csvProblem = words, csvFailure = if (words == null) hubFailure(e) else null) }
            }
        }
    }

    /** The screen opened the share sheet (or could not): the file is not offered again; [problem] says why not, if it did not open. */
    fun csvShown(problem: String?) = _state.update { it.copy(csvFile = null, csvProblem = problem) }

    /** The "Running low" chip: shows the list of low spools (least first, as the Hub gives it) instead of the search. */
    fun setLowOnly(on: Boolean) {
        _state.update { it.copy(lowOnly = on, checkedOnly = if (on) false else it.checkedOnly, failure = if (on) null else it.failure) }
        if (on) loadLowSpools()
    }

    /** Reads the running-low list again (the chip, Try again). */
    fun loadLowSpools() {
        val load = loadLow ?: return
        _state.update { it.copy(busy = true) }
        lowJob?.cancel()
        lowJob = viewModelScope.launch {
            try {
                val spools = load()
                _state.update { it.copy(busy = false, low = spools, lowLoaded = true, failure = null, loadedAt = System.currentTimeMillis()) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _state.update { it.copy(busy = false, failure = hubFailure(e)) }
            }
        }
    }

    /**
     * Reads the checked-out spools (the screen asks once, the chip and Try again ask again). A Hub that ignores the
     * filter or has no such route leaves the chip off; any other failure keeps what was shown.
     */
    fun loadCheckedOut() {
        val load = loadChecked ?: return
        if (_state.value.checkedOnly) _state.update { it.copy(busy = true) }
        checkedJob?.cancel()
        checkedJob = viewModelScope.launch {
            try {
                val answer = load()
                if (!CheckedOutRules.supported(answer.filters)) {
                    _state.update { it.copy(busy = false, checkedOnly = false, checked = emptyList(), checkedCount = null) }
                } else {
                    val spools = CheckedOutRules.rows(answer.spools)
                    _state.update {
                        it.copy(busy = false, checked = spools, checkedCount = CheckedOutRules.count(answer), checkedLoaded = true, loadedAt = System.currentTimeMillis(),
                            failure = if (it.checkedOnly) null else it.failure)
                    }
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                val gone = e is ApiError && (e.status == HTTP_NOT_FOUND || e.status == HTTP_BAD_REQUEST)
                _state.update {
                    it.copy(busy = false, checkedOnly = it.checkedOnly && !gone, checkedCount = if (gone) null else it.checkedCount,
                        failure = if (it.checkedOnly && !gone) hubFailure(e) else it.failure)
                }
            }
        }
    }

    /** Drops what was read of the Checked out filter (another Hub, or none yet): no chip until the Hub names the filter again. */
    fun forgetCheckedOut() {
        checkedJob?.cancel()
        _state.update { it.copy(checkedOnly = false, checked = emptyList(), checkedCount = null, checkedLoaded = false, returnFailure = null) }
    }

    /** The "Checked out" chip: the list of checked-out spools replaces the search. */
    fun setCheckedOnly(on: Boolean) {
        _state.update { it.copy(checkedOnly = on, lowOnly = if (on) false else it.lowOnly, failure = if (on) null else it.failure, returnFailure = null) }
        if (on) loadCheckedOut()
    }

    /** "Return to <home>": asks nothing; the row leaves the list and the toast offers Undo through the activity. */
    fun returnToHome(spool: SpoolSummary) {
        val send = returnSpool ?: return
        if (_state.value.returningId != null || !CheckedOutRules.canReturn(spool)) return
        _state.update { it.copy(returningId = spool.id, returnFailure = null) }
        viewModelScope.launch {
            try {
                val where = send(spool.id).toSpoolSummary().location
                val event = undoFlow?.find(UndoOffer(listOf("spool:${spool.id}"), setOf(CheckedOutRules.RETURNED_KIND)))
                _state.update { s ->
                    val left = s.checked.filter { it.id != spool.id }
                    s.copy(
                        returningId = null, checked = left, checkedCount = s.checkedCount?.let { n -> maxOf(n - 1, 0) },
                        returnToast = ReturnToast(CheckedOutTexts.returned(where), event),
                    )
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                // a refusal (409 not_checked_out) says its own words; the list is read again, it may have changed
                _state.update { it.copy(returningId = null, returnFailure = hubFailure(e)) }
                if (e is ApiError && e.status != 0) loadCheckedOut()
            }
        }
    }

    /** The toast was shown (or replaced). */
    fun returnToastShown() = _state.update { it.copy(returnToast = null) }

    /** The toast's Undo; the list is read again whatever came of it. */
    suspend fun undoReturn(event: ActivityEvent): UndoOutcome {
        val flow = undoFlow ?: return UndoOutcome.Refused("")
        val outcome = flow.undo(event)
        loadCheckedOut()
        return outcome
    }

    private var checkedJob: Job? = null

    private companion object {
        const val HTTP_NOT_FOUND = 404
        const val HTTP_BAD_REQUEST = 400
        const val MIN_TYPED = 2
        const val TYPING_PAUSE_MS = 300L
    }
}

/** Whether the phone can read tags, for the tag hint (05 7.1). */
enum class NfcState { Ready, Off, Missing }

internal fun nfcStateOf(context: Context): NfcState {
    val adapter = NfcAdapter.getDefaultAdapter(context) ?: return NfcState.Missing
    return if (adapter.isEnabled) NfcState.Ready else NfcState.Off
}

/** The phone's NFC state, read again whenever the screen is resumed (the person may switch NFC on in the system settings). */
@Composable
internal fun rememberNfcState(): NfcState {
    val context = LocalContext.current
    var state by remember { mutableStateOf(nfcStateOf(context)) }
    val owner = LocalLifecycleOwner.current
    DisposableEffect(owner) {
        val observer = LifecycleEventObserver { _, event -> if (event == Lifecycle.Event.ON_RESUME) state = nfcStateOf(context) }
        owner.lifecycle.addObserver(observer)
        onDispose { owner.lifecycle.removeObserver(observer) }
    }
    return state
}

/** Opens [url] in the phone's browser; a phone with none leaves the screen as it is. */
internal fun openInBrowser(context: Context, url: String) {
    try {
        context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    } catch (_: android.content.ActivityNotFoundException) {
    }
}

/** Opens the system's NFC settings. */
internal fun openNfcSettings(context: Context) {
    context.startActivity(Intent(Settings.ACTION_NFC_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
}

/**
 * The Spools screen (05 7.1): the search field, the Sealed only chip, the tag hint and the results. [onOpenSpool] opens
 * the spool screen with the row's summary. There is no search button: a search runs after a pause in typing and on
 * the keyboard's search key.
 */
@Composable
fun SearchScreen(
    vm: SearchViewModel,
    onOpenSpool: (SpoolSummary) -> Unit,
    onPairAgain: () -> Unit,
    modifier: Modifier = Modifier,
    nfc: NfcState = rememberNfcState(),
    online: Boolean = true,
    /** How many spools are running low, for the chip "Running low (n)"; null: no chip (the Hub has no such list). */
    lowCount: Int? = null,
    /** Whatever identifies the paired Hub's client; a new one makes the Checked out filter be asked again. */
    hubKey: Any? = null,
    onNfcSettings: (() -> Unit)? = null,
    /** Opens the scanner in label mode (Scan a label, 7.1); null: no button. */
    onScanLabel: (() -> Unit)? = null,
    spoolDetail: (@Composable (spool: SpoolSummary, embedded: Boolean, onClose: () -> Unit) -> Unit)? = null,
) {
    val ui by vm.state.collectAsStateWithLifecycle()
    val context = LocalContext.current
    val space = SpoolwardenTheme.space
    val colors = SpoolwardenTheme.colors
    LaunchedEffect(vm) { vm.loadSpoolmanLink() }
    // the Hub may have changed (a re-pair): what was learned of the old one goes, then the filter is asked of this one
    LaunchedEffect(vm, hubKey) {
        vm.forgetCheckedOut()
        vm.loadCheckedOut()
    }
    // a Return: the toast says where the spool went, with Undo for 10 seconds when the Hub offers one (R88 1.6)
    val toast = LocalToast.current
    val undoLabel = stringResource(R.string.common_undo)
    val returned = ui.returnToast
    LaunchedEffect(returned) {
        if (returned == null) return@LaunchedEffect
        vm.returnToastShown()
        toast?.showUndoable(returned.text, undoLabel, returned.event, undoHoldMs(context), vm::undoReturn)
    }
    // Download as CSV (R84 6.5): the file goes to the system share sheet
    val csv = ui.csvFile
    val csvSubject = stringResource(R.string.nav_spools)
    LaunchedEffect(csv) {
        if (csv != null) {
            vm.csvShown(
                when (shareCsv(context, csv, csvSubject)) {
                    CsvOutcome.Opened -> null
                    CsvOutcome.NoApp -> context.getString(R.string.printer_report_no_app)
                },
            )
        }
    }
    // Open Spoolman (R83 section 7) and Download as CSV: in the screen's menu; Open Spoolman only when the Hub gave the address
    val spoolmanUrl = ui.spoolmanUrl
    if (spoolmanUrl != null || vm.canExport) {
        ScreenTopBarActions {
            var open by remember { mutableStateOf(false) }
            Box {
                SwIconButton(R.drawable.sw_ic_more, stringResource(R.string.common_more_actions), { open = true })
                SwMenu(open, { open = false }) {
                    if (spoolmanUrl != null) SwMenuItem(SearchTexts.OPEN_SPOOLMAN, { open = false; openInBrowser(context, spoolmanUrl) })
                    if (vm.canExport) SwMenuItem(
                        if (ui.csvBusy) HistoryTexts.DOWNLOADING else HistoryTexts.DOWNLOAD_CSV,
                        {
                            open = false
                            csvTarget(context, csvFileName("spoolman-spools", null))?.let(vm::downloadCsv)
                                ?: vm.csvShown(HistoryTexts.CSV_NOT_SAVED)
                        },
                        enabled = !ui.csvBusy,
                    )
                }
            }
        }
    }
    val presentation = rememberDetailPresentation()
    val useCardDetail = presentation == DetailPresentation.Card && spoolDetail != null
    val useSheetDetail = presentation == DetailPresentation.Sheet && spoolDetail != null
    val usePaneDetail = presentation == DetailPresentation.Pane && spoolDetail != null
    var expandedIds by rememberSaveable { mutableStateOf(emptyList<Int>()) }
    var sheetSpoolId by rememberSaveable { mutableStateOf<Int?>(null) }
    var cachedSpool by remember { mutableStateOf<SpoolSummary?>(null) }
    val rowFocus = remember { mutableMapOf<Int, FocusRequester>() }
    var returnFocusId by remember { mutableStateOf<Int?>(null) }
    val view = LocalView.current
    LaunchedEffect(returnFocusId) {
        val id = returnFocusId ?: return@LaunchedEffect
        // Wait for the collapsed card row to re-enter the layout and focus tree.
        repeat(2) { withFrameNanos { } }
        rowFocus[id]?.let { requestDetailFocus(it, view) }
        returnFocusId = null
    }
    LaunchedEffect(presentation) {
        if (presentation == DetailPresentation.Screen && spoolDetail != null) {
            val activeId = sheetSpoolId ?: expandedIds.lastOrNull()
            if (activeId != null) {
                val target = ui.results.firstOrNull { it.id == activeId }
                    ?: cachedSpool?.takeIf { it.id == activeId }
                    ?: com.thompsonfabco.filamentkit.ui.spool.SpoolCache.get(activeId)
                sheetSpoolId = null
                expandedIds = emptyList()
                if (target != null) onOpenSpool(target)
            }
        }
    }
    val listBody: @Composable (Modifier) -> Unit = { listModifier ->
        Column(listModifier.fillMaxSize()) {
            // the progress line sits right under the field; the results stay while it runs
            Column(
                Modifier.weight(1f).fillMaxWidth().verticalScroll(rememberScrollState()).padding(space.s4),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Column(
                    Modifier.widthIn(max = SpoolwardenTheme.sizes.columnMax).fillMaxWidth(),
                    verticalArrangement = Arrangement.spacedBy(space.s3),
                ) {
                    Column {
                        OutlinedTextField(
                            value = ui.query,
                            onValueChange = vm::setQuery,
                            label = { Text(stringResource(R.string.spools_search_label)) },
                            leadingIcon = { SwIcon(R.drawable.sw_ic_search, null, tint = colors.muted) },
                            trailingIcon = if (ui.query.isNotEmpty()) {
                                { SwIconButton(R.drawable.sw_ic_close, stringResource(R.string.common_clear_search), vm::clear) }
                            } else {
                                null
                            },
                            singleLine = true,
                            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                            keyboardActions = KeyboardActions(onSearch = { vm.search() }),
                            modifier = Modifier.fillMaxWidth(),
                        )
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(space.s2)) {
                        ToggleChip(stringResource(R.string.spools_sealed_only), ui.sealedOnly) { vm.setSealedOnly(!ui.sealedOnly) }
                        // the Hub's running-low list (R80 section 7); no chip for a Hub that has none
                        if (lowCount != null || ui.lowOnly) {
                            ToggleChip(stringResource(R.string.running_low_chip, lowCount ?: ui.low.size), ui.lowOnly) { vm.setLowOnly(!ui.lowOnly) }
                        }
                        // spools with a remembered place (R88 1.6); no chip for a Hub without the filter
                        ui.checkedCount?.let { n ->
                            ToggleChip(CheckedOutTexts.chip(n), ui.checkedOnly) { vm.setCheckedOnly(!ui.checkedOnly) }
                        }
                    }
                    val openOrToggle: (SpoolSummary) -> Unit = { spool ->
                        when {
                            useCardDetail -> {
                                cachedSpool = spool
                                com.thompsonfabco.filamentkit.ui.spool.SpoolCache.put(spool)
                                expandedIds = if (spool.id in expandedIds) expandedIds - spool.id else expandedIds + spool.id
                            }
                            useSheetDetail || usePaneDetail -> {
                                cachedSpool = spool
                                com.thompsonfabco.filamentkit.ui.spool.SpoolCache.put(spool)
                                sheetSpoolId = spool.id
                            }
                            else -> onOpenSpool(spool)
                        }
                    }
                    if (ui.query.isBlank() && !ui.lowOnly && !ui.checkedOnly) TagHint(nfc, onNfcSettings ?: { openNfcSettings(context) })
                    if (onScanLabel != null && ui.query.isBlank() && !ui.lowOnly) {
                        SwButton(LabelScan.SCAN_BUTTON, onScanLabel, Modifier.fillMaxWidth(), icon = R.drawable.sw_ic_camera)
                    }
                    HubNoticeOf(ui.failure, onPairAgain)
                    HubNoticeOf(ui.csvFailure, onPairAgain)
                    (ui.csvProblem ?: ui.csvFailure?.let { hubFailureText(it) })?.let { NoticeCard(NoticeKind.Bad, it) }
                    if (ui.lowOnly) LowSpoolsList(ui, openOrToggle, online, onTryAgain = vm::loadLowSpools)
                    if (ui.checkedOnly) CheckedOutList(ui, openOrToggle, online, onReturn = vm::returnToHome, onPairAgain = onPairAgain, onTryAgain = vm::loadCheckedOut)
                    val searched = ui.searched
                    // the states every Hub screen has (05 3.2): the progress line, the error card, offline
                    if (!ui.lowOnly && !ui.checkedOnly) ScreenState(
                        loaded = searched != null,
                        loading = ui.busy,
                        isEmpty = searched != null && ui.results.isEmpty(),
                        empty = {
                            Column {
                                Text(stringResource(R.string.spools_empty, searched.orEmpty()), style = MaterialTheme.typography.bodyLarge)
                                if (ui.sealedOnly) {
                                    SwButton(stringResource(R.string.spools_clear_sealed), { vm.setSealedOnly(false) }, kind = SwButtonKind.Quiet)
                                }
                            }
                        },
                        error = ui.failure?.let { hubFailureText(it) },
                        onTryAgain = { if (ui.query.isNotBlank()) vm.search() },
                        offline = !online,
                        loadedSecondsAgo = secondsSince(ui.loadedAt),
                    ) {
                        SwCollection(
                            ui.results, inset = SpoolwardenTheme.space.s4, surface = true, visual = SpoolwardenTheme.sizes.swatchRowW + SpoolwardenTheme.space.s3,
                            tile = { _, spool ->
                                val requester = rowFocus.getOrPut(spool.id) { FocusRequester() }
                                SpoolTile(spool, Modifier.focusRequester(requester), onClick = { openOrToggle(spool) })
                            },
                            isExpanded = if (useCardDetail) ({ it.id in expandedIds }) else null,
                            detail = if (useCardDetail) {
                                { _, spool ->
                                    val close = {
                                        expandedIds = expandedIds - spool.id
                                        returnFocusId = spool.id
                                    }
                                    SwCardDetail(onClose = close) {
                                        spoolDetail(spool, true, close)
                                    }
                                }
                            } else {
                                null
                            },
                        ) { _, spool ->
                            val requester = rowFocus.getOrPut(spool.id) { FocusRequester() }
                            SpoolRow(
                                spool,
                                modifier = Modifier.focusRequester(requester),
                                onClick = { openOrToggle(spool) },
                            )
                        }
                    }
                }
            }
        }
    }
    val openSpool = sheetSpoolId?.let { id ->
        ui.results.firstOrNull { it.id == id }
            ?: cachedSpool?.takeIf { it.id == id }
            ?: com.thompsonfabco.filamentkit.ui.spool.SpoolCache.get(id)
    }
    if (usePaneDetail) {
        val close = {
            returnFocusId = openSpool?.id
            sheetSpoolId = null
        }
        SwPaneLayout(
            hasSelection = openSpool != null,
            emptyText = stringResource(R.string.detail_pane_empty_spool),
            onClose = close,
            modifier = modifier,
            list = { listBody(Modifier) },
            detail = { openSpool?.let { spoolDetail(it, true, close) } },
        )
    } else {
        listBody(modifier)
        if (useSheetDetail && openSpool != null) {
            val close = {
                returnFocusId = openSpool.id
                sheetSpoolId = null
            }
            SwSheetDetail(onClose = close) {
                spoolDetail(openSpool, true, close)
            }
        }
    }
}

/** The line under the chip while nothing is typed: how to open a spool by its tag, or why the phone cannot. */
@Composable
private fun TagHint(nfc: NfcState, onNfcSettings: () -> Unit) {
    val colors = SpoolwardenTheme.colors
    val space = SpoolwardenTheme.space
    Row(horizontalArrangement = Arrangement.spacedBy(space.s3), verticalAlignment = Alignment.CenterVertically) {
        SwIcon(R.drawable.sw_ic_tag, null, tint = colors.muted)
        Column(Modifier.weight(1f)) {
            Text(
                stringResource(
                    when (nfc) {
                        NfcState.Ready -> R.string.spools_tag_hint
                        NfcState.Off -> R.string.spools_nfc_off
                        NfcState.Missing -> R.string.spools_no_nfc
                    },
                ),
                style = MaterialTheme.typography.bodyMedium, color = colors.muted,
            )
            if (nfc == NfcState.Off) SwButton(stringResource(R.string.spools_nfc_settings), onNfcSettings, kind = SwButtonKind.Quiet)
        }
    }
}

/** A filter chip: the whole chip toggles, no check icon (08 10.3). */
@Composable
private fun ToggleChip(label: String, selected: Boolean, onClick: () -> Unit) {
    val colors = SpoolwardenTheme.colors
    FilterChip(
        selected = selected,
        onClick = onClick,
        label = { Text(label, style = MaterialTheme.typography.labelLarge) },
        modifier = Modifier.heightIn(min = SpoolwardenTheme.sizes.row),
        shape = SpoolwardenTheme.shapes.rS,
        colors = FilterChipDefaults.filterChipColors(
            containerColor = colors.surface, labelColor = colors.text,
            selectedContainerColor = colors.accentSoft, selectedLabelColor = colors.accentText,
        ),
        border = FilterChipDefaults.filterChipBorder(
            enabled = true, selected = selected, borderColor = colors.border, selectedBorderColor = colors.border,
            borderWidth = SpoolwardenTheme.sizes.hairline, selectedBorderWidth = SpoolwardenTheme.sizes.hairline,
        ),
    )
}

/** The spool a running-low row stands for, as the Spools list shows it. */
internal fun LowSpool.toSummary(): SpoolSummary = SpoolSummary(
    id = id, brand = "", name = filament, material = material, colorHex = color, location = location, remaining = remainingG,
)

/** The running-low list (R80 section 7): least filament first, each row with "Last one" or "<n> more in stock" under it. */
@Composable
private fun LowSpoolsList(ui: SearchUi, onOpen: (SpoolSummary) -> Unit, online: Boolean, onTryAgain: () -> Unit) {
    val space = SpoolwardenTheme.space
    val colors = SpoolwardenTheme.colors
    ScreenState(
        loaded = ui.lowLoaded,
        loading = ui.busy,
        isEmpty = ui.lowLoaded && ui.low.isEmpty(),
        empty = { Text(stringResource(R.string.running_low_empty), style = MaterialTheme.typography.bodyLarge) },
        error = ui.failure?.let { hubFailureText(it) },
        onTryAgain = onTryAgain,
        offline = !online,
        loadedSecondsAgo = secondsSince(ui.loadedAt),
    ) {
        Column(verticalArrangement = Arrangement.spacedBy(space.s2)) {
            ui.low.forEach { spool ->
                Column {
                    SpoolRow(spool.toSummary(), onClick = { onOpen(spool.toSummary()) })
                    Text(
                        if (spool.lastOne) {
                            stringResource(R.string.running_low_last_one)
                        } else {
                            pluralStringResource(R.plurals.running_low_in_stock, spool.inStock, spool.inStock)
                        },
                        color = if (spool.lastOne) colors.warn else colors.muted,
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
            }
        }
    }
}

/**
 * The checked-out list (R88 1.6): each row says where the spool is, its home and since when, with "Return to <home>"
 * under it. The Hub's refusal of a Return shows under the list in its own words.
 */
@Composable
private fun CheckedOutList(ui: SearchUi, onOpen: (SpoolSummary) -> Unit, online: Boolean, onReturn: (SpoolSummary) -> Unit, onPairAgain: () -> Unit, onTryAgain: () -> Unit) {
    val space = SpoolwardenTheme.space
    val colors = SpoolwardenTheme.colors
    ScreenState(
        loaded = ui.checkedLoaded,
        loading = ui.busy,
        isEmpty = ui.checkedLoaded && ui.checked.isEmpty(),
        empty = { Text(CheckedOutTexts.EMPTY, style = MaterialTheme.typography.bodyLarge) },
        error = ui.failure?.let { hubFailureText(it) },
        onTryAgain = onTryAgain,
        offline = !online,
        loadedSecondsAgo = secondsSince(ui.loadedAt),
    ) {
        Column(verticalArrangement = Arrangement.spacedBy(space.s2)) {
            ui.returnFailure?.let { HubFailureNotice(it, onPairAgain) }
            ui.checked.forEach { spool ->
                Column {
                    SpoolRow(spool, onClick = { onOpen(spool) })
                    CheckedOutRules.lines(spool).forEach {
                        Text(it, color = colors.muted, style = MaterialTheme.typography.bodySmall)
                    }
                    SwButton(
                        CheckedOutTexts.returnTo(spool.home), { onReturn(spool) },
                        kind = SwButtonKind.Quiet, enabled = ui.returningId == null, busy = ui.returningId == spool.id,
                    )
                }
            }
        }
    }
}
