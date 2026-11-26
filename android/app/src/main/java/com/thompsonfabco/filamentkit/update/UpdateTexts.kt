package com.thompsonfabco.filamentkit.update

import android.content.pm.PackageInstaller
import androidx.annotation.StringRes
import com.thompsonfabco.filamentkit.R
import com.thompsonfabco.filamentkit.ui.printers.Say

/** Why an update did not go through: one line each in 05-android-app 13, worded in the string catalogue ([text]). */
enum class UpdateFailure(@StringRes val text: Int) {
    HubUnreachable(R.string.app_update_fail_hub),
    NoUpdate(R.string.app_update_fail_none),
    CertificateChanged(R.string.app_update_fail_cert),
    NewerInstalled(R.string.app_update_fail_newer),
    Damaged(R.string.app_update_fail_damaged),
    Storage(R.string.app_update_fail_storage),
    Blocked(R.string.app_update_fail_blocked),
    AndroidVersion(R.string.app_update_fail_android),
    TimedOut(R.string.app_update_fail_timeout),
    LocalNetwork(R.string.app_update_fail_network),

    /** The check for an update failed for a reason that is none of the others. */
    CheckFailed(R.string.app_update_fail_check),
    Generic(R.string.app_update_fail_install),

    /** Signed by another key than the installed app: its own panel (with "What this means"). */
    Signature(R.string.app_update_signature),
}

/** Texts of the Home update banner (until its screen step) and of a failed install. */
object UpdateTexts {
    const val REFUSED_GUIDE_URL = "https://github.com/thompsonfabco/Spoolwarden/blob/main/docs/install-android-app.md#update-refused"

    /** The banner is showing the refusal of a differently signed update. */
    fun refused(ui: UpdateUi): Boolean = ui.failure == UpdateFailure.Signature

    /** Trying again is how a corrected server copy gets picked up. */
    fun installLabel(ui: UpdateUi): Say = when {
        ui.busy -> ui.progress?.let { Say.Words(R.string.app_update_downloading_percent, listOf(it)) } ?: Say.Words(R.string.app_update_downloading_unknown)
        refused(ui) -> Say.Words(R.string.common_try_again)
        else -> Say.Words(R.string.app_update_install_plain)
    }

    /** `version` is the server's release, the one the update comes with; the APK's own name is always "1.0". */
    fun bannerTitle(version: String?): Say =
        version?.trim()?.ifBlank { null }?.let { Say.Words(R.string.app_update_banner_version, listOf(it)) } ?: Say.Words(R.string.app_update_banner)

    /**
     * What went wrong with a finished install session, or null when there is nothing to say (it worked, it is
     * waiting for the user, or the user cancelled). The system's own [message] decides the signing case, because
     * [status] alone is not specific enough: Android folds several causes into one status. It is never told to
     * uninstall: a different key inside the in-app updater is the one sign of a forged APK. The answer is the
     * catalogue's id, so it needs no Context.
     */
    @StringRes
    fun installFailure(status: Int, message: String?): Int? = failureOf(status, message)?.text

    /** The same decision as [installFailure], as the value the update panel maps to the catalogue's words. */
    fun failureOf(status: Int, message: String?): UpdateFailure? {
        val m = message.orEmpty()
        return when {
            status == PackageInstaller.STATUS_SUCCESS || status == PackageInstaller.STATUS_PENDING_USER_ACTION ||
                status == PackageInstaller.STATUS_FAILURE_ABORTED -> null
            "UPDATE_INCOMPATIBLE" in m || "signatures do not match" in m -> UpdateFailure.Signature
            "VERSION_DOWNGRADE" in m -> UpdateFailure.NewerInstalled
            status == PackageInstaller.STATUS_FAILURE_INVALID -> UpdateFailure.Damaged
            status == PackageInstaller.STATUS_FAILURE_STORAGE -> UpdateFailure.Storage
            status == PackageInstaller.STATUS_FAILURE_BLOCKED -> UpdateFailure.Blocked
            status == PackageInstaller.STATUS_FAILURE_INCOMPATIBLE -> UpdateFailure.AndroidVersion
            status == PackageInstaller.STATUS_FAILURE_TIMEOUT -> UpdateFailure.TimedOut
            else -> UpdateFailure.Generic
        }
    }
}
