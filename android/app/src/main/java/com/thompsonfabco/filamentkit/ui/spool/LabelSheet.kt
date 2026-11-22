package com.thompsonfabco.filamentkit.ui.spool

import android.annotation.SuppressLint
import android.content.Context
import android.print.PrintAttributes
import android.print.PrintManager
import android.util.Base64
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import com.thompsonfabco.filamentkit.R
import com.thompsonfabco.filamentkit.net.ApiError
import com.thompsonfabco.filamentkit.ui.components.SwButton
import com.thompsonfabco.filamentkit.ui.components.SwButtonKind
import com.thompsonfabco.filamentkit.ui.components.SwMenu
import com.thompsonfabco.filamentkit.ui.components.SwMenuItem
import com.thompsonfabco.filamentkit.ui.components.SwSheet
import com.thompsonfabco.filamentkit.ui.components.SwSkeleton
import com.thompsonfabco.filamentkit.ui.theme.SpoolwardenTheme
import kotlinx.coroutines.CancellationException

/**
 * The texts of Print a label (05-android-app 7.10) that are not in the string catalogue yet. Only the texts owner opens
 * `strings/en.json`; each text has a proposed key in the RC5 report and moves there with it.
 */
object PrintTexts {
    const val MENU = "Print a label"
    const val TITLE = "Print a label"
    const val PRINT = "Print"
    const val COULD_NOT = "Could not draw the label."

    fun address(address: String) =
        "The code holds this Hub's address, $address. If the Hub is renamed or moves to another port, " +
            "old labels still open in this app's scanner but not from a phone's camera."

    fun preview(id: Int, name: String, material: String, left: String) = "Label of spool $id: $name, $material, $left g"
}

/**
 * The label sizes of the Hub's drawing (`size=` of `GET api/spools/{id}/label.svg`) with their names (7.10, R85 of 22 Nov). A
 * sheet size answers one label of that stock's size, so the page is always one label.
 */
internal enum class LabelSize(val wire: String, val title: String, val widthMm: Float, val heightMm: Float) {
    Small("62x29", "62 x 29 mm", 62f, 29f),
    Medium("50x30", "50 x 30 mm", 50f, 30f),
    A4Small("a4-63x34", "A4 sheet of 24, 63.5 x 33.9 mm", 63.5f, 33.9f),
    A4Large("a4-70x37", "A4 sheet of 24, 70 x 37 mm", 70f, 37f),
    Letter("letter-66x25", "Letter sheet of 30, 2-5/8 x 1 in", 66.675f, 25.4f),
}

/** What the app asks Android to print on: the label's own size, in thousandths of an inch; no margins. */
internal fun labelMedia(size: LabelSize): PrintAttributes.MediaSize =
    PrintAttributes.MediaSize("sw-label-${size.wire}", size.title, mil(size.widthMm), mil(size.heightMm))

private fun mil(mm: Float): Int = Math.round(mm * 1000 / 25.4f)

/**
 * The page the print service gets: the Hub's drawing as an image (so nothing inside the drawing can run), as wide as the
 * page.
 */
internal fun labelHtml(svg: ByteArray): String =
    "<html><body style=\"margin:0\"><img style=\"width:100%;height:auto\" src=\"data:image/svg+xml;base64," +
        Base64.encodeToString(svg, Base64.NO_WRAP) + "\"></body></html>"

/** What the Hub drew: the [Loaded] SVG, or the text of why it did not ([Failed], blank: no word came). */
internal sealed interface LabelLoad {
    class Loaded(val svg: ByteArray) : LabelLoad
    data class Failed(val text: String) : LabelLoad
}

/** Asks for one label drawing ([ask] is `GET api/spools/{id}/label.svg?size=`). */
internal suspend fun loadLabel(ask: suspend () -> ByteArray): LabelLoad = try {
    LabelLoad.Loaded(ask())
} catch (e: CancellationException) {
    throw e
} catch (e: ApiError) {
    LabelLoad.Failed(e.message)
}

/** The Hub's address as a person reads it: `hub.local:8710` from `https://hub.local:8710`. */
internal fun hubAddress(baseUrl: String): String = baseUrl.substringAfter("://")

/** Where the sheet gets a drawing from and what address the label's code holds (the Hub's, as the app reaches it). */
class LabelSource(val svg: suspend (size: String) -> ByteArray, val address: () -> String)

/**
 * Print a label (05-android-app 7.10), a sheet over the spool screen: the size, the label as the Hub draws it, the address
 * the code holds, and Print, which hands the label to Android's print service (one label; nothing changes on the Hub).
 */
@SuppressLint("SetJavaScriptEnabled")
@Composable
internal fun PrintLabelSheet(spool: SpoolSummary, source: LabelSource, onDismiss: () -> Unit) {
    val space = SpoolwardenTheme.space
    val colors = SpoolwardenTheme.colors
    val context = LocalContext.current
    var size by remember { mutableStateOf(LabelSize.Small) }
    var load by remember { mutableStateOf<LabelLoad?>(null) }
    var retry by remember { mutableIntStateOf(0) }
    var choosing by remember { mutableStateOf(false) }
    var page by remember { mutableStateOf<WebView?>(null) }
    var shown by remember { mutableStateOf(false) }
    LaunchedEffect(size, retry) {
        load = null
        shown = false
        load = loadLabel { source.svg(size.wire) }
    }
    SwSheet(onDismiss = onDismiss) {
        Column(
            Modifier.fillMaxWidth().padding(horizontal = space.s4).padding(bottom = space.s4),
            verticalArrangement = Arrangement.spacedBy(space.s3),
        ) {
            Text(PrintTexts.TITLE, Modifier.semantics { heading() }, style = MaterialTheme.typography.titleMedium)
            Box {
                SwButton(size.title, { choosing = true }, Modifier.fillMaxWidth().heightIn(min = SpoolwardenTheme.sizes.row))
                SwMenu(choosing, { choosing = false }) {
                    LabelSize.entries.forEach { choice ->
                        SwMenuItem(choice.title, { choosing = false; size = choice }, checked = choice == size)
                    }
                }
            }
            val said = PrintTexts.preview(spool.id, spool.title, spool.material, spool.remaining?.let { formatGrams(it) }.orEmpty())
            when (val l = load) {
                null -> SwSkeleton(Modifier.aspectRatio(size.widthMm / size.heightMm), height = SpoolwardenTheme.sizes.columnMax / 4)
                is LabelLoad.Failed -> {
                    Text(
                        PrintTexts.COULD_NOT + l.text.takeIf { it.isNotBlank() }?.let { " $it" }.orEmpty(),
                        style = MaterialTheme.typography.bodyMedium, color = colors.bad,
                    )
                    SwButton(stringResource(R.string.common_try_again), { retry++ })
                }
                is LabelLoad.Loaded -> AndroidView(
                    modifier = Modifier.fillMaxWidth().aspectRatio(size.widthMm / size.heightMm).heightIn(max = SpoolwardenTheme.sizes.columnMax / 2)
                        .semantics { contentDescription = said },
                    factory = { ctx ->
                        WebView(ctx).apply {
                            settings.javaScriptEnabled = false
                            settings.allowFileAccess = false
                            webViewClient = object : WebViewClient() {
                                override fun onPageFinished(view: WebView, url: String?) {
                                    shown = true
                                }
                            }
                            page = this
                        }
                    },
                    update = { it.loadDataWithBaseURL(null, labelHtml(l.svg), "text/html", "utf-8", null) },
                    onRelease = { if (page === it) page = null; it.destroy() },
                )
            }
            Text(PrintTexts.address(source.address()), style = MaterialTheme.typography.bodySmall, color = colors.muted)
            SwButton(
                PrintTexts.PRINT, { printLabel(context, page, spool, size) }, Modifier.fillMaxWidth(),
                kind = SwButtonKind.Main, enabled = load is LabelLoad.Loaded && shown,
            )
        }
    }
}

/** Hands the label shown in [page] to Android's print service, which shows its own print dialog (no PDF is made here). */
private fun printLabel(context: Context, page: WebView?, spool: SpoolSummary, size: LabelSize) {
    val view = page ?: return
    val manager = context.getSystemService(PrintManager::class.java) ?: return
    val job = "Spool ${spool.id}"
    manager.print(
        job, view.createPrintDocumentAdapter(job),
        PrintAttributes.Builder().setMediaSize(labelMedia(size)).setMinMargins(PrintAttributes.Margins.NO_MARGINS).build(),
    )
}
