package com.thompsonfabco.filamentkit.ui.auth

import android.content.ActivityNotFoundException
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import com.thompsonfabco.filamentkit.R
import com.thompsonfabco.filamentkit.ui.theme.SpoolwardenTheme

/**
 * Sign-in screen for Enterprise Hubs: supports Local/LDAP credentials and Chrome Custom Tab SAML PKCE.
 */
@Composable
fun EnterpriseLoginScreen(
    viewModel: EnterpriseLoginViewModel,
    hubBaseUrl: String,
    sessionExpiredNotice: Boolean = false,
    onSignedIn: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val state by viewModel.state.collectAsState()
    val context = LocalContext.current
    var username by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }

    LaunchedEffect(Unit) {
        viewModel.loadCapabilities()
    }

    val methods = state.capabilities.methods
    val showPasswordForm = methods.isEmpty() || "local" in methods || "ldap" in methods
    val showSsoButton = "saml" in methods

    val space = SpoolwardenTheme.space
    Scaffold(modifier = modifier.fillMaxSize()) { innerPadding ->
        Column(
            modifier = Modifier
                .padding(innerPadding)
                .padding(space.s5)
                .fillMaxSize(),
            verticalArrangement = Arrangement.Center,
        ) {
            Text(
                text = stringResource(R.string.ent_signin_product_title),
                style = MaterialTheme.typography.headlineSmall,
            )
            if (sessionExpiredNotice) {
                Spacer(Modifier.height(space.s2))
                Text(
                    text = stringResource(R.string.ent_signin_session_expired),
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.error,
                )
            }
            if (state.error != null) {
                Spacer(Modifier.height(space.s2))
                Text(
                    text = state.error.orEmpty(),
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.error,
                )
            }
            if (showPasswordForm) {
                Spacer(Modifier.height(space.s4))
                OutlinedTextField(
                    value = username,
                    onValueChange = { username = it },
                    label = { Text(stringResource(R.string.ent_signin_username)) },
                    singleLine = true,
                    enabled = !state.busy,
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Next),
                    modifier = Modifier.fillMaxWidth(),
                )
                Spacer(Modifier.height(space.s3))
                OutlinedTextField(
                    value = password,
                    onValueChange = { password = it },
                    label = { Text(stringResource(R.string.ent_signin_password)) },
                    singleLine = true,
                    enabled = !state.busy,
                    visualTransformation = PasswordVisualTransformation(),
                    keyboardOptions = KeyboardOptions(
                        keyboardType = KeyboardType.Password,
                        imeAction = ImeAction.Done,
                    ),
                    keyboardActions = KeyboardActions(
                        onDone = { viewModel.loginWithPassword(username, password, onSignedIn) },
                    ),
                    modifier = Modifier.fillMaxWidth(),
                )
                Spacer(Modifier.height(space.s4))
                Button(
                    onClick = { viewModel.loginWithPassword(username, password, onSignedIn) },
                    enabled = !state.busy,
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    if (state.busy) {
                        CircularProgressIndicator(strokeWidth = 2.dp)
                    } else {
                        Text(stringResource(R.string.ent_signin_submit))
                    }
                }
            }
            if (showSsoButton) {
                Spacer(Modifier.height(space.s3))
                OutlinedButton(
                    onClick = {
                        val url = viewModel.buildSamlAuthUrl(hubBaseUrl)
                        val intent = Intent(Intent.ACTION_VIEW, Uri.parse(url)).apply {
                            putExtras(
                                Bundle().apply {
                                    putBinder("android.support.customtabs.extra.SESSION", null)
                                },
                            )
                        }
                        try {
                            context.startActivity(intent)
                        } catch (_: ActivityNotFoundException) {
                            viewModel.reportError(context.getString(R.string.ent_signin_failed))
                        }
                    },
                    enabled = !state.busy,
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Text(stringResource(R.string.ent_signin_sso))
                }
            }
        }
    }
}
