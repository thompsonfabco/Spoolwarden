package com.thompsonfabco.filamentkit.ui.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.defaultMinSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.navigation.NavDestination
import androidx.navigation.NavDestination.Companion.hasRoute
import com.thompsonfabco.filamentkit.R
import com.thompsonfabco.filamentkit.ui.components.LocalToast
import com.thompsonfabco.filamentkit.ui.components.NeedsHubReason
import com.thompsonfabco.filamentkit.ui.components.SwButton
import com.thompsonfabco.filamentkit.ui.theme.SpoolwardenTheme
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.emptyFlow
import kotlinx.serialization.Serializable

/** Settings > Weigh Station voice. */
@Serializable data object SettingsVoiceRoute

/** The top bar's title on the voice group's page; null on any other screen. */
internal fun voicePlaceTitle(destination: NavDestination?): String? =
    VoiceTexts.GROUP.takeIf { destination?.hasRoute(SettingsVoiceRoute::class) == true }

/** What the Weigh Station voice group does; the app wires each to the [VoiceModel]. */
internal class VoiceActions(
    /** A control was let go: save what differs from the Hub's values. */
    val onSave: (VoiceEdit) -> Unit = {},
    /** Test, with the values on the screen. */
    val onTest: (VoiceEdit) -> Unit = {},
    /** The sentences to show as a snackbar: the result of Test, or why a save did not take. */
    val said: Flow<String> = emptyFlow(),
)

/**
 * Settings > Weigh Station voice: Speak, Voice (read-only), Speaker (only for a voice
 * with several), Speed, Volume and Test. A value is saved when its control is let go; Test uses the values on the
 * screen. Offline the controls are disabled and the first says it needs the Hub.
 */
@Composable
internal fun VoiceSettingsPage(ui: VoiceUi, actions: VoiceActions, modifier: Modifier = Modifier) {
    val saved = ui.settings ?: return
    val toast = LocalToast.current
    LaunchedEffect(actions.said, toast) { actions.said.collect { toast?.show(it) } }
    // the controls show the Hub's values again after a save that did not take (the revision) or a new answer
    var edit by remember(saved, ui.revision) { mutableStateOf(VoiceEdit.of(saved)) }
    var speakerText by remember(saved, ui.revision) { mutableStateOf(saved.speaker.toString()) }
    val enabled = !ui.offline
    val space = SpoolwardenTheme.space

    fun saveSpeaker() {
        val n = speakerFrom(speakerText, saved.speakerRange)
        if (n == null) {
            speakerText = edit.speaker.toString()
        } else if (n != edit.speaker) {
            edit = edit.copy(speaker = n)
            actions.onSave(edit)
        }
    }

    SettingsPage(modifier) {
        SettingsPanel {
            SwitchRow(VoiceTexts.SPEAK, edit.speech, enabled) { on ->
                edit = edit.copy(speech = on)
                actions.onSave(edit)
            }
            FieldRow(VoiceTexts.VOICE, saved.voice)
            Text(voiceHint(saved), color = SpoolwardenTheme.colors.muted, style = MaterialTheme.typography.bodyMedium)
            if (speakerShown(saved)) {
                val range = saved.speakerRange!!
                Column(verticalArrangement = Arrangement.spacedBy(space.s1)) {
                    OutlinedTextField(
                        value = speakerText,
                        onValueChange = { speakerText = it.filter(Char::isDigit) },
                        label = { Text(VoiceTexts.SPEAKER) },
                        singleLine = true,
                        enabled = enabled,
                        textStyle = SpoolwardenTheme.type.input,
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number, imeAction = ImeAction.Done),
                        keyboardActions = androidx.compose.foundation.text.KeyboardActions(onDone = { saveSpeaker() }),
                        modifier = Modifier.width(SpoolwardenTheme.sizes.portField).onFocusChanged { if (!it.isFocused) saveSpeaker() },
                    )
                    Text(
                        VoiceTexts.speakerHint(range.last + 1),
                        color = SpoolwardenTheme.colors.muted, style = MaterialTheme.typography.bodyMedium,
                    )
                }
            }
            SliderRow(
                VoiceTexts.SPEED, edit.speed, SPEED_MIN.toFloat()..SPEED_MAX.toFloat(), SPEED_SLIDER_STEPS, enabled,
                snap = ::snapSpeed,
                onChange = { edit = edit.copy(speed = it) },
                onLetGo = { actions.onSave(edit) },
            )
            SliderRow(
                VoiceTexts.VOLUME, edit.volume, VOLUME_MIN.toFloat()..VOLUME_MAX.toFloat(), 0, enabled,
                snap = ::snapVolume,
                onChange = { edit = edit.copy(volume = it) },
                onLetGo = { actions.onSave(edit) },
            )
        }
        Column(verticalArrangement = Arrangement.spacedBy(space.s2)) {
            SwButton(
                VoiceTexts.TEST, { actions.onTest(edit) }, Modifier.fillMaxWidth(),
                enabled = enabled, busy = ui.testing,
            )
            if (ui.offline) NeedsHubReason()
        }
    }
}

/** The switch row of 02-components 5.6: the label, the switch, the state word. */
@Composable
private fun SwitchRow(label: String, on: Boolean, enabled: Boolean, onChange: (Boolean) -> Unit) {
    val colors = SpoolwardenTheme.colors
    Row(
        Modifier.fillMaxWidth().defaultMinSize(minHeight = SpoolwardenTheme.sizes.row)
            .toggleable(value = on, enabled = enabled, role = Role.Switch, onValueChange = onChange),
        horizontalArrangement = Arrangement.spacedBy(SpoolwardenTheme.space.s3),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(label, Modifier.weight(1f), style = MaterialTheme.typography.bodyLarge)
        Switch(
            checked = on, onCheckedChange = null, enabled = enabled,
            colors = SwitchDefaults.colors(
                checkedTrackColor = colors.accentText, checkedThumbColor = colors.surface, checkedBorderColor = colors.accentText,
                uncheckedTrackColor = colors.surface3, uncheckedThumbColor = colors.muted, uncheckedBorderColor = colors.borderStrong,
            ),
        )
        Text(
            stringResource(if (on) R.string.settings_hub_check_on else R.string.settings_hub_check_off),
            color = if (on) colors.accentText else colors.muted, style = SpoolwardenTheme.type.dataSmall,
        )
    }
}

/** A label with its value in percent over a slider; [snap] turns a position into a value, [onLetGo] saves. */
@Composable
private fun SliderRow(
    label: String,
    value: Int,
    range: ClosedFloatingPointRange<Float>,
    steps: Int,
    enabled: Boolean,
    snap: (Float) -> Int,
    onChange: (Int) -> Unit,
    onLetGo: () -> Unit,
) {
    val colors = SpoolwardenTheme.colors
    Column {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text(label, Modifier.weight(1f), style = MaterialTheme.typography.bodyLarge)
            Text("$value %", color = colors.muted, style = SpoolwardenTheme.type.data)
        }
        Slider(
            value = value.toFloat(), onValueChange = { onChange(snap(it)) }, onValueChangeFinished = onLetGo,
            valueRange = range, steps = steps, enabled = enabled,
            colors = SliderDefaults.colors(
                thumbColor = colors.accentText, activeTrackColor = colors.accentText, inactiveTrackColor = colors.surface3,
            ),
            modifier = Modifier.fillMaxWidth().semantics { contentDescription = label },
        )
    }
}
