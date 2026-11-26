"""Tkinter configuration GUI and controller for fksync slicer paths."""

import argparse
import json
import os
import sys
from pathlib import Path

from . import paths_config, store, studio

_KIND_LABELS = {
    "bambu": "Bambu Studio",
    "orca": "Orca Slicer",
    "orca_install": "Orca Install",
}


class ConfigController:
    """Headless-testable controller for loading, editing, and saving slicer path entries."""

    def __init__(
        self,
        dirs: store.AppDirs,
        appdata: Path | None = None,
        *,
        layout=None,
        program_files=None,
        registry=None,
    ):
        self.dirs = dirs
        self.appdata = appdata
        self.layout = layout
        self.program_files = program_files
        self.registry = registry
        self.entries: list[paths_config.SlicerEntry] = []

    def load(self) -> list[paths_config.SlicerEntry]:
        loaded = paths_config.load_slicer_paths(self.dirs)
        if loaded:
            self.entries = loaded
        else:
            self.entries = paths_config.detect_slicer_paths(
                self.appdata,
                layout=self.layout,
                program_files=self.program_files,
                registry=self.registry,
            )
        return self.entries

    def autodetect(self) -> int:
        detected = paths_config.detect_slicer_paths(
            self.appdata,
            layout=self.layout,
            program_files=self.program_files,
            registry=self.registry,
        )
        merged = paths_config.merge_detected(self.entries, detected)
        added = len(merged) - len(self.entries)
        self.entries = merged
        return added

    def set_enabled(self, index: int, enabled: bool) -> None:
        self.entries[index].enabled = bool(enabled)

    def toggle(self, index: int) -> bool:
        self.entries[index].enabled = not self.entries[index].enabled
        return self.entries[index].enabled

    def modify(
        self,
        index: int,
        path: str,
        *,
        kind: str | None = None,
        label: str | None = None,
    ) -> None:
        entry = self.entries[index]
        if kind is not None:
            if kind not in paths_config.VALID_KINDS:
                raise ValueError(f"invalid slicer kind {kind!r}; expected one of {', '.join(paths_config.VALID_KINDS)}")
            entry.kind = kind
        clean_path = str(path).strip()
        if not clean_path:
            raise ValueError("slicer entry path must not be empty")
        entry.path = clean_path
        if label is not None:
            clean_label = str(label).strip()
            entry.label = clean_label or Path(clean_path).name or _KIND_LABELS[entry.kind]
        entry.custom = True

    def add(
        self,
        kind: str,
        path: str,
        *,
        label: str | None = None,
        enabled: bool = True,
    ) -> paths_config.SlicerEntry:
        if kind not in paths_config.VALID_KINDS:
            raise ValueError(f"invalid slicer kind {kind!r}; expected one of {', '.join(paths_config.VALID_KINDS)}")
        clean_path = str(path).strip()
        if not clean_path:
            raise ValueError("slicer entry path must not be empty")
        clean_label = str(label).strip() if label is not None else ""
        if not clean_label:
            clean_label = Path(clean_path).name or _KIND_LABELS[kind]
        entry = paths_config.SlicerEntry(
            kind=kind,
            label=clean_label,
            path=clean_path,
            enabled=bool(enabled),
            custom=True,
        )
        self.entries.append(entry)
        return entry

    def remove(self, index: int) -> paths_config.SlicerEntry:
        return self.entries.pop(index)

    def save(self) -> dict:
        return paths_config.save_slicer_paths(self.dirs, self.entries)


def run_gui(controller: ConfigController) -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title("Spoolwarden Slicer Sync — Configuration")
    root.geometry("760x440")
    root.minsize(600, 340)

    selected_idx: list[int | None] = [0 if controller.entries else None]
    row_vars: list[tk.BooleanVar] = []
    row_frames: list[tk.Frame] = []

    main_frame = ttk.Frame(root, padding=12)
    main_frame.pack(fill="both", expand=True)

    header = ttk.Label(
        main_frame,
        text="Select the slicer folders to sync and configure custom paths:",
    )
    header.pack(anchor="w", pady=(0, 8))

    list_container = ttk.Frame(main_frame)
    list_container.pack(fill="both", expand=True)

    canvas = tk.Canvas(list_container, highlightthickness=1, highlightbackground="#cccccc")
    scrollbar = ttk.Scrollbar(list_container, orient="vertical", command=canvas.yview)
    rows_frame = ttk.Frame(canvas)

    rows_frame.bind(
        "<Configure>",
        lambda _e: canvas.configure(scrollregion=canvas.bbox("all")),
    )
    canvas_window = canvas.create_window((0, 0), window=rows_frame, anchor="nw")
    canvas.bind(
        "<Configure>",
        lambda e: canvas.itemconfigure(canvas_window, width=e.width),
    )
    canvas.configure(yscrollcommand=scrollbar.set)

    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    status_var = tk.StringVar(value="")

    def update_selection_highlight() -> None:
        sel = selected_idx[0]
        for idx, rf in enumerate(row_frames):
            bg = "#dbeafe" if idx == sel else "#ffffff"
            rf.configure(bg=bg)
            for child in rf.winfo_children():
                if isinstance(child, tk.Label):
                    child.configure(bg=bg)

    def select_row(idx: int) -> None:
        selected_idx[0] = idx
        update_selection_highlight()

    def refresh_list() -> None:
        for child in rows_frame.winfo_children():
            child.destroy()
        row_vars.clear()
        row_frames.clear()

        if selected_idx[0] is not None and selected_idx[0] >= len(controller.entries):
            selected_idx[0] = len(controller.entries) - 1 if controller.entries else None

        for idx, entry in enumerate(controller.entries):
            rf = tk.Frame(rows_frame, bg="#ffffff", padx=6, pady=4)
            rf.pack(fill="x", expand=True, pady=1)
            row_frames.append(rf)

            var = tk.BooleanVar(value=entry.enabled)
            row_vars.append(var)

            cb = ttk.Checkbutton(
                rf,
                variable=var,
                command=lambda i=idx, v=var: controller.set_enabled(i, v.get()),
            )
            cb.pack(side="left", padx=(0, 6))

            kind_lbl = tk.Label(
                rf,
                text=_KIND_LABELS.get(entry.kind, entry.kind),
                width=14,
                anchor="w",
                bg="#ffffff",
                font=("TkDefaultFont", 9, "bold"),
            )
            kind_lbl.pack(side="left", padx=(0, 8))

            name_lbl = tk.Label(rf, text=entry.label, width=20, anchor="w", bg="#ffffff")
            name_lbl.pack(side="left", padx=(0, 8))

            path_lbl = tk.Label(rf, text=entry.path, anchor="w", bg="#ffffff", fg="#333333")
            path_lbl.pack(side="left", fill="x", expand=True)

            for widget in (rf, kind_lbl, name_lbl, path_lbl):
                widget.bind("<Button-1>", lambda _e, i=idx: select_row(i))

        update_selection_highlight()

    def open_entry_dialog(
        title: str,
        initial_kind: str = "bambu",
        initial_label: str = "",
        initial_path: str = "",
    ) -> tuple[str, str, str] | None:
        result: list[tuple[str, str, str] | None] = [None]
        dlg = tk.Toplevel(root)
        dlg.title(title)
        dlg.transient(root)
        dlg.grab_set()
        dlg.resizable(False, False)

        body = ttk.Frame(dlg, padding=12)
        body.pack(fill="both", expand=True)

        ttk.Label(body, text="Kind:").grid(row=0, column=0, sticky="w", pady=4)
        kind_var = tk.StringVar(value=initial_kind)
        kind_combo = ttk.Combobox(
            body,
            textvariable=kind_var,
            values=list(paths_config.VALID_KINDS),
            state="readonly",
            width=24,
        )
        kind_combo.grid(row=0, column=1, columnspan=2, sticky="we", pady=4, padx=(8, 0))

        ttk.Label(body, text="Label:").grid(row=1, column=0, sticky="w", pady=4)
        label_var = tk.StringVar(value=initial_label)
        ttk.Entry(body, textvariable=label_var, width=42).grid(
            row=1, column=1, columnspan=2, sticky="we", pady=4, padx=(8, 0)
        )

        ttk.Label(body, text="Path:").grid(row=2, column=0, sticky="w", pady=4)
        path_var = tk.StringVar(value=initial_path)
        ttk.Entry(body, textvariable=path_var, width=42).grid(
            row=2, column=1, sticky="we", pady=4, padx=(8, 4)
        )

        def browse() -> None:
            chosen = filedialog.askdirectory(
                parent=dlg,
                initialdir=path_var.get() or None,
                title="Select Slicer Folder",
            )
            if chosen:
                path_var.set(chosen)

        ttk.Button(body, text="Browse...", command=browse).grid(row=2, column=2, pady=4)

        btn_row = ttk.Frame(body)
        btn_row.grid(row=3, column=0, columnspan=3, sticky="e", pady=(10, 0))

        def on_ok() -> None:
            if not path_var.get().strip():
                messagebox.showerror("Invalid Path", "Please enter or browse to a folder path.", parent=dlg)
                return
            result[0] = (kind_var.get(), label_var.get(), path_var.get())
            dlg.destroy()

        ttk.Button(btn_row, text="OK", command=on_ok).pack(side="left", padx=(0, 6))
        ttk.Button(btn_row, text="Cancel", command=dlg.destroy).pack(side="left")

        dlg.wait_window()
        return result[0]

    def on_autodetect() -> None:
        added = controller.autodetect()
        refresh_list()
        status_var.set(f"Added {added} new path(s)." if added else "No new paths found.")

    def on_modify() -> None:
        idx = selected_idx[0]
        if idx is None or not (0 <= idx < len(controller.entries)):
            messagebox.showinfo("Modify Path", "Select a path entry first.", parent=root)
            return
        entry = controller.entries[idx]
        edited = open_entry_dialog("Modify Slicer Path", entry.kind, entry.label, entry.path)
        if edited is None:
            return
        kind, label, path = edited
        try:
            controller.modify(idx, path, kind=kind, label=label)
        except ValueError as e:
            messagebox.showerror("Modify Path", str(e), parent=root)
            return
        refresh_list()
        status_var.set("Updated path entry.")

    def on_add() -> None:
        added = open_entry_dialog("Add Slicer Path")
        if added is None:
            return
        kind, label, path = added
        try:
            controller.add(kind, path, label=label)
        except ValueError as e:
            messagebox.showerror("Add Path", str(e), parent=root)
            return
        selected_idx[0] = len(controller.entries) - 1
        refresh_list()
        status_var.set("Added custom path.")

    def on_remove() -> None:
        idx = selected_idx[0]
        if idx is None or not (0 <= idx < len(controller.entries)):
            messagebox.showinfo("Remove Path", "Select a path entry first.", parent=root)
            return
        controller.remove(idx)
        refresh_list()
        status_var.set("Removed path entry.")

    def on_save() -> None:
        controller.save()
        status_var.set(f"Saved {len(controller.entries)} path(s).")

    controls = ttk.Frame(main_frame)
    controls.pack(fill="x", pady=(10, 4))

    ttk.Button(controls, text="Auto-detect paths", command=on_autodetect).pack(side="left", padx=(0, 6))
    ttk.Button(controls, text="Modify...", command=on_modify).pack(side="left", padx=(0, 6))
    ttk.Button(controls, text="Add...", command=on_add).pack(side="left", padx=(0, 6))
    ttk.Button(controls, text="Remove", command=on_remove).pack(side="left", padx=(0, 6))

    ttk.Button(controls, text="Close", command=root.destroy).pack(side="right")
    ttk.Button(controls, text="Save", command=on_save).pack(side="right", padx=(0, 6))

    ttk.Label(main_frame, textvariable=status_var).pack(anchor="w", pady=(4, 0))

    refresh_list()
    root.mainloop()
    return 0


def _resolve_env(appdata_arg: str | None, localappdata_arg: str | None):
    if sys.platform.startswith("linux") and not appdata_arg and not localappdata_arg:
        layout = studio.LinuxLayout.from_env(os.environ)
        return None, store.AppDirs(store.linux_data_dir(os.environ)), layout
    if sys.platform == "darwin" and not appdata_arg and not localappdata_arg:
        layout = studio.MacLayout.from_env(os.environ)
        return None, store.AppDirs(store.mac_data_dir(os.environ)), layout
    appdata = Path(appdata_arg or os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    local = Path(localappdata_arg or os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return appdata, store.AppDirs(local / "filament-kit-sync"), None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="filament-kit-sync-config", description="Configure Spoolwarden Slicer Sync paths.")
    parser.add_argument("--appdata", help=argparse.SUPPRESS)
    parser.add_argument("--localappdata", help=argparse.SUPPRESS)
    parser.add_argument("--apply-paths", help="Apply slicer path entries from a JSON file and save config")
    parser.add_argument("--detect-json", action="store_true", help="Detect slicer paths and print as JSON")
    args = parser.parse_args(argv)

    appdata, dirs, layout = _resolve_env(args.appdata, args.localappdata)

    if args.apply_paths:
        raw = json.loads(Path(args.apply_paths).read_text(encoding="utf-8"))
        entries = [paths_config.SlicerEntry.from_dict(item) for item in raw]
        paths_config.save_slicer_paths(dirs, entries)
        return 0

    if args.detect_json:
        pf = paths_config.default_program_files() if layout is None else None
        entries = paths_config.detect_slicer_paths(appdata, layout=layout, program_files=pf)
        print(json.dumps([e.to_dict() for e in entries]))
        return 0

    controller = ConfigController(dirs, appdata, layout=layout)
    controller.load()
    return run_gui(controller)


if __name__ == "__main__":
    sys.exit(main())
