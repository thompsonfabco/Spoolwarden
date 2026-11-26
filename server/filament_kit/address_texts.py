"""The texts of the address and first-run phase that the server builds or that the client steps (Hub
page, apps) name by key: the Hub's port, Getting started, the guided printer, the Label Reader lead-in and the Checks
rows of the satellites.

`MESSAGES` holds every text under its key in strings/en.json, first letter included; the catalogue holds exactly these
(test_af5_texts keeps them equal). The sentences of the installer stay literals in install/install.sh and apply.py.
The texts of hubport.py, station_client.py, tagsources.py and local_reader.py () are kept equal to
their catalogue keys by the same test (`MIRRORED`), so the Hub page and the apps name one key for each.

{product} and {command} are the product's and the command's name (the catalogue holds app_name and command_name once);
a plural text takes its form from `count`.
"""
from __future__ import annotations

from . import output

PRODUCT, COMMAND = "Spoolwarden", "spoolwarden"

MESSAGES: dict[str, str | dict] = {
    # ---- the Hub's port: Settings > Network and addresses, `port`
    "port_row_label": "Hub port",
    "port_change": "Change port",
    "port_dialog_title": "Move the Hub to port {port}?",
    "port_dialog_field": "Port",
    "port_dialog_body": "Pair your phones and the Slicer Sync PC again. A Weigh Station on another machine must be "
                        "paired again. Tag Readers set up for this Hub must be set up again.",
    "port_moving": "Moving the Hub to port {port}…",
    "port_done": "The Hub is on port {port}: {address}",
    "port_show": "Hub port: {port}",
    "port_show_address": "Address: {address}",
    "port_fix_other": "choose another port: sudo {command} port 8710",
    "port_error_not_a_number": "'{text}' is not a port number.",
    "port_error_range": "Port {port} is not allowed: use 1024 to 65535.",
    "port_error_range_change": "Port {port} is not allowed: use 443, or 1024 to 65535.",
    "port_error_taken": "Port {port} is already in use on this machine.",
    "port_error_unsure": "Port {port} could not be checked: this machine did not say whether it is free.",
    "port_changed": "The Hub now uses port {port}.",
    "port_paired_found": "Phones and the Slicer Sync PC find the Hub again on your network.",
    "port_paired_again": "Pair your phones and the Slicer Sync PC again.",
    "port_paired_station": "A Weigh Station on another machine must be paired again. Tag Readers set up for this Hub must be set up again.",
    "port_remote_refusal": "Remote access needs port 443, and the Hub is using it. Move the Hub to another port first: "
                           "Settings > Network and addresses, Hub port.",
    "port_remote_refusal_short": "Remote access needs port 443, and the Hub is using it",
    "port_remote_on_443": "Remote access is on and uses port 443, so the Hub cannot take it.",
    "port_remote_fix": "sudo {command} port 8710",

    # ---- Getting started: the panel on the Hub page, the block of `status`
    "start_title": "Getting started",
    "start_count": "{done} of {total} done",
    "start_line": "Getting started: {done} of {total} done",
    "start_state_done": "Done",
    "start_state_next": "Next",
    "start_state_not_yet": "Not yet",
    "start_show": "Show",
    "start_hide": "Hide this list",
    "start_all_set": "Completed!",
    "start_restore_row": "Moving from another computer? Restore its backup",
    "start_restore_button": "Restore a backup",
    "start_step_name": "Name this Hub",
    "start_step_storage": "Check storage locations",
    "start_step_features": "Choose features",
    "start_step_reader": "Install the Label Reader",
    "start_step_key": "Set the Anthropic API key",
    "start_step_printer": "Add a printer",
    "start_step_slots": "Map its slots",
    "start_step_phone": "Use a phone",
    "start_step_passphrase": "Set the web passphrase",
    "start_step_pc": "Pair Slicer Sync",
    "start_step_spool": "Add your first spool",
    "start_step_backup": "Make a first backup",
    "start_open_settings": "Open settings",
    "start_go_features": "Go to Features",
    "start_go_devices": "Go to Devices",
    "start_open_web_app": "Open the web app",
    "start_do_from_phone": "Do this from your phone.",
    "start_keep_name": "Keep \"{name}\"",
    "start_keep_storage": "Keep the defaults",
    "start_keep_features": "Keep these",
    "start_line_name": "The Hub is called \"{name}\".",
    "start_line_storage": {"one": "{count} storage location.", "other": "{count} storage locations."},
    "start_line_features": {"one": "{count} feature is on.", "other": "{count} features are on."},
    "start_line_reader_done": "Installed: version {version}.",
    "start_line_reader_remote": "The reader at {address} is used.",
    "start_line_reader_open": "Not installed yet.",
    "start_line_key_done": "The key is set.",
    "start_line_key_open": "No key is set yet.",
    "start_line_printer_done": {"one": "{count} printer.", "other": "{count} printers."},
    "start_line_printer_open": "No printer yet.",
    "start_line_slots_done": "A slot map is saved.",
    "start_line_slots_open": "No slot map is saved yet.",
    "start_line_slots_no_printer": "Add a printer first.",
    "start_line_phone_done": "A phone is paired.",
    "start_line_phone_passphrase": "The web passphrase is set.",
    "start_line_phone_open": "No phone is paired yet.",
    "start_line_passphrase_done": "The passphrase is set.",
    "start_line_passphrase_open": "Not set.",
    "start_line_pc_done": "A PC is paired.",
    "start_line_pc_open": "No PC is paired yet.",
    "start_line_spool_done": "Spoolman has spools.",
    "start_line_spool_open": "Spoolman has no spools yet.",
    "start_line_spool_unknown": "Spoolman did not answer.",
    "start_line_backup_done": "A backup exists.",
    "start_line_backup_open": "No backup yet.",
    "start_line_marked_done": "Marked done",
    "cli_start_done": "done",
    "cli_start_next": "next",
    "cli_start_not_yet": "not yet",
    "cli_start_restore": "Moving from another computer? Restore its backup: sudo {command} restore <zip>",

    # ---- Add a printer, guided: the Hub page's drawer and `printer add`
    "printer_guided_title": "Add a printer",
    "printer_guided_which": "Which printer?",
    "printer_guided_found": "Found on your network",
    "printer_guided_looking": "Looking…",
    "printer_guided_none": "None found. Type its address in the next step.",
    "printer_guided_bambu_by_address": "Bambu Lab printers are added by address.",
    "printer_guided_use": "Use this",
    "printer_guided_access_hint": "On the printer: Settings > Network/LAN > Access Code",
    "printer_guided_serial": "Serial number",
    "printer_guided_serial_hint": "Printed on the printer; needed when the address was typed.",
    "printer_guided_serial_missing": "Type the printer's serial number.",
    "printer_guided_testing": "Testing…",
    "printer_guided_test_failed": "The Hub could not reach a Klipper printer at {address}.",
    "printer_guided_add_anyway": "Add anyway",
    "printer_guided_try_again": "Try again",
    "printer_guided_map_title": "Map its slots now?",
    "printer_guided_map_body": "Mapping tells the Hub which slot is which. Nothing heats or moves.",
    "printer_guided_map_later": "Later",
    "cli_printer_add_looking": "Looking for printers on your network…",
    "cli_printer_add_found": "Found on your network:",
    "cli_printer_add_found_row": "  {number}  {name}  {family}  {address}",
    "cli_printer_add_pick": "Use one of these? Number, or Enter to type an address: ",
    "cli_printer_add_none": "None found. Type its address.",
    "cli_printer_add_asking": "Asking {address}…",
    "cli_printer_add_anyway": "Add anyway?",
    "cli_printer_add_not_added": "Nothing was added.",
    "cli_printer_map_ask": "Map its slots now?",
    "cli_printer_map_later": "Map them later: sudo {command} printer map \"{name}\"",

    # ---- the Label Reader lead-in
    "features_reader_lead_in": "After this, install the reader in Settings > Label Reader (a download).",
    "features_reader_not_installed": "Not installed yet",
    "features_reader_finish": "Finish in Settings",
    "features_reader_switched_on": "Label Reader switched on. Install it here to finish.",
    "reader_not_installed": "The Label Reader is switched on but not installed.",
    "cli_next": "Next: {step}",

    # ---- Checks rows of the satellites (the design, command line)
    "check_station_not_https": "The Weigh Station {name} is not using HTTPS. Update it.",
    "check_station_not_https_fix": "Update it: on that machine run sudo {command} update.",
    "check_station_not_https_at": "The Weigh Station at {address} is not using HTTPS.",
    "check_station_not_https_why": "The Weigh Station at {address} is not using HTTPS: it could not make its "
                                   "certificate ({reason}).",
    "check_station_not_https_why_fix": "Put right what the reason names on that machine, then restart the Weigh Station.",
    "check_devices_out_of_date": {"one": "{count} device is out of date.", "other": "{count} devices are out of date."},
    "check_devices_out_of_date_fix": "see Devices on the Hub page",
    "check_station_cert_changed": "The Weigh Station's certificate is not the one the Hub trusts.",
    "check_station_cert_fix": "Restart the Weigh Station so it tells the Hub its new certificate "
                              "(sudo systemctl restart filament-kit-station), or pair it again.",
    "check_tag_not_https": "The tag source {address} is not using HTTPS.",
    "check_tag_cert_changed": "The certificate of the tag source {address} has changed.",
    "check_tag_cert_fix": "Save the tag source again to trust the new certificate.",
    "check_reader_needs_https": "A Label Reader on another machine needs an https address.",
    "check_reader_not_https_note": "The Label Reader at {address} is not using HTTPS. A later Label Reader version "
                                   "adds it.",
    "check_reader_not_https": "The Label Reader at {address} is not using HTTPS.",
    "check_reader_not_https_fix": "Update the Label Reader on that machine, then change its address to https:// in "
                                  "Settings > Label Reader.",
    "check_reader_cert_changed": "The Label Reader at {address} presents a different certificate than the one the Hub "
                                 "trusts.",
    "check_reader_cert_fix": "Press Test in Settings > Label Reader to trust the new certificate.",
}

# What the code of and holds under a catalogue key: {key: (module, constant)}. The catalogue holds these texts
# once for the Hub page and the apps; test_af5_texts keeps each constant equal to its key (a `{command}` filled in, a
# `{text!r}` as `'{text}'`). The Checks rows read these constants (one text source), and the constants hold the wording
# of the design, so the tag-source rows are here too.
MIRRORED = {
    "port_error_not_a_number": ("hubport", "TEXT_NOT_A_NUMBER"),
    "port_error_range": ("hubport", "TEXT_RANGE"),
    "port_error_range_change": ("hubport", "TEXT_RANGE_CHANGE"),
    "port_error_taken": ("hubport", "TEXT_TAKEN"),
    "port_error_unsure": ("hubport", "TEXT_UNSURE"),
    "port_changed": ("hubport", "TEXT_CHANGED"),
    "port_paired_found": ("hubport", "TEXT_PAIRED_FOUND"),
    "port_paired_again": ("hubport", "TEXT_PAIRED_AGAIN"),
    "port_paired_station": ("hubport", "TEXT_PAIRED_STATION"),
    "port_dialog_body": ("hubport", "TEXT_PAIRED_DEVICES"),
    "port_remote_refusal": ("hubport", "TEXT_REMOTE_REFUSAL"),
    "port_remote_refusal_short": ("hubport", "TEXT_REMOTE_REFUSAL_SHORT"),
    "port_remote_on_443": ("hubport", "TEXT_REMOTE_ON_443"),
    "port_remote_fix": ("hubport", "TEXT_REMOTE_FIX"),
    "check_station_not_https": ("station_client", "TEXT_STATION_NOT_HTTPS"),
    "check_station_not_https_at": ("station_client", "TEXT_STATION_NOT_HTTPS_AT"),
    "check_station_not_https_why": ("station_client", "TEXT_STATION_NOT_HTTPS_WHY"),
    "check_station_cert_changed": ("station_client", "TEXT_STATION_CERT_CHANGED"),
    "check_station_cert_fix": ("station_client", "TEXT_STATION_CERT_FIX"),
    "check_tag_not_https": ("tagsources", "TEXT_NOT_HTTPS"),
    "check_tag_cert_changed": ("tagsources", "TEXT_CERT_CHANGED"),
    "check_tag_cert_fix": ("tagsources", "TEXT_CERT_FIX"),
    "check_reader_needs_https": ("local_reader", "TEXT_NEEDS_HTTPS"),
    "check_reader_not_https_note": ("local_reader", "TEXT_NOT_HTTPS_NOTE"),
    "check_reader_not_https": ("local_reader", "TEXT_NOT_HTTPS_ROW"),
    "check_reader_not_https_fix": ("local_reader", "TEXT_NOT_HTTPS_FIX"),
    "check_reader_cert_changed": ("local_reader", "TEXT_CERT_CHANGED"),
    "check_reader_cert_fix": ("local_reader", "TEXT_CERT_FIX"),
}


def say(key: str, /, **fill) -> str:
    """The text of `key` with its {names} filled in; a plural text takes its form from `count`. {product} and {command}
    are the product's and the command's name, as in the generated catalogues."""
    text = MESSAGES[key]
    if isinstance(text, dict):
        text = text["one" if fill.get("count") == 1 else "other"]
    return output.fill(text, product=PRODUCT, command=COMMAND, **fill)


REASON_LIMIT = 200
_ANSI = None


def clean_line(text, limit: int = REASON_LIMIT) -> str:
    """Text a satellite sent (a station's `plain=<reason>`), as one clean line: ANSI escape sequences gone, every control
    and format character (Unicode Cc, Cf: newlines, tabs, NUL) turned into a space, runs of spaces collapsed, at most
    `limit` characters and cut with an ellipsis."""
    global _ANSI
    import re
    import unicodedata
    if _ANSI is None:
        _ANSI = re.compile("\x1b\\[[0-?]*[ -/]*[@-~]|\x1b\\][^\x07\x1b]*(?:\x07|\x1b\\\\)?|\x1b[@-Z\\\\-_]")
    s = _ANSI.sub("", str(text if text is not None else ""))
    s = "".join(" " if unicodedata.category(c) in ("Cc", "Cf") else c for c in s)
    s = " ".join(s.split())
    return s if len(s) <= limit else s[:limit - 1].rstrip() + "…"
