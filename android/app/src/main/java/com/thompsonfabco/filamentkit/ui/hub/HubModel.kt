package com.thompsonfabco.filamentkit.ui.hub

import androidx.annotation.StringRes
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.thompsonfabco.filamentkit.R
import com.thompsonfabco.filamentkit.net.GettingStarted
import com.thompsonfabco.filamentkit.net.HubAnswer
import com.thompsonfabco.filamentkit.net.HubCheckState
import com.thompsonfabco.filamentkit.ui.home.printersFailure
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.serialization.Serializable
import java.time.Instant
import java.time.LocalDate
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
import java.util.Locale

/** `hub-glance`: the read-only Hub screen, on the stack of the selected tab. */
@Serializable data object HubGlanceRoute

/** The groups of the Hub screen, in the order it shows them. */
enum class HubGroup(@StringRes val title: Int) {
    Status(R.string.devices_status_title),
    GettingStarted(R.string.start_title),
    Version(R.string.updates_version_title),
    Features(R.string.nav_features),
    Catalog(R.string.settings_group_catalog),
    Backups(R.string.storage_use_item_backups),
    LabelReader(R.string.settings_group_label_reader),
    Devices(R.string.nav_devices),
    RecentlyAdded(R.string.nav_recently_added),
}

/** Steps remain and the list is not hidden: the Getting started group and the line on Home are shown. */
fun gettingStartedShown(g: GettingStarted?): Boolean = g != null && !g.hidden && !g.finished && g.total > 0 && g.done < g.total

/** "Getting started on the Hub: 4 of 8 done" while [gettingStartedShown]; null (no line) otherwise. */
fun gettingStartedLine(g: GettingStarted?): HubText? =
    if (g != null && gettingStartedShown(g)) HubText.Words(R.string.hub_getting_started_line, listOf(g.done, g.total)) else null

/** One line of a group. */
sealed interface HubLine {
    /** A label over a value. */
    data class Field(val label: HubText, val value: HubText) : HubLine

    /** A row of Status: the Hub's own text and its fix line. */
    data class Check(val state: HubCheckState, val text: String, val fix: String?) : HubLine

    /** A sentence on its own. */
    data class Note(val text: HubText) : HubLine
}

/** A group with what it shows. */
data class HubSection(val group: HubGroup, val lines: List<HubLine>)

/**
 * The Hub's time as an instant: a number is whole seconds since 1970 (what the Hub sends now), a text is ISO 8601 with
 * an offset (an older Hub); null when it is neither.
 */
internal fun hubInstant(raw: String?): Instant? {
    val text = raw?.trim().orEmpty()
    if (text.isEmpty()) return null
    text.toDoubleOrNull()?.let { return if (it.isFinite()) runCatching { Instant.ofEpochSecond(it.toLong()) }.getOrNull() else null }
    return try {
        OffsetDateTime.parse(text).toInstant()
    } catch (_: Exception) {
        null
    }
}

/** [raw] (the Hub's time: ISO 8601 with offset; seconds since 1970 are read too) in the phone's zone and language; the text as given when it is not a time. */
fun hubTime(raw: String?, zone: ZoneId = ZoneId.systemDefault(), locale: Locale = Locale.getDefault()): String? {
    if (raw.isNullOrBlank()) return null
    val at = hubInstant(raw) ?: return raw
    return DateTimeFormatter.ofLocalizedDateTime(FormatStyle.MEDIUM, FormatStyle.SHORT).withLocale(locale).format(at.atZone(zone))
}

/** Like [hubTime], the day only ("Nov 17, 2026"); a plain `YYYY-MM-DD` text (a library entry's `added`) is read as that day. */
fun hubDay(raw: String?, zone: ZoneId = ZoneId.systemDefault(), locale: Locale = Locale.getDefault()): String? {
    if (raw.isNullOrBlank()) return null
    val format = DateTimeFormatter.ofLocalizedDate(FormatStyle.MEDIUM).withLocale(locale)
    runCatching { return format.format(LocalDate.parse(raw.trim())) }
    val at = hubInstant(raw) ?: return raw
    return format.format(at.atZone(zone))
}

internal fun deviceKindText(kind: String): HubText = when (kind) {
    "phone" -> HubText.Words(R.string.devices_kind_phone)
    "pc" -> HubText.Words(R.string.devices_kind_pc)
    "station" -> HubText.Words(R.string.devices_kind_station)
    "web" -> HubText.Words(R.string.devices_kind_web)
    "reader" -> HubText.Words(R.string.devices_kind_reader)
    "builtin" -> HubText.Words(R.string.hub_kind_builtin)
    else -> HubText.FromHub(kind)
}

private fun words(@StringRes id: Int) = HubText.Words(id)

private fun fromHub(text: String) = HubText.FromHub(text)

private fun onOff(on: Boolean) = words(if (on) R.string.status_on else R.string.status_off)

/**
 * What the screen shows, group by group, in order. A group the Hub left out (no part, or an empty list) is not shown;
 * Getting started only while [gettingStartedShown]. Nothing here is built from an address or a secret: the Hub redacts.
 */
fun hubSections(h: HubAnswer, time: (String?) -> String? = { hubTime(it) }): List<HubSection> = buildList {
    if (h.checks.isNotEmpty()) {
        add(HubSection(HubGroup.Status, h.checks.map { HubLine.Check(it.state, it.text, it.fix) }))
    }
    val started = h.gettingStarted
    if (started != null && gettingStartedShown(started)) {
        add(HubSection(HubGroup.GettingStarted, listOf(HubLine.Note(HubText.Words(R.string.start_count, listOf(started.done, started.total))))))
    }
    add(HubSection(HubGroup.Version, buildList {
        add(HubLine.Field(words(R.string.updates_installed), fromHub(h.version)))
        h.newest?.let { n ->
            add(HubLine.Field(words(R.string.hub_newest), fromHub(n.version)))
            time(n.checkedAt)?.let { add(HubLine.Field(words(R.string.updates_last_checked), fromHub(it))) }
        }
        if (h.updateAvailable) add(HubLine.Note(words(R.string.hub_update_available)))
    }))
    if (h.features.isNotEmpty()) {
        add(HubSection(HubGroup.Features, h.features.map { HubLine.Field(fromHub(it.name), onOff(it.on)) }))
    }
    h.catalog?.let { c ->
        add(HubSection(HubGroup.Catalog, listOf(
            HubLine.Field(words(R.string.updates_version_title), c.version?.let(::fromHub) ?: words(R.string.hub_not_known)),
            HubLine.Field(words(R.string.updates_last_checked), time(c.checkedAt)?.let(::fromHub) ?: words(R.string.common_never)),
        )))
    }
    h.backup?.let { b ->
        add(HubSection(HubGroup.Backups, listOf(
            HubLine.Field(words(R.string.hub_last_backup), time(b.lastAt)?.let(::fromHub) ?: words(R.string.common_never)),
            HubLine.Field(words(R.string.settings_backups_nightly), onOff(b.nightly)),
        )))
    }
    h.reader?.let { r ->
        add(HubSection(HubGroup.LabelReader, listOf(
            HubLine.Field(words(R.string.hub_engine), fromHub(r.engine)),
            HubLine.Field(words(R.string.hub_key), words(if (r.keySet) R.string.status_set else R.string.status_not_set)),
        )))
    }
    if (h.devices.isNotEmpty()) {
        add(HubSection(HubGroup.Devices, h.devices.map { d ->
            val parts = buildList {
                if (d.kind.isNotBlank()) add(deviceKindText(d.kind))
                add(d.version?.let(::fromHub) ?: words(R.string.devices_version_unknown))
                time(d.lastSeen)?.let { add(HubText.Words(R.string.hub_device_last_seen, listOf(it))) }
                if (d.outOfDate) add(words(R.string.hub_out_of_date))
            }
            HubLine.Field(
                if (d.thisOne) HubText.Words(R.string.hub_device_this, listOf(d.name)) else fromHub(d.name),
                HubText.Joined(parts),
            )
        }))
    }
    h.recordsKept?.let { add(HubSection(HubGroup.RecentlyAdded, listOf(HubLine.Field(words(R.string.settings_reader_keep), fromHub(it.toString()))))) }
}

/** What the Hub screen and Home's line draw from: [hub] is the last answer, kept while a newer read fails. */
data class HubUi(
    val hub: HubAnswer? = null,
    val loading: Boolean = false,
    /** A read has finished (with an answer, a 404 or a failure). */
    val loaded: Boolean = false,
    /** 404: an older Hub without `GET api/hub`. */
    val unavailable: Boolean = false,
    /** The Hub's own refusal, if that was the failure. */
    val message: String? = null,
    /** The failure was no route to the Hub, or an answer not understood, with no message of the Hub's. */
    val failed: Boolean = false,
    /** When [hub] was read (epoch millis), for the offline notice. */
    val loadedAt: Long? = null,
)

/**
 * The Hub's `GET api/hub`. [load] asks each time (it resolves the session's current client, so a re-pair reaches it) and
 * returns null for an older Hub (404); a failed read keeps the answer already shown.
 */
class HubViewModel(
    private val load: suspend () -> HubAnswer?,
    private val now: () -> Long = System::currentTimeMillis,
) : ViewModel() {
    private val _state = MutableStateFlow(HubUi())
    val state: StateFlow<HubUi> = _state.asStateFlow()

    fun refresh() {
        if (_state.value.loading) return
        _state.update { it.copy(loading = true) }
        viewModelScope.launch {
            try {
                val answer = load()
                _state.value = if (answer == null) {
                    HubUi(loaded = true, unavailable = true)
                } else {
                    HubUi(hub = answer, loaded = true, loadedAt = now())
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                val failure = printersFailure(e)
                _state.update { it.copy(loading = false, loaded = true, failed = true, message = failure.message) }
            }
        }
    }
}
