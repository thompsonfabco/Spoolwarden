#include "panel_link.h"
#include <stdio.h>
#include <string.h>

namespace panel {

uint32_t backoffMs(uint32_t failures) {
  if (failures == 0) return 0;
  uint32_t ms = 1000;
  for (uint32_t i = 1; i < failures && ms < BACKOFF_MAX_MS; i++) ms *= 2;
  return ms > BACKOFF_MAX_MS ? BACKOFF_MAX_MS : ms;  // 1, 2, 4, 8, 15, 15 ...
}

static bool after(uint32_t now, uint32_t t) { return (int32_t)(now - t) >= 0; }

// Why the attempt ended, and how long to wait before the next one (a refusal's own wait does not grow the backoff).
void Link::fail(LinkIo &io, Why why, uint32_t waitMs, bool count) {
  io.close();
  why_ = why;
  if (count) { failures_++; waitMs = backoffMs(failures_); }
  else failures_ = 0;  // the station answered: the connection worked
  uint32_t now = io.now();
  waitUntil_ = now + waitMs;
  helloSent_ = false; firstLen_ = 0;
  goPhase(Phase::Waiting, now);
}

void Link::step(LinkIo &io) {
  uint32_t now = io.now();

  uint32_t g = cfgGen_;  // new settings from the loop (a seqlock read: an odd or changed generation is tried again)
  if (!(g & 1) && g != seenGen_) {
    Settings tmp = pending_;
    if (cfgGen_ == g) {
      cfg_ = tmp; seenGen_ = g;
      io.close();
      if (radioOn_) io.wifiStop();  // new settings, new join
      radioOn_ = joined_ = stalled_ = certBad_ = helloSent_ = false;
      failures_ = 0; why_ = Why::None; firstLen_ = 0;
      goPhase(Phase::Off, now);
    }
  }

  if (!cfg_.configured()) {
    if (phase_ != Phase::Off) {
      io.close();
      if (radioOn_) io.wifiStop();
      radioOn_ = joined_ = stalled_ = certBad_ = false;
      why_ = Why::None;
      goPhase(Phase::Off, now);
    }
    return;
  }
  if (usb_) {  // USB wins: the station is talking to the panel over the cable, so there is no Wi-Fi link
    if (phase_ != Phase::UsbWins) {
      io.close();
      helloSent_ = false; firstLen_ = 0;
      goPhase(Phase::UsbWins, now);
    }
    return;
  }
  if (phase_ == Phase::Off || phase_ == Phase::UsbWins) {  // (back) to work: the radio may be on already
    failures_ = 0; waitUntil_ = now; joinAt_ = beginAt_ = now; stalled_ = false;
    goPhase(Phase::Joining, now);
  }

  switch (phase_) {
    case Phase::Joining:
      if (!radioOn_) { io.wifiBegin(cfg_); radioOn_ = true; joinAt_ = beginAt_ = now; }
      if (io.wifiConnected()) {
        joined_ = true; stalled_ = false;
        goPhase(Phase::Waiting, now);
      } else {
        joined_ = false;
        if (now - joinAt_ >= WIFI_STALL_MS) stalled_ = true;
        if (now - beginAt_ >= WIFI_RETRY_MS) { io.wifiBegin(cfg_); beginAt_ = now; }
      }
      break;

    case Phase::Waiting:
      if (!io.wifiConnected()) {
        joined_ = false; joinAt_ = beginAt_ = now; stalled_ = false;
        goPhase(Phase::Joining, now);
        break;
      }
      joined_ = true;
      if (!after(now, waitUntil_)) break;
      io.lookupStart(cfg_.addr);
      goPhase(Phase::Resolving, now);
      break;

    case Phase::Resolving: {
      LookupState st = io.lookupPoll();
      if (st == LookupState::Done) goPhase(Phase::Connecting, now);
      else if (st == LookupState::Failed || now - phaseAt_ >= LOOKUP_MS) fail(io, Why::NoRoute, 0, true);
      break;
    }

    case Phase::Connecting:
      if (io.tcpConnect(cfg_.port, CONNECT_MS)) goPhase(Phase::Handshaking, io.now());
      else fail(io, Why::NoConnect, 0, true);
      break;

    case Phase::Handshaking: {
      if (!io.tlsHandshake(HANDSHAKE_MS)) { fail(io, Why::NoHandshake, 0, true); break; }
      // The pin, after the handshake and BEFORE anything is sent: a wrong certificate gets neither the HELLO nor the
      // token, only a close.
      uint8_t sha[32];
      if (!io.peerFingerprint(sha) || !fingerprintMatches(cfg_.fp, sha)) {
        certBad_ = true;
        fail(io, Why::WrongCert, 0, true);
        break;
      }
      certBad_ = false;
      helloSent_ = false; firstLen_ = 0;
      goPhase(Phase::Hello, io.now());
      break;
    }

    case Phase::Hello:
      helloStep(io);
      break;

    case Phase::Up:
      upStep(io);
      break;

    default:
      break;
  }
}

void Link::helloStep(LinkIo &io) {
  if (!helloSent_) {
    char l[MAX_LINE + 2];
    size_t n = helloLine(l, MAX_LINE, version_, cfg_.token);
    l[n++] = '\n';
    if (!io.write((const uint8_t *)l, n, WRITE_MS)) { fail(io, Why::WriteFailed, 0, true); return; }
    helloSent_ = true; firstLen_ = 0;
    tx_.clear();  // lines the loop queued for an earlier link are stale
    helloAt_ = lastTx_ = io.now();
    return;
  }
  uint32_t now = io.now();
  int n = io.read(first_ + firstLen_, sizeof first_ - firstLen_);
  if (n < 0) { fail(io, Why::Closed, 0, true); return; }
  if (n == 0) {
    if (now - helloAt_ >= HELLO_REPLY_MS) fail(io, Why::Silent, 0, true);
    return;
  }
  firstLen_ += (size_t)n;
  static const char P[] = "REFUSED ";
  size_t m = firstLen_ < sizeof P - 1 ? firstLen_ : sizeof P - 1;
  if (memcmp(first_, P, m) != 0) {  // not a refusal: the station is speaking the protocol, the link is up
    rx_.push(first_, firstLen_);
    firstLen_ = 0;
    failures_ = 0; upCount_++; why_ = Why::None; lastRx_ = now;
    goPhase(Phase::Up, now);
    return;
  }
  if (firstLen_ < sizeof P - 1) return;  // still the start of a possible REFUSED
  const uint8_t *nl = (const uint8_t *)memchr(first_, '\n', firstLen_);
  if (!nl && firstLen_ < sizeof first_) return;  // the rest of the line
  char reason[16] = "";
  size_t from = sizeof P - 1, to = nl ? (size_t)(nl - first_) : firstLen_;
  for (size_t i = from; i < to && i - from < sizeof reason - 1; i++) {
    char c = (char)first_[i];
    if (c == '\r') break;
    reason[i - from] = c;
  }
  if (strcmp(reason, "usb") == 0) fail(io, Why::RefusedUsb, REFUSED_LONG_MS, false);
  else if (strcmp(reason, "token") == 0) fail(io, Why::RefusedToken, REFUSED_LONG_MS, false);
  else if (strcmp(reason, "busy") == 0) fail(io, Why::RefusedBusy, REFUSED_BUSY_MS, false);
  else fail(io, Why::RefusedOther, 0, true);  // "REFUSED hello" and the like: an ordinary failed connection
}

void Link::upStep(LinkIo &io) {
  uint32_t now = io.now();
  if (!io.wifiConnected()) {  // the network is gone: join again, then dial
    io.close();
    joined_ = false; joinAt_ = beginAt_ = now; stalled_ = false; why_ = Why::Closed;
    goPhase(Phase::Joining, now);
    return;
  }
  uint8_t b[128];
  for (size_t budget = 1024; budget >= sizeof b && rx_.room() >= sizeof b; budget -= sizeof b) {
    int n = io.read(b, sizeof b);
    if (n < 0) { fail(io, Why::Closed, 0, true); return; }
    if (n == 0) break;
    rx_.push(b, (size_t)n);
    lastRx_ = io.now();
  }
  uint8_t w[256];
  size_t n = tx_.peek(w, sizeof w);
  if (n) {
    if (!io.write(w, n, WRITE_MS)) { fail(io, Why::WriteFailed, 0, true); return; }
    tx_.drop(n);
    lastTx_ = io.now();
  } else if (after(now, lastTx_ + KEEPALIVE_MS)) {  // idle: K, which is all the station's keep-alive needs
    static const uint8_t K[] = {'K', '\n'};
    if (!io.write(K, sizeof K, WRITE_MS)) { fail(io, Why::WriteFailed, 0, true); return; }
    lastTx_ = io.now();
  }
  if (io.now() - lastRx_ >= SILENCE_MS) fail(io, Why::Silent, 0, true);
}

// ---- the OLED's words ----
static void put(char *row, const char *text) { snprintf(row, 22, "%s", text); }

bool netScreen(const NetView &v, Screen &out) {
  if (!v.configured || v.phase == Phase::UsbWins) return false;
  memset(&out, 0, sizeof out);
  if (v.wrongCert) {
    put(out.rows[0], "Wrong certificate");
    put(out.rows[1], "Set up again by USB");
    return true;
  }
  put(out.rows[0], "No Weigh Station");  // "No Weigh Station service", over two rows: the title font is not 24 wide
  put(out.rows[1], "service");
  put(out.rows[2], !v.wifiJoined ? (v.wifiStalled ? "Wi-Fi: no network" : "Wi-Fi: joining") : "Wi-Fi: connected");
  if (v.wifiJoined && v.why == Why::RefusedToken) put(out.rows[3], "Refused: wrong token");
  else if (v.wifiJoined && v.why == Why::RefusedUsb) put(out.rows[3], "Refused: USB in use");
  else if (v.wifiJoined && v.why == Why::RefusedBusy) put(out.rows[3], "Refused: busy");
  else if (v.settings && v.settings->ssidLen) {
    char name[17] = "";
    for (size_t i = 0; i < v.settings->ssidLen && i < sizeof name - 1; i++) {
      uint8_t c = v.settings->ssid[i];
      name[i] = (c >= 0x20 && c < 0x7f) ? (char)c : '?';
    }
    snprintf(out.rows[3], sizeof out.rows[3], "Net: %s", name);
  }
  return true;
}

}  // namespace panel
