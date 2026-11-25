package com.thompsonfabco.filamentkit.ui.home

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.material3.Button
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import com.thompsonfabco.filamentkit.tags.TagEvents
import com.thompsonfabco.filamentkit.ui.theme.SpoolwardenTheme

/** Debug builds only: emulator helper row rendered at the bottom of Home. */
@Composable
internal fun DebugHomeExtra() {
    SimulateTag()
}

/** Debug builds only: type a UID (e.g. `4:77:90:e`) and emit it as if the tag had been tapped. */
@Composable
private fun SimulateTag() {
    var uid by remember { mutableStateOf("4:77:90:e") }
    Row(horizontalArrangement = Arrangement.spacedBy(SpoolwardenTheme.space.s2), verticalAlignment = Alignment.CenterVertically) {
        OutlinedTextField(
            value = uid,
            onValueChange = { uid = it },
            label = { Text("Tag UID") },
            singleLine = true,
            modifier = Modifier.weight(1f),
        )
        Button(onClick = { TagEvents.emit(uid.trim()) }) { Text("Simulate tag") }
    }
}
