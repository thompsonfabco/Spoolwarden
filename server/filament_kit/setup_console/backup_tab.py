"""Backups on the Settings tab: list, download as a zip, back up now, keep-days, and restore from an uploaded zip.
A backup holds every secret of the kit, so each of these takes the setup code; a trusted computer's cookie stands in for
it, as for every other change (the user's choice, 2026-11-09).

    POST backups/list {code}                     the backups in /var/backups/filament-kit, newest first (the helper: the
                                                 folder is root only, since a backup holds the setup code and TLS key)
    POST backups/download {code, name}           one of them, as the standard zip (a nightly tar.gz is turned into one):
                                                 the helper hands it over as an open file, nothing is written for it
    POST backups/keep {code, days}               how many days the nightly backup keeps (the helper's op backup_keep)
    POST restore/upload  (the zip as the body; X-Setup-Code)   written to the state folder, then pinned by the
                                                 helper (restore_pin): copied into the root-only folder, hashed and
                                                 checked; its summary and sha256
    POST restore/forget {code}                   drop an uploaded zip
Back up now is the job `backup`, the restore the job `restore` with {confirm: "restore", sha256}: the helper restores
only the pinned copy, and only while it has the hash the owner was shown.
"""
from __future__ import annotations

import os
import shutil

import anyio
from starlette.responses import StreamingResponse

from .. import backup, backup_crypt, config, demo
from .texts import say

MAX_UPLOAD = 2 * 2**30


def routes(page) -> list[tuple]:
    from ..setup_page import Refused

    w = lambda fn: page.guard(fn, write=True)  # noqa: E731

    def not_in_demo() -> None:
        """The demo restores no backup: an upload is not written, a pending one is not dropped, before the
        helper (which refuses as well) is asked."""
        if demo.is_on():
            raise Refused(409, demo.NOT_IN_DEMO, code=demo.NOT_IN_DEMO_CODE)

    async def listing(request, body):
        r = await page._helper({"op": "backup_list", "code": body["code"]})
        return {"backups": r["backups"], "keep_days": r["keep_days"], "folder": r["folder"],
                "password_set": bool(r.get("password_set", False))}

    async def download(request, body):
        not_in_demo()
        name = body.get("name")
        if not isinstance(name, str) or not name:
            raise Refused(404, "no such backup")
        r, fh = await page._helper_file({"op": "backup_open", "code": body["code"], "name": name})
        page.audit(request, f"backup {name} downloaded")
        headers = {"Content-Disposition": f'attachment; filename="{r["download"]}"', "Content-Length": str(r["size"])}

        def chunks():
            try:
                while data := fh.read(2**20):
                    yield data
            finally:
                fh.close()
        return StreamingResponse(chunks(), media_type="application/zip", headers=headers)

    async def keep(request, body):
        r = await page._helper({"op": "backup_keep", "code": body["code"], "days": body.get("days")})
        page.audit(request, f"backups kept for {r['days']} days")
        return {"ok": True, "days": r["days"]}

    async def password_set(request, body):
        pw = body.get("password")
        if not isinstance(pw, str) or len(pw) < backup.MIN_PASSWORD_LEN:
            raise Refused(400, say("hub_backup_password_short", count=backup.MIN_PASSWORD_LEN),
                          code="too_short", field="password")
        try:
            await page._helper({"op": "backup_password_set", "code": body["code"], "password": pw})
        except Refused as e:
            if e.status == 400 and "code" not in e.extra:
                raise Refused(400, e.message, code="too_short", **e.extra) from None
            raise
        page.audit(request, "backup password set")
        return {"set": True}

    async def password_remove(request, body):
        await page._helper({"op": "backup_password_remove", "code": body["code"]})
        page.audit(request, "backup password removed")
        return {"set": False}

    async def upload_zip(request, body):
        not_in_demo()
        dest = backup.uploaded()
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > MAX_UPLOAD:
            raise Refused(413, "the backup is larger than the page takes; copy it to the Hub and run: "
                               "sudo filament-kit restore <file>")
        await anyio.to_thread.run_sync(lambda: shutil.rmtree(dest.parent, ignore_errors=True))
        await anyio.to_thread.run_sync(lambda: dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True))
        part = dest.with_name("backup.zip.part")
        n = 0
        fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
        try:
            with os.fdopen(fd, "wb") as fh:
                async for chunk in request.stream():
                    n += len(chunk)
                    if n > MAX_UPLOAD:
                        raise Refused(413, "the backup is larger than the page takes; copy it to the Hub and run: "
                                           "sudo filament-kit restore <file>")
                    await anyio.to_thread.run_sync(fh.write, chunk)
            os.replace(part, dest)
            r = await page._helper({"op": "restore_pin", "code": body["code"]})
            if r.get("needs_password"):
                await anyio.to_thread.run_sync(lambda: shutil.rmtree(dest.parent, ignore_errors=True))
                page.audit(request, "encrypted backup uploaded for a restore")
                return {"ok": True, "needs_password": True, "sha256": r["sha256"]}
            summary = {k: r[k] for k in ("kit", "host", "created", "modules", "files", "spoolman", "size", "sha256",
                                         "other_host", "replaces_data") if k in r}
            # pinned by root: the server's own copy is of no further use, and root does not touch this folder
            await anyio.to_thread.run_sync(lambda: shutil.rmtree(dest.parent, ignore_errors=True))
        except BaseException:
            await anyio.to_thread.run_sync(lambda: shutil.rmtree(dest.parent, ignore_errors=True))
            raise
        page.audit(request, "backup uploaded for a restore")
        return {"ok": True, **summary}

    async def restore_password(request, body):
        pw = body.get("password")
        if not isinstance(pw, str) or not pw:
            raise Refused(400, backup_crypt.WRONG_MESSAGE, code="wrong_password", field="password")
        try:
            r = await page._helper({"op": "restore_password", "code": body["code"], "password": pw})
        except Refused as e:
            if e.status == 400 and "code" not in e.extra:
                raise Refused(400, e.message, code="wrong_password", **e.extra) from None
            raise
        summary = {k: r[k] for k in ("kit", "host", "created", "modules", "files", "spoolman", "size", "sha256",
                                     "other_host", "replaces_data") if k in r}
        page.audit(request, "encrypted backup opened for a restore")
        return {"ok": True, **summary}

    async def forget(request, body):
        not_in_demo()
        await anyio.to_thread.run_sync(lambda: shutil.rmtree(backup.uploaded().parent, ignore_errors=True))
        await page._helper({"op": "restore_forget", "code": body["code"]})
        return {"ok": True}

    return [("/backups/list", page.guard(listing, write="helper"), "POST"),
            ("/backups/download", page.guard(download, write="helper"), "POST"),
            ("/backups/keep", page.guard(keep, write="helper"), "POST"),
            ("/backups/password", page.guard(password_set, write="helper"), "POST"),
            ("/backups/password/remove", page.guard(password_remove, write="helper"), "POST"),
            ("/restore/upload", page.guard(upload_zip, write=True, raw=True), "POST"),
            ("/restore/password", page.guard(restore_password, write="helper"), "POST"),
            ("/restore/forget", w(forget), "POST")]
