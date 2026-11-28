"""Tkinter configuration GUI and controller for fksync slicer paths."""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from fksync import paths_config, store, studio, sync

_KIND_LABELS = {
    "bambu": "Bambu Studio",
    "orca": "Orca Slicer",
    "orca_install": "Orca Install",
}
_LABEL_TO_KIND = {v: k for k, v in _KIND_LABELS.items()}
_BASE_TITLE = "Spoolwarden Slicer Sync — Configuration"


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


def _looks_like_slicer_dir(kind: str, folder: Path) -> bool:
    if kind == "orca_install":
        return (folder / "orca-slicer.exe").is_file() or (folder / "resources").is_dir()
    return (folder / "user").is_dir() or any(folder.glob("*.json"))


def _open_log_file(log_path: Path) -> bool:
    if not log_path.exists():
        return False
    if sys.platform == "win32":
        os.startfile(str(log_path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(log_path)])
    else:
        subprocess.Popen(["xdg-open", str(log_path)])
    return True


def run_gui(controller: ConfigController) -> int:
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

    import tkinter as tk
    from tkinter import filedialog, messagebox, simpledialog, ttk

    root = tk.Tk()
    root.title(_BASE_TITLE)
    root.geometry("800x540")
    root.minsize(640, 420)

    style = ttk.Style(root)
    bg_normal = style.lookup("TFrame", "background") or "SystemButtonFace"
    fg_normal = style.lookup("TLabel", "foreground") or "SystemWindowText"
    bg_sel = style.lookup("Treeview", "background", ("selected",)) or "SystemHighlight"
    fg_sel = style.lookup("Treeview", "foreground", ("selected",)) or "SystemHighlightText"

    selected_idx: list[int | None] = [0 if controller.entries else None]
    row_vars: list[tk.BooleanVar] = []
    row_frames: list[tk.Frame] = []
    dirty: list[bool] = [False]
    busy: list[bool] = [False]

    def set_dirty(val: bool) -> None:
        dirty[0] = val
        root.title(f"*{_BASE_TITLE}" if val else _BASE_TITLE)

    main_frame = ttk.Frame(root, padding=12)
    main_frame.pack(fill="both", expand=True)

    # --- Status banner (UI-33) ---
    banner = ttk.LabelFrame(main_frame, text="Sync Status", padding=8)
    banner.pack(fill="x", pady=(0, 8))

    hub_var = tk.StringVar(value="Hub: checking...")
    sync_var = tk.StringVar(value="Last sync: checking...")
    task_var = tk.StringVar(value="Schedule: checking...")
    auth_var = tk.StringVar(value="Account: checking...")

    banner_info = ttk.Frame(banner)
    banner_info.pack(side="left", fill="x", expand=True)
    ttk.Label(banner_info, textvariable=hub_var).pack(anchor="w")
    ttk.Label(banner_info, textvariable=sync_var).pack(anchor="w")
    ttk.Label(banner_info, textvariable=task_var).pack(anchor="w")
    ttk.Label(banner_info, textvariable=auth_var).pack(anchor="w")

    banner_btns = ttk.Frame(banner)
    banner_btns.pack(side="right", anchor="e")
    sync_btn = ttk.Button(banner_btns, text="Sync Now")
    sync_btn.pack(side="left", padx=(0, 6))
    pair_btn = ttk.Button(banner_btns, text="Pair...")
    pair_btn.pack(side="left", padx=(0, 6))
    auth_btn = ttk.Button(banner_btns, text="Sign In...")
    auth_btn.pack(side="left", padx=(0, 6))
    log_btn = ttk.Button(banner_btns, text="Open Log")
    log_btn.pack(side="left")

    header = ttk.Label(
        main_frame,
        text="Select the slicer folders to sync and configure custom paths:",
    )
    header.pack(anchor="w", pady=(0, 6))

    list_container = ttk.Frame(main_frame)
    list_container.pack(fill="both", expand=True)

    canvas = tk.Canvas(list_container, highlightthickness=1, background=bg_normal)
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

    def _on_mousewheel(event) -> None:
        if getattr(event, "num", None) == 4:
            canvas.yview_scroll(-1, "units")
        elif getattr(event, "num", None) == 5:
            canvas.yview_scroll(1, "units")
        elif getattr(event, "delta", 0):
            canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    canvas.bind("<MouseWheel>", _on_mousewheel)
    canvas.bind("<Button-4>", _on_mousewheel)
    canvas.bind("<Button-5>", _on_mousewheel)

    status_var = tk.StringVar(value="")

    def refresh_status_banner(check_task: bool = False) -> None:
        try:
            cfg = store.load_config(controller.dirs)
        except ValueError:
            cfg = {}
        tok = store.load_token(controller.dirs)
        user_sess = store.load_user_session(controller.dirs)
        if tok and cfg.get("host"):
            port = cfg.get("port", "")
            dev = cfg.get("device") or ""
            hub_var.set(f"Hub: Paired to {cfg['host']}:{port}" + (f" ({dev})" if dev else ""))
            pair_btn.configure(text="Unpair")
        else:
            hub_var.set("Hub: Not paired")
            pair_btn.configure(text="Pair...")
        if user_sess:
            uname = user_sess.get("username") or "user"
            urole = user_sess.get("role") or "user"
            auth_var.set(f"Account: Signed in as {uname} ({urole})")
            auth_btn.configure(text="Sign Out")
        else:
            auth_var.set("Account: Not signed in (device token)" if tok else "Account: Not signed in")
            auth_btn.configure(text="Sign In...")

        st = sync.load_state(controller.dirs)
        w = st.get("waiting")
        failed = st.get("last_error") if isinstance(st.get("last_error"), dict) else None
        last = st.get("last") or {}
        if isinstance(w, dict) and w.get("since"):
            app_name = w.get("app") or "slicer"
            sync_var.set(f"Last sync: Waiting for {app_name} to close")
        elif failed:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(failed.get("at") or 0)))
            sync_var.set(f"Last sync: Error at {when} — {failed.get('detail', '')}")
        elif last:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(last.get("at") or 0)))
            sync_var.set(f"Last sync: {last.get('outcome', 'done')} at {when}")
        else:
            sync_var.set("Last sync: None yet")

        if check_task:
            def _probe_task() -> None:
                from fksync import __main__ as cli_main
                reg = cli_main._task_registered()
                if reg is True:
                    msg = "Schedule: Active (runs every 10 minutes)"
                elif reg is False:
                    msg = "Schedule: Not registered"
                else:
                    msg = "Schedule: N/A"
                root.after(0, lambda: task_var.set(msg))
            threading.Thread(target=_probe_task, daemon=True).start()

    def set_busy(val: bool, msg: str = "") -> None:
        busy[0] = val
        state = "disabled" if val else "normal"
        sync_btn.configure(state=state)
        pair_btn.configure(state=state)
        auth_btn.configure(state=state)
        if msg:
            status_var.set(msg)

    def on_sync_now() -> None:
        if busy[0]:
            return
        set_busy(True, "Syncing with Spoolwarden...")

        def _work() -> None:
            from fksync import __main__ as cli_main
            rc = cli_main.cmd_run(controller.dirs, controller.appdata, quiet=True, layout=controller.layout)
            def _done() -> None:
                set_busy(False, "Sync complete." if rc == 0 else f"Sync finished with code {rc}.")
                refresh_status_banner(check_task=False)
            root.after(0, _done)

        threading.Thread(target=_work, daemon=True).start()

    def on_pair_or_unpair() -> None:
        if busy[0]:
            return
        tok = store.load_token(controller.dirs)
        if tok:
            if not messagebox.askyesno("Unpair", "Unpair this PC from Spoolwarden? (Your slicer folders will be kept.)", parent=root):
                return
            from fksync import __main__ as cli_main
            rc = cli_main.cmd_unpair(controller.dirs, yes=True)
            refresh_status_banner(check_task=False)
            status_var.set("Unpaired from Spoolwarden." if rc == 0 else "Could not unpair (sync may be running).")
            return

        link_text = simpledialog.askstring(
            "Pair with Spoolwarden",
            "Paste the fkit: pairing link from 'sudo spoolwarden pair --pc':",
            parent=root,
        )
        if not link_text or not link_text.strip():
            return
        raw = link_text.strip()
        set_busy(True, "Pairing with Spoolwarden...")

        def _work() -> None:
            from fksync import __main__ as cli_main, client
            err_msg = ""
            rc = 1
            try:
                if raw.lower().startswith("fkit:"):
                    link = client.parse_link(raw)
                    cfg = {"host": link.host, "port": link.port, "fp": link.fp, "ips": list(link.ips)}
                    controller.dirs.ensure()
                    with store.single_instance(controller.dirs):
                        store.recover_interrupted_pair(controller.dirs)
                        rc = cli_main._finish_pair(controller.dirs, cfg, link.token, controller.appdata, controller.layout)
                else:
                    err_msg = "Please paste an fkit: pairing link."
            except Exception as exc:
                err_msg = str(exc)

            def _done() -> None:
                set_busy(False, "Paired with Spoolwarden." if rc == 0 else (err_msg or "Pairing failed."))
                refresh_status_banner(check_task=False)
            root.after(0, _done)

        threading.Thread(target=_work, daemon=True).start()

    def on_open_log() -> None:
        if not _open_log_file(controller.dirs.log_file):
            messagebox.showinfo("Open Log", f"No log file yet at:\n{controller.dirs.log_file}", parent=root)

    def on_sign_in_or_out() -> None:
        if busy[0]:
            return
        sess = store.load_user_session(controller.dirs)
        if sess:
            if messagebox.askyesno("Sign Out", f"Sign out {sess.get('username') or 'current user'}?", parent=root):
                set_busy(True, "Signing out...")
                def _work_logout() -> None:
                    tok = sess.get("token")
                    if tok:
                        try:
                            cfg = store.load_config(controller.dirs)
                        except ValueError:
                            cfg = {}
                        if cfg.get("host") and cfg.get("fp"):
                            from fksync import client
                            try:
                                client.Client(cfg, tok, timeout=5).logout()
                            except Exception:  # noqa: BLE001 - best-effort remote logout
                                pass
                    store.clear_user_session(controller.dirs)
                    def _done_logout() -> None:
                        set_busy(False, "Signed out.")
                        refresh_status_banner(check_task=False)
                    root.after(0, _done_logout)
                threading.Thread(target=_work_logout, daemon=True).start()
            return
        try:
            cfg = store.load_config(controller.dirs)
        except ValueError:
            cfg = {}
        if not cfg.get("host") or not cfg.get("fp"):
            messagebox.showinfo("Sign In", "Pair this PC with a Hub first before signing in.", parent=root)
            return

        dlg = tk.Toplevel(root)
        dlg.title("Sign In to Spoolwarden")
        dlg.transient(root)
        dlg.grab_set()
        frm = ttk.Frame(dlg, padding=14)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="Username:").grid(row=0, column=0, sticky="w", pady=(0, 6))
        user_var = tk.StringVar()
        ttk.Entry(frm, textvariable=user_var, width=28).grid(row=0, column=1, sticky="ew", pady=(0, 6))
        ttk.Label(frm, text="Password:").grid(row=1, column=0, sticky="w", pady=(0, 8))
        pass_var = tk.StringVar()
        ttk.Entry(frm, textvariable=pass_var, show="*", width=28).grid(row=1, column=1, sticky="ew", pady=(0, 8))
        err_var = tk.StringVar(value="")
        ttk.Label(frm, textvariable=err_var, foreground="red").grid(row=2, column=0, columnspan=2, sticky="w")

        def _finish_auth(res: dict) -> None:
            u = res.get("user") or {}
            uname = u.get("username") if isinstance(u, dict) else str(u or user_var.get().strip())
            store.save_user_session(controller.dirs, {
                "token": res["token"],
                "username": uname,
                "role": res.get("role"),
                "permissions": res.get("permissions") or [],
            })
            dlg.destroy()
            refresh_status_banner(check_task=False)
            status_var.set(f"Signed in as {uname}.")

        def _do_pw() -> None:
            uname, pw = user_var.get().strip(), pass_var.get()
            if not uname or not pw:
                err_var.set("Enter username and password.")
                return
            err_var.set("Signing in...")
            def _work() -> None:
                from fksync import client
                try:
                    res = client.Client(cfg, None).login_password(uname, pw)
                    root.after(0, lambda: _finish_auth(res))
                except Exception as exc:  # noqa: BLE001
                    root.after(0, lambda msg=str(exc): err_var.set(msg))
            threading.Thread(target=_work, daemon=True).start()

        def _do_sso() -> None:
            err_var.set("Opening browser for SSO...")
            def _work() -> None:
                from fksync import __main__ as cli_main, client
                try:
                    res = cli_main._loopback_sso_login(cfg, client.Client(cfg, None))
                    root.after(0, lambda: _finish_auth(res))
                except Exception as exc:  # noqa: BLE001
                    root.after(0, lambda msg=str(exc): err_var.set(msg))
            threading.Thread(target=_work, daemon=True).start()

        btns = ttk.Frame(frm)
        btns.grid(row=3, column=0, columnspan=2, sticky="e", pady=(8, 0))
        ttk.Button(btns, text="Sign In", command=_do_pw).pack(side="left", padx=(0, 6))
        ttk.Button(btns, text="Sign in with SSO", command=_do_sso).pack(side="left", padx=(0, 6))
        ttk.Button(btns, text="Cancel", command=dlg.destroy).pack(side="left")

    sync_btn.configure(command=on_sync_now)
    pair_btn.configure(command=on_pair_or_unpair)
    auth_btn.configure(command=on_sign_in_or_out)
    log_btn.configure(command=on_open_log)

    def update_selection_highlight() -> None:
        sel = selected_idx[0]
        for idx, rf in enumerate(row_frames):
            is_sel = (idx == sel)
            bg = bg_sel if is_sel else bg_normal
            fg = fg_sel if is_sel else fg_normal
            rf.configure(bg=bg)
            for child in rf.winfo_children():
                if isinstance(child, tk.Label):
                    child.configure(bg=bg, fg=fg)

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
            rf = tk.Frame(rows_frame, bg=bg_normal, padx=6, pady=4)
            rf.pack(fill="x", expand=True, pady=1)
            row_frames.append(rf)

            var = tk.BooleanVar(value=entry.enabled)
            row_vars.append(var)

            def _on_toggle(i=idx, v=var) -> None:
                select_row(i)
                controller.set_enabled(i, v.get())
                set_dirty(True)

            cb = ttk.Checkbutton(rf, variable=var, command=_on_toggle)
            cb.pack(side="left", padx=(0, 6))

            kind_lbl = tk.Label(
                rf,
                text=_KIND_LABELS.get(entry.kind, entry.kind),
                width=14,
                anchor="w",
                bg=bg_normal,
                fg=fg_normal,
                font=("TkDefaultFont", 9, "bold"),
            )
            kind_lbl.pack(side="left", padx=(0, 8))

            name_lbl = tk.Label(rf, text=entry.label, width=20, anchor="w", bg=bg_normal, fg=fg_normal)
            name_lbl.pack(side="left", padx=(0, 8))

            path_lbl = tk.Label(rf, text=entry.path, anchor="w", bg=bg_normal, fg=fg_normal)
            path_lbl.pack(side="left", fill="x", expand=True)

            for widget in (rf, kind_lbl, name_lbl, path_lbl):
                widget.bind("<Button-1>", lambda _e, i=idx: select_row(i))
                widget.bind("<Double-Button-1>", lambda _e, i=idx: (select_row(i), on_modify()))
                widget.bind("<MouseWheel>", _on_mousewheel)
                widget.bind("<Button-4>", _on_mousewheel)
                widget.bind("<Button-5>", _on_mousewheel)

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
        kind_var = tk.StringVar(value=_KIND_LABELS.get(initial_kind, _KIND_LABELS["bambu"]))
        kind_combo = ttk.Combobox(
            body,
            textvariable=kind_var,
            values=[_KIND_LABELS[k] for k in paths_config.VALID_KINDS],
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
        path_entry = ttk.Entry(body, textvariable=path_var, width=42)
        path_entry.grid(row=2, column=1, sticky="we", pady=4, padx=(8, 4))

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

        def on_ok(_event=None) -> None:
            raw_path = path_var.get().strip()
            if not raw_path:
                messagebox.showerror("Invalid Path", "Please enter or browse to a folder path.", parent=dlg)
                return
            folder = Path(raw_path)
            if not folder.is_dir():
                messagebox.showerror("Invalid Path", f"Folder does not exist:\n{raw_path}", parent=dlg)
                return
            kind_key = _LABEL_TO_KIND.get(kind_var.get(), "bambu")
            if not _looks_like_slicer_dir(kind_key, folder):
                if not messagebox.askyesno(
                    "Confirm Folder",
                    f"'{raw_path}' does not look like a {_KIND_LABELS[kind_key]} folder.\nUse it anyway?",
                    parent=dlg,
                ):
                    return
            result[0] = (kind_key, label_var.get(), raw_path)
            dlg.destroy()

        ttk.Button(btn_row, text="OK", command=on_ok).pack(side="left", padx=(0, 6))
        ttk.Button(btn_row, text="Cancel", command=dlg.destroy).pack(side="left")

        dlg.bind("<Return>", on_ok)
        dlg.bind("<Escape>", lambda _e: dlg.destroy())
        path_entry.focus_set()

        dlg.wait_window()
        return result[0]

    def on_autodetect() -> None:
        added = controller.autodetect()
        if added:
            set_dirty(True)
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
        set_dirty(True)
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
        set_dirty(True)
        refresh_list()
        status_var.set("Added custom path.")

    def on_remove() -> None:
        idx = selected_idx[0]
        if idx is None or not (0 <= idx < len(controller.entries)):
            messagebox.showinfo("Remove Path", "Select a path entry first.", parent=root)
            return
        controller.remove(idx)
        set_dirty(True)
        refresh_list()
        status_var.set("Removed path entry.")

    def on_save() -> bool:
        try:
            controller.save()
        except store.AlreadyRunning as e:
            messagebox.showerror(
                "Save",
                f"Cannot save while another sync is running ({e}). Try again in a moment.",
                parent=root,
            )
            status_var.set("Save skipped: sync is running.")
            return False
        set_dirty(False)
        status_var.set(f"Saved {len(controller.entries)} path(s).")
        return True

    def on_close() -> None:
        if dirty[0]:
            ans = messagebox.askyesnocancel(
                "Unsaved Changes",
                "You have unsaved slicer path changes. Save before closing?",
                parent=root,
            )
            if ans is None:
                return
            if ans and not on_save():
                return
        root.destroy()

    def _on_key(event) -> None:
        if isinstance(root.focus_get(), (ttk.Entry, ttk.Combobox)):
            return
        if not controller.entries:
            return
        cur = selected_idx[0] if selected_idx[0] is not None else 0
        if event.keysym == "Up":
            select_row(max(0, cur - 1))
        elif event.keysym == "Down":
            select_row(min(len(controller.entries) - 1, cur + 1))
        elif event.keysym == "Delete":
            on_remove()

    root.bind("<Up>", _on_key)
    root.bind("<Down>", _on_key)
    root.bind("<Delete>", _on_key)
    root.protocol("WM_DELETE_WINDOW", on_close)

    controls = ttk.Frame(main_frame)
    controls.pack(fill="x", pady=(10, 4))

    ttk.Button(controls, text="Auto-detect paths", command=on_autodetect).pack(side="left", padx=(0, 6))
    ttk.Button(controls, text="Modify...", command=on_modify).pack(side="left", padx=(0, 6))
    ttk.Button(controls, text="Add...", command=on_add).pack(side="left", padx=(0, 6))
    ttk.Button(controls, text="Remove", command=on_remove).pack(side="left", padx=(0, 6))

    ttk.Button(controls, text="Close", command=on_close).pack(side="right")
    ttk.Button(controls, text="Save", command=on_save).pack(side="right", padx=(0, 6))

    ttk.Label(main_frame, textvariable=status_var).pack(anchor="w", pady=(4, 0))

    refresh_list()
    refresh_status_banner(check_task=True)
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
    parser.add_argument("--list-json", action="store_true", help="Print saved slicer paths as JSON")
    args = parser.parse_args(argv)

    appdata, dirs, layout = _resolve_env(args.appdata, args.localappdata)

    if args.apply_paths:
        raw = json.loads(Path(args.apply_paths).read_text(encoding="utf-8-sig"))
        entries = [paths_config.SlicerEntry.from_dict(item) for item in raw]
        try:
            paths_config.save_slicer_paths(dirs, entries)
        except store.AlreadyRunning as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        return 0

    if args.detect_json:
        pf = paths_config.default_program_files() if layout is None else None
        entries = paths_config.detect_slicer_paths(appdata, layout=layout, program_files=pf)
        print(json.dumps([e.to_dict() for e in entries]))
        return 0

    if args.list_json:
        entries = paths_config.load_slicer_paths(dirs)
        print(json.dumps([e.to_dict() for e in entries]))
        return 0

    controller = ConfigController(dirs, appdata, layout=layout)
    controller.load()
    return run_gui(controller)


if __name__ == "__main__":
    sys.exit(main())
