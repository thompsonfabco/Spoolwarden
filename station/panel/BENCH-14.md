# Panel firmware 14 (Wi-Fi): what is NOT verified until it runs on a C3

Firmware 14 was built and unit-tested without a board: the line parser, the set-up lines, the link state machine
(timings, back-off, K, refusals, the pin-before-token rule, USB winning) run against a fake network on the host
(`pio test -e native`), and the C3 target compiles and links. **Nothing below has run on hardware.** Each item says what
to do, what to see, and what passes. An item not done is SKIPPED, never passed. Thresholds marked (est) are proposals
from the Hardware session's proposal (section 6), not measured limits.

Needs: one panel with the speaker amplifier and OLED fitted, a 5 V USB wall adapter and an A-to-C cable (no computer on the
panel's USB), the station service on a computer on the same 2.4 GHz network (the C3 has no 5 GHz), esptool 5.6.0, a USB
serial terminal (115200). Station side: `sudo spoolwarden weigh-station panel-wifi` (the station branch's name for it; DOC1
may rename it). The images are `firmware/intake-panel.bin` (whole, at 0x0) and `firmware/intake-panel-program.bin`
(program only, at 0x10000).

## B1. Antenna and range, case closed (the gate: Hardware proposal H4)
- Do: with the panel in its enclosure, set it up (B7) and let it join. Log the access point's RSSI and retry count for the panel,
  then 30 cold starts (unplug, plug in), at the working position. Repeat with the amplifier playing speech (I2S on GPIO 20/21
  runs beside the antenna) and a bare board of the same kind outside the case as the reference.
- See: how many of the 30 starts reach "Wi-Fi: connected" and then link up; RSSI against the reference.
- Pass: 30 of 30; RSSI within 6 dB of the reference at the same spot; no drop in 24 h (est).

## B2. TLS handshake time and speech stutter (H8, H5; R88 prove-first 4)
- Do: link up, then play speech phrases back to back for 10 minutes (volume 50) with the tag reader polling and the OLED
  updating; also reconnect 30 times (restart the station service) while a phrase plays. A bench build that prints a timestamp
  at the handshake start and end, the minimum free heap and the largest free block over serial is needed: the release
  firmware prints none of this.
- See: handshake time; whether the speech breaks up (listen), especially while a handshake runs; free heap during a handshake.
- Pass: handshake plus HELLO under 2 s (est; the station allows 5 s); no audible dropout in the steady run; no underrun
  from the handshake (the link task runs at the loop's priority, below the speech task); minimum free heap at least 40 KB (est).

## B3. Update keeps the Wi-Fi set-up, a whole flash wipes it (TO PROVE FIRST: one panel, both ways)
- Do: panel on firmware 14, set up and linked. (a) Flash the program image only:
  `python -m esptool --chip esp32c3 --port <port> write-flash 0x10000 intake-panel-program.bin`. (b) Set up again, then flash
  the whole image: `... write-flash 0x0 intake-panel.bin`. Also do (a) and (b) through the station's own flash command
  (`weigh-station flash-display`), which should choose the image by the panel's reported version.
- See: after (a) the panel joins and links with no set-up. After (b) it behaves as unconfigured (the USB texts, no Wi-Fi) and the
  station says "The panel was flashed from scratch, so its Wi-Fi set-up is gone."
- Pass: (a) NVS kept, (b) NVS gone, on the same panel. The shipped whole image holds 0xFF over 0x9000-0xDFFF (checked in the
  file), so (b) is expected; (a) rests on esptool writing only the sectors it is given (not tested). If (a) loses the set-up,
  the program image must not be used for updates and `LAYOUT_BREAKS`/the update rule go back to the whole image.

## B4. Supply with Wi-Fi and the amplifier (H1-H3)
- Do: wall adapter, no computer. Both lamps lit, speech at volume 50 and 100, tag reader on. Measure the 5 V current with a
  peak-hold meter (or a shunt and scope) and the 3V3 and 5V rails during Wi-Fi transmit bursts. Firmware 14 switches modem
  sleep off (`WiFi.setSleep(false)`) so frames are not held back by the access point's DTIM interval: this raises the idle
  current; compare with the default sleep mode.
- See: peak 5 V current, lowest 3V3 and 5V voltage, any brown-out reset (the panel re-announces itself, the display flickers).
- Pass: 5 V peak at or under 500 mA, or the owner restates the standard's rule for wall-powered devices; 3V3 never below 3.0 V,
  5V never below 4.5 V (est); no reset.

## B5. K cadence and the link under real load
- Do: a steady hour with the Hub, the station and the panel in normal use (tags, buttons, speech) and a second device saturating
  the router for 10 minutes. Read the station log for "button panel on Wi-Fi gone (silent)" lines; watch the panel for the
  "No Weigh Station service" screen.
- See: how many times the link dropped, and the time from a drop to the first lamp command after the reconnect.
- Pass: no "silent" drop in the hour; K lines never more than 1.5 s apart (capture with the station's debug log or Wireshark on
  the station's port); a reconnect within 15 s of the cause ending (est).

## B6. Wrong certificate shows the right screen and sends no token
- Do: on the station, replace its certificate (delete `cert.pem` and `key.pem` in the station's state folder and restart the
  service) so its fingerprint no longer matches the panel's. Watch the panel and the station log.
- See: the panel shows "Wrong certificate" over "Set up again by USB". The station log shows connections that end during
  or right after the handshake and NO "wrong token" line and no HELLO (the panel must not have sent the token). Run
  `panel-wifi` again: the panel links.
- Pass: the screen, no HELLO or token on the wire (check with a capture on the station's port 5004: only the TLS handshake, then a
  close), and recovery after a new set-up. Also check that "Wrong certificate" (17 characters) fits the title line: the firmware
  falls back to the small font if it does not, which is untested on a real OLED.

## B7. Set-up over USB, stored settings round trip
- Do: with the panel on USB and the station service stopped (or `panel-wifi` as the station command runs it), run the
  set-up. Then unplug, power-cycle on the wall adapter, and see it join. Then by hand in the terminal (115200, each line ended
  with a newline): a valid `CFG WIFI` and `CFG LINK` line (copy the station's), then each failing case: a 257-character line,
  a bad hex digit, an odd-length hex name, a 33-byte name, a 7-byte and a 64-byte password, a 65-character address, port 0
  and 65536, a 63- and a 65-digit fingerprint, a 31-digit token, `CFG CLEAR`, `CFG FOO`.
- See: `OK` after each valid line (and only then; the panel keeps the values across a power cycle), `ERR long`, `ERR hex`,
  `ERR ssid`, `ERR password`, `ERR address`, `ERR port`, `ERR fingerprint`, `ERR token`, `ERR cfg` for the others; after
  `CFG CLEAR` the panel forgets the network and shows the USB texts.
- Pass: every answer as listed; the panel never answers `OK` for a line it did not store; a 256-character line is accepted
  (a name, password and address at their caps). Also: an open network (password `-`), a name with non-ASCII characters, a hidden
  network, and WPA3-only or enterprise networks (not supported: say what the panel shows). A name that contains a NUL byte is not
  supported by the Wi-Fi call and is not tested.
- Also test the address forms: an IPv4 address, a DNS name, and a `.local` name. The panel resolves names with lwIP's resolver, which
  does not do mDNS, so a `.local` name probably fails ("No Weigh Station service" with Wi-Fi connected): record what happens.

## B8. Reconnect after the station restarts, and after the access point restarts
- Do: restart the station service; then power-cycle the access point; then unplug the router while a phrase plays.
- See: the panel shows "No Weigh Station service" with its Wi-Fi state ("joining", "no network" after 10 s, "connected"), then
  links again without a button press or a reset; the lamps and speech resume.
- Pass: it links again by itself within 15 s of the service or network returning; the waits between dials follow 1, 2, 4, 8, then
  15 s (read them from the station log's connection times). The screen never freezes and the buttons keep working while it is down.

## B9. Refusals and the waits
- Do: (a) wrong token: edit `panel_link.xml` in the station's state folder (or re-run `panel-wifi` against a different panel) so the
  token differs. (b) USB: plug the panel into the station computer's USB while the station runs and the panel is on Wi-Fi.
  (c) busy: a second Wi-Fi panel connected (needs two panels).
- See: (a) the panel shows "Refused: wrong token" and the station log shows one attempt every 30 s (plus the station's own 1 s
  delay). (b) the panel leaves Wi-Fi at once and the station uses USB; unplug USB and the panel returns to Wi-Fi within about 10 s.
  (c) the second panel retries every 2 s.
- Pass: the 30 s, 2 s and USB behaviour as stated; with USB plugged and the station talking, no Wi-Fi attempts.

## B10. The scale-less station over Wi-Fi, end to end
- Do: the real station with no scale: scan a tag with the panel's reader (and a USB one), press yellow, long-press yellow, press red.
- See: the buttons, `TAG` and `AIR` lines arrive over Wi-Fi as they do on USB; the station's checked-out-spool return works.
- Pass: same behaviour as on USB; the station's Devices row says "Panel: on Wi-Fi".

## B11. A save is all or none, and CFG CLEAR tells the truth (host-tested only; the NVS layer under it is not)
- Do: set up a network and a station (B7). Then (a) send a new `CFG WIFI` and pull the USB cable during the write, several times;
  after each, power the panel from the wall and see which network it joins. (b) `CFG CLEAR`, then power-cycle.
- See: after (a) the panel joins the old network with the old password, or the new one with the new password, never a mix; a
  save answered `ERR store` leaves the old set. After (b) it answers `OK` and behaves as unconfigured.
- Pass: no mixed set in any of the cuts. The layout is two slots of keys and a marker flipped last (lib/panel_core/src/panel_store.h);
  native tests fail every write position against a fake store, so only the real NVS calls (`putBytes`, `remove`, `isKey`) are unproven.

## B12. The build refuses a program over 95 % of the app slot
- Do: `sh build.sh` prints `ok: panel program: ... bytes in the app slot (NN.N%, limit 95.0%)`. To see it fail without a big
  program: `FK_PANEL_MAX_PERCENT=80 sh build.sh`.
- Pass: the second exits non-zero with `FAIL: ...` and writes no image (the check runs before the images are merged).

## Known and not measured
- Image size: the program image is 1,148,240 bytes of the 1,310,720-byte application slot (87.6 %), above the proposal's 80 %
  target (est); the ruling accepts the program at 85.5 % of the slot, and build.sh fails above 95 % (B12). There is no over-the-air update, the slot is fixed by the partition table, and USB flashing does not need headroom, so
  this is a margin, not a failure, but the next feature will reach the slot's end. Nothing was measured of free heap at run time.
- The loop's own work never waits on the network (the link runs in its own task), but a lookup or handshake that never ends is only
  abandoned at its bound (2 s and 3 s), not killed, until it returns; at most one lookup task runs at a time.
- The Wi-Fi password sits in the panel's flash in plain text (flash encryption is not set up); anyone with the USB port can read it.
