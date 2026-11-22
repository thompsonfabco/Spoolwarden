"""The catalogue of event kinds (ruling R80 section 2, docs/design 03).

A published contract from the first release that has it: kinds and value names are added, never renamed or removed
(the Home Assistant integration and `--json` readers rely on it; test_event_kinds.py pins every name). The catalogue
holds no texts: titles and sentences are built by each interface from the kind and the values (EA3).

A kind may carry a condition (R80 section 3): `raises` a lasting state, or `clears` one. The key of a condition is
its `base`, plus ":<subject id>" when the kind is `keyed` (print-paused:<printer id>, spool-low:<spool id>); the
`check` kinds take the row key instead of the subject id.
"""
from __future__ import annotations

from dataclasses import dataclass

SEVERITIES = ("info", "ok", "warn", "bad")
SUBJECT_TYPES = ("printer", "spool", "device", "location", "hub")
ACTOR_TYPES = ("hub", "device", "page", "cli", "connector")
RAISES, CLEARS = "raises", "clears"


@dataclass(frozen=True)
class Kind:
    name: str
    severities: tuple[str, ...]          # the allowed ones; the first is the default
    values: tuple[str, ...] = ()         # the names a `data` value may have (all optional)
    condition: str | None = None         # RAISES, CLEARS or None
    base: str | None = None              # the condition key's base
    keyed: bool = True                   # the condition key ends in ":<subject id>"
    emitted: bool = True                 # False = "Later": the name is reserved, nothing may write it yet
    key_from_data: bool = False          # a clearing kind that names the condition it clears in its value `key`
    choices: tuple[tuple[str, tuple[str, ...]], ...] = ()    # (value name, the only values it may have)

    @property
    def severity(self) -> str:
        return self.severities[0]


# What a person or the Hub changes about a spool: the spool, and the before and after of what changed.
_SPOOL_CHANGE = ("spool", "before_location", "after_location", "before_grams", "after_grams",
                 "before_slot", "after_slot", "before_tag", "after_tag", "before_archived",
                 "batch",      # batch: all spool events of one request (R80 E5)
                 # what opening a spool by linking its tag changed, so an undo can put it back
                 "before_first_used", "before_comment", "took_tag_from")
# Every print.* event of one print run and its usage.recorded carry `run`; Klipper ones also `printer_job` (R80 E5).
_RUN = ("run", "printer_job")


def _kinds() -> dict[str, Kind]:
    out: dict[str, Kind] = {}

    def add(name, severities, values=(), condition=None, base=None, keyed=True, emitted=True, key_from_data=False,
            choices=()):
        out[name] = Kind(name, (severities,) if isinstance(severities, str) else tuple(severities), tuple(values),
                         condition, base, keyed, emitted, key_from_data, tuple(choices))

    add("print.started", "info", ("job", "printer") + _RUN)
    add("print.paused", "warn", ("job", "percent", "reason", "stage") + _RUN, RAISES, "print-paused")
    add("print.runout", "warn", ("job", "percent", "slot") + _RUN, RAISES, "print-paused")
    add("print.resumed", "ok", ("job",) + _RUN, CLEARS, "print-paused")
    add("print.finished", "ok", ("job", "minutes") + _RUN, CLEARS, "print-paused")
    add("print.failed", "bad", ("job", "percent", "reason", "error") + _RUN, CLEARS, "print-paused")
    add("printer.error", "bad", ("code", "text"))
    add("printer.offline", "warn", (), RAISES, "printer-offline")
    add("printer.online", "ok", (), CLEARS, "printer-offline")
    add("usage.recorded", "info", ("printer", "job", "spool", "material", "grams") + _RUN)
    add("spool.low", "warn", ("spool", "filament", "material", "grams", "in_stock"), RAISES, "spool-low")
    add("spool.ok", "ok", ("spool",), CLEARS, "spool-low")
    for name in ("added", "opened", "weighed", "moved", "emptied", "loaded", "unloaded"):
        add(f"spool.{name}", "info", _SPOOL_CHANGE + (("auto",) if name == "loaded" else ()))
    add("storage.low", ("warn", "bad"), ("free", "total", "percent", "direction"), RAISES, "storage-low", keyed=False)
    add("storage.ok", "ok", ("free", "total"), CLEARS, "storage-low", keyed=False)
    # a later raise replaces the stored condition (R80 E2); `direction` = down when the disk fell from bad to warn
    add("condition.cleared", "info", ("key", "reason"), CLEARS, None, keyed=False, key_from_data=True,
        choices=(("reason", ("reconciled", "subject_removed", "feature_off")),))
    add("location.renamed", "info", ("from", "to", "spools"))
    add("device.down", "warn", (), RAISES, "device-down")
    add("device.up", "ok", (), CLEARS, "device-down")
    add("check.failed", "warn", ("text",), RAISES, "check")
    add("check.ok", "ok", ("text",), CLEARS, "check")
    add("slot.cleared", "info", _SPOOL_CHANGE + ("slot",))
    add("hub.started", "info", ("version",))
    add("hub.updated", "info", ("from", "to", "rollback"))
    add("hub.restored", "info", ("backup", "made_at"))
    add("backup.finished", "ok", ("file", "bytes", "encrypted", "nightly"))
    add("backup.failed", "warn", ("reason", "nightly"))
    add("device.paired", "info", ("kind",))
    add("device.removed", "info", ("kind",))
    add("settings.changed", "info", ("group", "names", "feature", "on"))
    add("printer.added", "info", ("family", "names"))
    add("printer.changed", "info", ("family", "names"))
    add("printer.removed", "info", ("family", "names"))
    add("catalog.changed", "info", ("what", "entry", "version"),
        choices=(("what", ("entry_added", "entry_removed", "updated", "rolled_back")),))
    add("usage.undone", "info", ("printer", "job", "spool", "grams", "entry"))
    add("record.changed", "info", ("record", "what"), choices=(("what", ("revised", "undone")),))
    return out


KINDS: dict[str, Kind] = _kinds()

ACTIVITY_KINDS: frozenset[str] = frozenset({
    "spool.added", "spool.opened", "spool.weighed", "spool.moved", "spool.emptied", "spool.loaded", "spool.unloaded",
    "slot.cleared",
    "usage.recorded", "usage.undone",
    "location.renamed",
    "hub.started", "hub.updated", "hub.restored",
    "backup.finished", "backup.failed",
    "device.paired", "device.removed",
    "settings.changed",
    "printer.added", "printer.changed", "printer.removed",
    "catalog.changed",
    "record.changed",
})


def get(name: str) -> Kind | None:
    return KINDS.get(name)


def condition_key(kind: Kind, subject_id: str = "", key: str | None = None) -> str | None:
    """The key of the condition a kind raises or clears, None for a moment. `key` (the check row's key) stands in
    for the subject id."""
    if kind.condition is None:
        return None
    if not kind.keyed:
        return kind.base
    return f"{kind.base}:{key if key is not None else subject_id}"
