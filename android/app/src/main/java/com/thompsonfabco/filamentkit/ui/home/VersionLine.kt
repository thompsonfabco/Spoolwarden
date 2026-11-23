package com.thompsonfabco.filamentkit.ui.home

import android.content.Context
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import com.thompsonfabco.filamentkit.R

/** `App 0.4.3 · Server 0.4.3`; the server part is left out while a paired server's version is not known. */
internal fun versionLine(context: Context, app: String, paired: Boolean, server: String?): String = when {
    !paired -> context.getString(R.string.app_version_unpaired, app)
    server.isNullOrBlank() -> context.getString(R.string.pair_version, app)
    else -> context.getString(R.string.app_version_server, app, server)
}

/** The small version line, directly under the title of the home and pairing screens (inside their padded column). */
@Composable
internal fun VersionLine(text: String, modifier: Modifier = Modifier) {
    Text(
        text,
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = modifier,
    )
}
