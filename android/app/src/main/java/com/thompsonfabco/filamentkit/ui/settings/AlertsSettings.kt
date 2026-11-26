package com.thompsonfabco.filamentkit.ui.settings

import android.Manifest
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.defaultMinSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.core.app.NotificationManagerCompat
import com.thompsonfabco.filamentkit.R
import com.thompsonfabco.filamentkit.alerts.AlertsConfig
import com.thompsonfabco.filamentkit.alerts.SwitchStep
import com.thompsonfabco.filamentkit.alerts.switchStep
import com.thompsonfabco.filamentkit.ui.components.LocalToast
import com.thompsonfabco.filamentkit.ui.components.NeedsHubReason
import com.thompsonfabco.filamentkit.ui.components.NoticeCard
import com.thompsonfabco.filamentkit.ui.components.NoticeKind
import com.thompsonfabco.filamentkit.ui.components.SwAction
import com.thompsonfabco.filamentkit.ui.components.SwButton
import com.thompsonfabco.filamentkit.ui.openAppSettings
import com.thompsonfabco.filamentkit.ui.printers.Say
import com.thompsonfabco.filamentkit.ui.printers.resolve
import com.thompsonfabco.filamentkit.ui.theme.SpoolwardenTheme
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.emptyFlow
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject

/** What the Alerts group does; the app wires each to its owner. */
internal class AlertsActions(
    /** The switch, already allowed to change: turning on starts the watching service, off stops it. */
    val onSwitch: (Boolean) -> Unit = {},
    val onTest: () -> Unit = {},
    val onRecent: () -> Unit = {},
    /** Asks Android for the local network permission (API 37+). */
    val requestLocalNetwork: () -> Unit = {},
    /** Saves a partial `PATCH /api/alerts` body to the Hub. */
    val onPatchConfig: (JsonObject) -> Unit = {},
    /** Feedback sentences for a snackbar: saved or why a save failed. */
    val said: Flow<Say> = emptyFlow(),
)

/** Which help the switch is showing after a refusal. */
private enum class Help { Notifications, LocalNetwork }

/**
 * Alerts: the switch "Alerts on this phone", Send a test alert, and Recent alerts. Turning it on asks
 * for Android's notification permission (API 33+) and the local network permission; when one is refused the switch
 * stays off and the help says what to allow.
 */
@Composable
internal fun AlertsSettings(facts: AlertsFacts, actions: AlertsActions, modifier: Modifier = Modifier) {
    val space = SpoolwardenTheme.space
    val context = LocalContext.current
    val toast = LocalToast.current
    LaunchedEffect(actions.said, toast) { actions.said.collect { toast?.show(it.resolve(context)) } }
    var help by remember { mutableStateOf<Help?>(null) }
    var testSent by remember { mutableStateOf(false) }

    fun notificationsAllowed() = NotificationManagerCompat.from(context).areNotificationsEnabled()

    // The permission answer: allowed goes on to the next step; refused leaves the switch off with the help.
    fun turnOn(afterNotifications: Boolean = false, ask: () -> Unit = {}) {
        when (switchStep(notificationsAllowed(), facts.localNetworkGranted)) {
            SwitchStep.TurnOn -> {
                help = null
                actions.onSwitch(true)
            }
            SwitchStep.AskNotifications -> {
                help = Help.Notifications
                if (!afterNotifications) ask()
            }
            SwitchStep.AskLocalNetwork -> {
                help = Help.LocalNetwork
                actions.requestLocalNetwork()
            }
        }
    }
    val askNotifications = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { turnOn(afterNotifications = true) }
    val ask: () -> Unit = {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) askNotifications.launch(Manifest.permission.POST_NOTIFICATIONS)
    }

    SettingsPage(modifier) {
        if (!facts.available) NoticeCard(NoticeKind.Info, stringResource(R.string.alerts_unavailable))
        SettingsPanel {
            AlertsSwitchRow(
                on = facts.on,
                onChange = { on ->
                    testSent = false
                    if (on) turnOn(ask = ask) else {
                        help = null
                        actions.onSwitch(false)
                    }
                },
            )
            val shown = help
            if (shown != null && !facts.on) {
                when (shown) {
                    Help.Notifications -> NoticeCard(
                        NoticeKind.Warn, stringResource(R.string.settings_alerts_denied_help),
                        actions = listOf(SwAction(stringResource(R.string.common_open_app_settings)) { context.openAppSettings() }),
                    )
                    Help.LocalNetwork -> NoticeCard(NoticeKind.Warn, stringResource(R.string.settings_alerts_local_network_help))
                }
            }
        }
        Column(verticalArrangement = Arrangement.spacedBy(space.s2)) {
            SwButton(
                stringResource(R.string.settings_alerts_test),
                { testSent = true; actions.onTest() },
                Modifier.fillMaxWidth(), enabled = facts.on,
            )
            if (testSent) Text(stringResource(R.string.settings_alerts_sent), color = SpoolwardenTheme.colors.muted, style = MaterialTheme.typography.bodySmall)
        }
        SwButton(stringResource(R.string.settings_alerts_recent), actions.onRecent, Modifier.fillMaxWidth())
        if (facts.available && facts.config != null) {
            HubAlertRulesSection(
                config = facts.config,
                enabled = !facts.offline,
                revision = facts.revision,
                onPatchConfig = actions.onPatchConfig,
            )
        }
    }
}

@Composable
private fun HubAlertRulesSection(
    config: AlertsConfig,
    enabled: Boolean,
    revision: Int,
    onPatchConfig: (JsonObject) -> Unit,
) {
    val space = SpoolwardenTheme.space
    GroupTitle(stringResource(R.string.alerts_rules_title))
    Text(stringResource(R.string.alerts_rules_hint), color = SpoolwardenTheme.colors.muted, style = MaterialTheme.typography.bodyMedium)
    if (!enabled) NeedsHubReason()
    SettingsPanel {
        for (type in config.types) {
            HubAlertSwitchRow(
                label = type.title.ifBlank { type.key },
                on = type.on,
                enabled = enabled,
            ) { on ->
                onPatchConfig(buildJsonObject { put("types", buildJsonObject { put(type.key, JsonPrimitive(on)) }) })
            }
            if (type.key == "running_low") {
                HubAlertSwitchRow(
                    label = stringResource(R.string.alerts_last_spool_only),
                    on = config.lastSpoolOnly,
                    enabled = enabled,
                    modifier = Modifier.padding(start = space.s4),
                ) { on ->
                    onPatchConfig(buildJsonObject { put("last_spool_only", JsonPrimitive(on)) })
                }
            }
        }
        SettingsDivider()
        ThresholdField(stringResource(R.string.alerts_running_low_g), config.runningLowG, revision, enabled) { n ->
            onPatchConfig(buildJsonObject { put("running_low_g", JsonPrimitive(n)) })
        }
        ThresholdField(stringResource(R.string.alerts_storage_warn_pct), config.storageWarnPercent, revision, enabled) { n ->
            onPatchConfig(buildJsonObject { put("storage_warn_percent", JsonPrimitive(n)) })
        }
        ThresholdField(stringResource(R.string.alerts_storage_bad_pct), config.storageBadPercent, revision, enabled) { n ->
            onPatchConfig(buildJsonObject { put("storage_bad_percent", JsonPrimitive(n)) })
        }
        ThresholdField(stringResource(R.string.alerts_humidity_high_pct), config.humidityHighPct, revision, enabled) { n ->
            onPatchConfig(buildJsonObject { put("humidity_high_pct", JsonPrimitive(n)) })
        }
    }
}

@Composable
private fun HubAlertSwitchRow(
    label: String,
    on: Boolean,
    enabled: Boolean,
    modifier: Modifier = Modifier,
    onChange: (Boolean) -> Unit,
) {
    val colors = SpoolwardenTheme.colors
    Row(
        modifier.fillMaxWidth().defaultMinSize(minHeight = SpoolwardenTheme.sizes.row)
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

@Composable
private fun ThresholdField(
    label: String,
    value: Int?,
    revision: Int,
    enabled: Boolean,
    onCommit: (Int) -> Unit,
) {
    var text by remember(value, revision) { mutableStateOf(value?.toString().orEmpty()) }
    fun commit() {
        val parsed = text.trim().toIntOrNull()
        if (parsed == null) {
            text = value?.toString().orEmpty()
        } else if (parsed != value) {
            onCommit(parsed)
        }
    }
    OutlinedTextField(
        value = text,
        onValueChange = { text = it.filter(Char::isDigit) },
        label = { Text(label) },
        singleLine = true,
        enabled = enabled,
        textStyle = SpoolwardenTheme.type.input,
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number, imeAction = ImeAction.Done),
        keyboardActions = KeyboardActions(onDone = { commit() }),
        modifier = Modifier.fillMaxWidth().onFocusChanged { if (!it.isFocused) commit() },
    )
}

/** The switch row of 02-components 5.6: the label, the switch, the state word; the hint under it at body size. */
@Composable
private fun AlertsSwitchRow(on: Boolean, onChange: (Boolean) -> Unit) {
    val colors = SpoolwardenTheme.colors
    val space = SpoolwardenTheme.space
    Column(verticalArrangement = Arrangement.spacedBy(space.s1)) {
        Row(
            Modifier.fillMaxWidth().defaultMinSize(minHeight = SpoolwardenTheme.sizes.row)
                .toggleable(value = on, role = Role.Switch, onValueChange = onChange),
            horizontalArrangement = Arrangement.spacedBy(space.s3),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(stringResource(R.string.settings_alerts_switch), Modifier.weight(1f), style = MaterialTheme.typography.bodyLarge)
            Switch(
                checked = on, onCheckedChange = null,
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
        Text(stringResource(R.string.settings_alerts_phone_hint), color = colors.muted, style = MaterialTheme.typography.bodyMedium)
    }
}
