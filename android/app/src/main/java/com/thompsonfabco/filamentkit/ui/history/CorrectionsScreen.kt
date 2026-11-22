package com.thompsonfabco.filamentkit.ui.history

import android.graphics.Bitmap
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedCard
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.Dp
import androidx.lifecycle.compose.LifecycleStartEffect
import androidx.lifecycle.viewmodel.compose.viewModel
import com.thompsonfabco.filamentkit.R
import com.thompsonfabco.filamentkit.net.Api
import com.thompsonfabco.filamentkit.ui.components.HubNotice
import com.thompsonfabco.filamentkit.ui.components.HubNoticeKind
import com.thompsonfabco.filamentkit.ui.components.LocalToast
import com.thompsonfabco.filamentkit.ui.components.NoticeCard
import com.thompsonfabco.filamentkit.ui.components.NoticeKind
import com.thompsonfabco.filamentkit.ui.components.ScreenState
import com.thompsonfabco.filamentkit.ui.components.SwAction
import com.thompsonfabco.filamentkit.ui.components.SwButton
import com.thompsonfabco.filamentkit.ui.components.SwEmptyState
import com.thompsonfabco.filamentkit.ui.theme.SpoolwardenTheme
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonElement

/** Corrections waiting (05-android-app 10.6, `recent/corrections`): what Share corrections keeps on the reader, each with Remove. */
@Composable
fun CorrectionsEntry(
    current: () -> Api,
    client: Any?,
    onOpenRecord: (String) -> Unit,
    onOpenPhoto: (String, Int) -> Unit,
    onPairAgain: () -> Unit,
) {
    val vm = viewModel(key = CORRECTIONS_KEY) { CorrectionsViewModel(CorrectionsBackend.of(current)) }
    CorrectionsScreen(vm, client, onOpenRecord, onOpenPhoto, onPairAgain)
}

@Composable
internal fun CorrectionsScreen(
    vm: CorrectionsViewModel,
    client: Any?,
    onOpenRecord: (String) -> Unit,
    onOpenPhoto: (String, Int) -> Unit,
    onPairAgain: () -> Unit,
) {
    val s by vm.state.collectAsState()
    val space = SpoolwardenTheme.space
    val toast = LocalToast.current
    LifecycleStartEffect(vm) {
        vm.refresh()
        onStopOrDispose { }
    }
    ReloadOnNewClient(client, vm::clientSeen)
    // out of reach: keep trying, the data on screen stays (05-android-app 3.2)
    LaunchedEffect(s.offline) {
        while (s.offline) {
            delay(RETRY_MS)
            vm.refresh()
        }
    }
    // the row has gone, so its result is a toast
    LaunchedEffect(s.removed) {
        if (s.removed) {
            vm.removedShown()
            toast?.show(CorrectionsTexts.REMOVED)
        }
    }
    val failure = s.error
    val offline = failure == RecentFailure.Offline
    val ago = remember(s.loadedAt, s.error) { s.loadedAt?.let { (System.currentTimeMillis() - it) / MS_PER_S } ?: 0L }
    Column(Modifier.fillMaxSize().padding(space.s4), verticalArrangement = Arrangement.spacedBy(space.s3)) {
        when {
            s.mismatch != null -> HubNotice(HubNoticeKind.CertificateChanged, onPairAgain)
            s.unauthorized -> HubNotice(HubNoticeKind.NoLongerPaired, onPairAgain)
        }
        if (s.readerDown) {
            NoticeCard(
                NoticeKind.Bad, CorrectionsTexts.READER_DOWN,
                actions = listOf(SwAction(stringResource(R.string.common_try_again), vm::refresh)),
            )
        }
        s.removeFailure?.let { NoticeCard(NoticeKind.Bad, failureText(it)) }
        ScreenState(
            loaded = s.loaded,
            loading = s.loading,
            modifier = Modifier.weight(1f),
            error = if (failure != null && !offline) failureText(failure) else null,
            onTryAgain = vm::refresh,
            offline = offline,
            loadedSecondsAgo = ago,
        ) {
            CorrectionsList(s, vm, onOpenRecord, onOpenPhoto)
        }
    }
}

private const val MS_PER_S = 1000L

@Composable
private fun CorrectionsList(
    s: CorrectionsState,
    vm: CorrectionsViewModel,
    onOpenRecord: (String) -> Unit,
    onOpenPhoto: (String, Int) -> Unit,
) {
    val space = SpoolwardenTheme.space
    LazyColumn(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(space.s3)) {
        if (s.rows.isEmpty() && s.notTaken.isEmpty()) {
            item {
                SwEmptyState(
                    icon = R.drawable.sw_ic_clock,
                    title = CorrectionsTexts.EMPTY_TITLE,
                    text = CorrectionsTexts.EMPTY_TEXT,
                )
            }
        } else if (s.rows.isNotEmpty()) {
            item {
                Column(verticalArrangement = Arrangement.spacedBy(space.s1)) {
                    Text(
                        CorrectionsTexts.head(s.count, RecentValues.megabytes(s.bytes)),
                        style = MaterialTheme.typography.titleSmall,
                    )
                    Text(
                        if (s.sharingOff) CorrectionsTexts.HEAD_OFF else CorrectionsTexts.HEAD_TEXT,
                        style = MaterialTheme.typography.bodyMedium,
                    )
                }
            }
        }
        items(s.rows, key = { it.id }) { row -> CorrectionCard(row, onRemove = { vm.remove(row.id) }, onOpenRecord = onOpenRecord, onOpenPhoto = onOpenPhoto) }
        items(s.notTaken) { NotTakenCard(it, onOpenRecord) }
        item { Text(CorrectionsTexts.FOOT, color = SpoolwardenTheme.colors.muted, style = MaterialTheme.typography.bodyMedium) }
    }
}

@Composable
private fun CorrectionCardFrame(content: @Composable () -> Unit) {
    val colors = SpoolwardenTheme.colors
    OutlinedCard(
        Modifier.fillMaxWidth(),
        shape = SpoolwardenTheme.shapes.r,
        colors = CardDefaults.outlinedCardColors(containerColor = colors.surface, contentColor = colors.text),
        border = BorderStroke(SpoolwardenTheme.sizes.hairline, colors.border),
    ) {
        Column(Modifier.fillMaxWidth().padding(SpoolwardenTheme.space.s3), verticalArrangement = Arrangement.spacedBy(SpoolwardenTheme.space.s2)) {
            content()
        }
    }
}

/** One correction (10.6): thumbnail, the filament, when and which reader, what changed, the note, and Remove. */
@Composable
private fun CorrectionCard(
    row: CorrectionRow,
    onRemove: () -> Unit,
    onOpenRecord: (String) -> Unit,
    onOpenPhoto: (String, Int) -> Unit,
) {
    val colors = SpoolwardenTheme.colors
    val space = SpoolwardenTheme.space
    val name = row.filament ?: CorrectionsTexts.UNNAMED
    val engine = engineText(row.engine)
    val time = RecentValues.time(row.at)
    val said = CorrectionsTexts.a11yRow(name, time, engine, row.changes.size)
    CorrectionCardFrame {
        Row(horizontalArrangement = Arrangement.spacedBy(space.s3)) {
            val photoOf = row.record?.takeIf { row.recordKept != false && row.photos > 0 }
            CorrectionThumb(row, name, photoOf?.let { { onOpenPhoto(it, 1) } })
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(space.s1)) {
                // the headline carries the whole spoken row; the line under it is then not read again
                val link = row.record?.takeIf { row.recordKept != false }
                if (link != null) {
                    Text(
                        name,
                        Modifier.fillMaxWidth().heightIn(min = SpoolwardenTheme.sizes.row).clickable(role = Role.Button) { onOpenRecord(link) }
                            .semantics { contentDescription = said },
                        color = colors.accentText, style = MaterialTheme.typography.titleSmall,
                    )
                } else {
                    Text(name, Modifier.semantics { contentDescription = said }, style = MaterialTheme.typography.titleSmall)
                }
                Text(
                    if (engine == null) time else CorrectionsTexts.rowMeta(time, engine),
                    Modifier.clearAndSetSemantics { }, color = colors.muted, style = SpoolwardenTheme.type.dataSmall,
                )
                if (row.recordKept == false) {
                    Text(CorrectionsTexts.NO_RECORD, color = colors.muted, style = MaterialTheme.typography.bodySmall)
                    Text(CorrectionsTexts.NO_PHOTOS, color = colors.muted, style = MaterialTheme.typography.bodySmall)
                }
            }
        }
        row.changes.forEach { c ->
            val field = fieldName(c.field)
            val read = correctionValue(c.field, c.read)
            val now = correctionValue(c.field, c.now)
            val line = CorrectionsTexts.change(field, read, now)
            val spoken = CorrectionsTexts.a11yChange(field, read, now)
            Text(line, Modifier.clearAndSetSemantics { contentDescription = spoken }, style = SpoolwardenTheme.type.dataSmall)
        }
        row.note?.let { Text(CorrectionsTexts.note(it), style = MaterialTheme.typography.bodyMedium) }
        val remove = if (row.removing) CorrectionsTexts.a11yRemoving(name) else CorrectionsTexts.a11yRemove(name)
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
            SwButton(
                if (row.removing) CorrectionsTexts.REMOVING else stringResource(R.string.common_remove), onRemove,
                Modifier.semantics { contentDescription = remove }, busy = row.removing,
            )
        }
    }
}

/** A record whose correction the reader did not take: it is not waiting, so there is nothing to remove. */
@Composable
private fun NotTakenCard(n: NotTaken, onOpenRecord: (String) -> Unit) {
    val colors = SpoolwardenTheme.colors
    CorrectionCardFrame {
        val name = n.filament ?: CorrectionsTexts.UNNAMED
        val link = recordIdOf(n.record)
        if (link != null) {
            Text(
                name,
                Modifier.fillMaxWidth().heightIn(min = SpoolwardenTheme.sizes.row).clickable(role = Role.Button) { onOpenRecord(link) },
                color = colors.accentText, style = MaterialTheme.typography.titleSmall,
            )
        } else {
            Text(name, style = MaterialTheme.typography.titleSmall)
        }
        n.at?.let { Text(RecentValues.time(it), color = colors.muted, style = SpoolwardenTheme.type.dataSmall) }
        RecentStatusLine(
            R.drawable.sw_st_triangle, CorrectionsTexts.notWaiting(n.reason), colors.warn, large = true,
        )
    }
}

@Composable
private fun engineText(engine: Engine?): String? = when (engine) {
    Engine.Local -> stringResource(R.string.settings_reader_badge_local)
    Engine.Cloud -> CorrectionsTexts.ENGINE_CLOUD
    null -> null
}

/** A value as the record shows it (unit after it), "(empty)" for nothing, and the colour with its "#". */
@Composable
private fun correctionValue(wire: String, v: JsonElement?): String {
    val field = RecentField.of(wire)
    val text = fieldValue(field, v) ?: return stringResource(R.string.common_field_empty_value)
    return if (field == RecentField.Colour) "#" + text.removePrefix("#") else text
}

/** The first photo's small picture (the reader sends it with the entry) with the photo count; a dashed box when no photo is kept. */
@Composable
private fun CorrectionThumb(row: CorrectionRow, name: String, onOpen: (() -> Unit)?, side: Dp = SpoolwardenTheme.sizes.thumbS) {
    val colors = SpoolwardenTheme.colors
    val shape = SpoolwardenTheme.shapes.rS
    if (row.photos == 0 && row.thumbnail == null) {
        Box(Modifier.size(side).dashedOutline(colors.borderStrong, shape, SpoolwardenTheme.space.s1, SpoolwardenTheme.sizes.hairline))
        return
    }
    val bitmap by produceState<Bitmap?>(null, row.id) {
        val bytes = row.thumbnail ?: return@produceState
        value = withContext(Dispatchers.Default) { decode(bytes, THUMB_SIDE_PX) }
    }
    val tap = if (onOpen == null) Modifier else Modifier.clickable(role = Role.Button, onClick = onOpen)
        .semantics { contentDescription = CorrectionsTexts.a11yPhotos(name, row.photos) }
    Box(Modifier.size(side).then(tap)) {
        val b = bitmap
        if (b == null) {
            Box(Modifier.size(side).clip(shape).background(colors.surface2))
        } else {
            Image(b.asImageBitmap(), null, Modifier.size(side).clip(shape), contentScale = ContentScale.Crop)
        }
        if (row.photos > 0) {
            Text(
                row.photos.toString(),
                Modifier.align(Alignment.BottomEnd).clearAndSetSemantics { }.background(colors.surface, shape)
                    .padding(horizontal = SpoolwardenTheme.space.s1),
                color = colors.text, style = SpoolwardenTheme.type.dataSmall,
            )
        }
    }
}

private const val THUMB_SIDE_PX = 192
