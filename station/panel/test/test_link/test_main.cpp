// Host-side (PlatformIO native) tests for lib/panel_core/panel_link: the Wi-Fi link state machine against a fake
// network. What they prove: the pin comes before anything is sent (a wrong certificate gets a close and no HELLO, so
// no token), the HELLO line, K every 500 ms when idle, the wait after a failed connection (1, 2, 4, 8, 15, 15 s) and
// after REFUSED usb / token (30 s) / busy (2 s), that no call ever waits longer than its bound (connect 2 s,
// handshake 3 s, write 1 s, lookup 2 s) and that a step makes at most one waiting call, USB winning, and the screen
// texts. Nothing here ran on a C3. Run: pio test -e native
#include <unity.h>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#include "panel_link.h"

using namespace panel;

void setUp() {}
void tearDown() {}

#define CHECK(cond, ...) do { char msg_[400]; snprintf(msg_, sizeof msg_, __VA_ARGS__); TEST_ASSERT_TRUE_MESSAGE(cond, msg_); } while (0)

static const char *TOKEN = "77777777777777777777777777777777";

struct FakeIo : LinkIo {
  uint32_t t = 100000;
  bool wifi = true, connectOk = true, handshakeOk = true, writeOk = true, shaOk = true, readClosed = false;
  LookupState lookup = LookupState::Done;
  uint8_t sha[32];
  std::string rxData;
  std::vector<std::string> writes;       // every write, whole
  std::vector<uint32_t> writeAt;
  int wifiBegins = 0, wifiStops = 0, closes = 0, lookups = 0, connects = 0, handshakes = 0;
  uint32_t maxBound = 0;                 // the largest bound any waiting call was given
  uint32_t boundConnect = 0, boundHandshake = 0, boundWrite = 0;
  int waitingCalls = 0;                  // since the last reset (a step)
  uint32_t spent = 0;                    // fake time the waiting calls used in this step

  FakeIo() { memset(sha, 0xaa, sizeof sha); }
  uint32_t now() override { return t; }
  void wifiBegin(const Settings &) override { wifiBegins++; }
  void wifiStop() override { wifiStops++; wifi = false; }
  bool wifiConnected() override { return wifi; }
  void lookupStart(const char *) override { lookups++; }
  LookupState lookupPoll() override { return lookup; }
  bool tcpConnect(uint16_t, uint32_t ms) override {
    connects++; waitingCalls++; boundConnect = ms; if (ms > maxBound) maxBound = ms;
    uint32_t used = connectOk ? 3 : ms; t += used; spent += used;
    return connectOk;
  }
  bool tlsHandshake(uint32_t ms) override {
    handshakes++; waitingCalls++; boundHandshake = ms; if (ms > maxBound) maxBound = ms;
    uint32_t used = handshakeOk ? 250 : ms; t += used; spent += used;
    return handshakeOk;
  }
  bool peerFingerprint(uint8_t out[32]) override { memcpy(out, sha, 32); return shaOk; }
  bool write(const uint8_t *p, size_t n, uint32_t ms) override {
    waitingCalls++; boundWrite = ms; if (ms > maxBound) maxBound = ms;
    uint32_t used = writeOk ? 1 : ms; t += used; spent += used;
    if (!writeOk) return false;
    writes.emplace_back((const char *)p, n); writeAt.push_back(t);
    return true;
  }
  int read(uint8_t *p, size_t n) override {
    if (rxData.empty()) return readClosed ? -1 : 0;
    size_t k = rxData.size() < n ? rxData.size() : n;
    memcpy(p, rxData.data(), k);
    rxData.erase(0, k);
    return (int)k;
  }
  void close() override { closes++; }
};

struct Rig {
  ByteRing<4096> rx;
  ByteRing<1024> tx;
  Link link{rx, tx, "14"};
  FakeIo io;
  Rig() {
    Settings s;
    cfgParse(("CFG WIFI " + std::string("6e") + " " + "-").c_str(), s);
    cfgParse(("CFG LINK 192.168.1.50 5004 " + std::string(64, 'a') + " " + TOKEN).c_str(), s);
    link.configure(s);
  }
  // One step; the fake clock moves by `ms` on top of whatever the waiting calls used.
  void step(uint32_t ms = 10) {
    io.t += ms; io.waitingCalls = 0; io.spent = 0;
    link.step(io);
    CHECK(io.waitingCalls <= 1, "a step made %d waiting calls", io.waitingCalls);
    CHECK(io.spent <= HANDSHAKE_MS, "a step used %u ms", (unsigned)io.spent);
  }
  void until(Phase p, int maxSteps = 200) {
    for (int i = 0; i < maxSteps && link.phase() != p; i++) step();
    CHECK(link.phase() == p, "phase %d, wanted %d", (int)link.phase(), (int)p);
  }
  // Steps (at least once) until an attempt has failed: back in Waiting with a reason.
  void failed(int maxSteps = 400) {
    for (int i = 0; i < maxSteps; i++) {
      step();
      if (link.phase() == Phase::Waiting && link.why() != Why::None) return;
    }
    CHECK(false, "no attempt failed (phase %d)", (int)link.phase());
  }
  // Up to the station's first line.
  void bringUp(const char *firstBytes = "L 0 0\n") {
    until(Phase::Hello);
    io.rxData = firstBytes;
    until(Phase::Up);
  }
};

// ---------- the schedule ----------
void test_backoff_schedule() {
  uint32_t want[] = {0, 1000, 2000, 4000, 8000, 15000, 15000, 15000, 15000};
  for (uint32_t n = 0; n < 9; n++) CHECK(backoffMs(n) == want[n], "after %u failures: %u, wanted %u", (unsigned)n, (unsigned)backoffMs(n), (unsigned)want[n]);
  CHECK(backoffMs(100) == 15000 && backoffMs(4000000000u) == 15000, "stays at 15 s");
}

void test_failed_connections_wait_1_2_4_8_15_15() {
  Rig r;
  r.io.connectOk = false;
  uint32_t want[] = {1000, 2000, 4000, 8000, 15000, 15000, 15000};
  for (uint32_t w : want) {
    r.failed();
    uint32_t wait = r.link.waitUntil() - r.io.t;
    CHECK(wait == w, "wait %u ms, wanted %u (failure %u)", (unsigned)wait, (unsigned)w, (unsigned)r.link.failures());
    CHECK(r.link.why() == Why::NoConnect, "why");
    r.io.t = r.link.waitUntil();  // and then the next attempt
  }
}

void test_a_failure_does_not_dial_again_early() {
  Rig r;
  r.io.connectOk = false;
  r.failed();
  int connects = r.io.connects;
  for (int i = 0; i < 90; i++) r.step(10);  // 900 ms: inside the 1 s wait
  CHECK(r.io.connects == connects, "dialled again inside the wait");
  for (int i = 0; i < 20; i++) r.step(10);
  CHECK(r.io.connects == connects + 1, "dialled once the wait was over: %d", r.io.connects - connects);
}

void test_success_resets_the_backoff() {
  Rig r;
  r.io.connectOk = false;
  for (int i = 0; i < 3; i++) { r.failed(); r.io.t = r.link.waitUntil(); }
  CHECK(r.link.failures() == 3, "failures %u", (unsigned)r.link.failures());
  r.io.connectOk = true;
  r.bringUp();
  CHECK(r.link.failures() == 0, "failures after a good connection: %u", (unsigned)r.link.failures());
  r.io.rxData.clear(); r.io.readClosed = true;
  r.step();
  CHECK(r.link.phase() == Phase::Waiting && r.link.waitUntil() - r.io.t == 1000, "the first wait after a dropped link is 1 s");
}

// ---------- the pin ----------
void test_hello_and_token_go_only_after_the_pin() {
  Rig r;
  r.until(Phase::Hello);
  CHECK(r.io.handshakes == 1, "handshake");
  CHECK(r.io.writes.empty(), "nothing written before the HELLO step");
  r.step();
  CHECK(r.io.writes.size() == 1, "one write: %d", (int)r.io.writes.size());
  CHECK(r.io.writes[0] == std::string("HELLO intake-panel 14 esp32c3 ") + TOKEN + "\n", "first line: %s", r.io.writes[0].c_str());
}

void test_wrong_certificate_sends_nothing() {
  Rig r;
  memset(r.io.sha, 0xcd, sizeof r.io.sha);  // not the pinned fingerprint
  r.failed();
  CHECK(r.io.handshakes == 1, "the handshake happened");
  CHECK(r.io.writes.empty(), "no HELLO and no token on a wrong certificate");
  CHECK(r.io.closes >= 1, "closed");
  CHECK(r.link.why() == Why::WrongCert && r.link.wrongCert(), "reason");
  // and it keeps trying on the schedule, never sending
  for (int i = 0; i < 4; i++) { r.io.t = r.link.waitUntil(); r.failed(); }
  CHECK(r.io.writes.empty(), "still nothing sent after repeated wrong certificates");
  NetView v; v.configured = true; v.phase = r.link.phase(); v.why = r.link.why(); v.wrongCert = r.link.wrongCert();
  v.wifiJoined = true;
  Screen s;
  CHECK(netScreen(v, s), "a screen");
  CHECK(std::string(s.rows[0]) == "Wrong certificate" && std::string(s.rows[1]) == "Set up again by USB", "%s / %s", s.rows[0], s.rows[1]);
}

void test_unreadable_certificate_is_a_wrong_certificate() {
  Rig r;
  r.io.shaOk = false;
  r.failed();
  CHECK(r.io.writes.empty() && r.link.wrongCert(), "no fingerprint, nothing sent");
}

void test_the_right_certificate_clears_the_warning() {
  Rig r;
  memset(r.io.sha, 0xcd, sizeof r.io.sha);
  r.failed();
  CHECK(r.link.wrongCert(), "warned");
  memset(r.io.sha, 0xaa, sizeof r.io.sha);
  r.io.t = r.link.waitUntil();
  r.bringUp();
  CHECK(!r.link.wrongCert() && r.link.up(), "cleared once the pinned certificate answers");
}

void test_uppercase_stored_fingerprint_matches() {
  Rig r;
  Settings s;
  cfgParse("CFG WIFI 6e -", s);
  cfgParse(("CFG LINK h 5004 " + std::string(64, 'A') + " " + TOKEN).c_str(), s);
  r.link.configure(s);
  r.bringUp();
  CHECK(r.link.up(), "A-F digits in the stored fingerprint");
}

void test_handshake_failure_is_a_failed_connection_not_a_wrong_certificate() {
  Rig r;
  r.io.handshakeOk = false;
  r.failed();
  CHECK(r.link.why() == Why::NoHandshake && !r.link.wrongCert() && r.io.writes.empty(), "reason %d", (int)r.link.why());
  CHECK(r.io.boundHandshake == HANDSHAKE_MS, "handshake bound %u", (unsigned)r.io.boundHandshake);
}

// ---------- the bounds ----------
void test_every_wait_has_its_bound() {
  Rig r;
  r.bringUp();
  for (int i = 0; i < 300; i++) r.step(10);
  CHECK(r.io.boundConnect == 2000 && r.io.boundHandshake == 3000 && r.io.boundWrite == 1000, "bounds %u %u %u",
        (unsigned)r.io.boundConnect, (unsigned)r.io.boundHandshake, (unsigned)r.io.boundWrite);
  CHECK(r.io.maxBound <= 3000, "no waiting call was given more than 3 s (%u)", (unsigned)r.io.maxBound);
  CHECK(CONNECT_MS <= 2000 && LOOKUP_MS <= 2000 && WRITE_MS <= 1000 && HANDSHAKE_MS <= 3000, "constants");
}

void test_a_lookup_that_never_answers_ends_at_2_s() {
  Rig r;
  r.io.lookup = LookupState::Pending;
  r.until(Phase::Resolving);
  uint32_t started = r.io.t;
  for (int i = 0; i < 500 && r.link.phase() == Phase::Resolving; i++) r.step(10);
  uint32_t took = r.io.t - started;
  CHECK(r.link.phase() == Phase::Waiting && r.link.why() == Why::NoRoute, "phase %d why %d", (int)r.link.phase(), (int)r.link.why());
  CHECK(took >= LOOKUP_MS && took <= LOOKUP_MS + 20, "gave up after %u ms", (unsigned)took);
  CHECK(r.io.connects == 0, "never connected");
}

void test_a_failed_lookup_is_a_failed_connection() {
  Rig r;
  r.io.lookup = LookupState::Failed;
  r.failed();
  CHECK(r.link.why() == Why::NoRoute && r.link.waitUntil() - r.io.t == 1000, "wait %u", (unsigned)(r.link.waitUntil() - r.io.t));
}

void test_a_stalled_write_ends_the_link_after_its_bound() {
  Rig r;
  r.bringUp();
  r.io.writeOk = false;
  uint8_t l[] = "P H\n";
  r.tx.push(l, 4);
  r.step();
  CHECK(r.link.phase() == Phase::Waiting && r.link.why() == Why::WriteFailed, "phase %d", (int)r.link.phase());
  CHECK(r.io.spent == WRITE_MS, "the write used %u ms", (unsigned)r.io.spent);
}

void test_a_stalled_hello_write_never_marks_it_sent() {
  Rig r;
  r.until(Phase::Hello);
  r.io.writeOk = false;
  r.step();
  CHECK(r.link.phase() == Phase::Waiting && r.link.why() == Why::WriteFailed, "phase %d", (int)r.link.phase());
}

// ---------- K ----------
void test_k_every_500_ms_when_idle() {
  Rig r;
  r.bringUp();
  size_t before = r.io.writes.size();
  r.io.writes.clear(); r.io.writeAt.clear();
  for (int i = 0; i < 300; i++) {  // 3 s in 10 ms steps, with a line from the station every second (so it stays up)
    if (i % 100 == 0) r.io.rxData += "L 0 0\n";
    r.step(10);
  }
  (void)before;
  int ks = 0;
  for (auto &w : r.io.writes) { CHECK(w == "K\n", "only K while idle, got '%s'", w.c_str()); ks++; }
  CHECK(ks >= 5 && ks <= 7, "%d K lines in 3 s (500 ms apart is 6)", ks);
  for (size_t i = 1; i < r.io.writeAt.size(); i++) {
    uint32_t gap = r.io.writeAt[i] - r.io.writeAt[i - 1];
    CHECK(gap >= 490 && gap <= 520, "K gap %u ms", (unsigned)gap);
  }
}

void test_a_panel_line_counts_as_the_keepalive_and_goes_unchanged() {
  Rig r;
  r.bringUp();
  r.io.writes.clear(); r.io.writeAt.clear();
  for (int i = 0; i < 40; i++) r.step(10);  // 400 ms idle
  uint8_t l[] = "P H\n";
  r.tx.push(l, 4);
  r.step(10);
  CHECK(r.io.writes.size() == 1 && r.io.writes[0] == "P H\n", "the button line went as it was");
  for (int i = 0; i < 40; i++) r.step(10);  // 400 ms after it: no K yet
  CHECK(r.io.writes.size() == 1, "no K inside 500 ms of a real line (%d writes)", (int)r.io.writes.size());
  for (int i = 0; i < 20; i++) { if (i == 5) r.io.rxData += "L 0 0\n"; r.step(10); }
  CHECK(r.io.writes.size() == 2 && r.io.writes[1] == "K\n", "then K");
}

void test_queued_lines_keep_their_order_and_boundaries() {
  Rig r;
  r.bringUp();
  r.io.writes.clear();
  std::string a = "AIR 22.50 61.0 1013.25\n", b = "TAG 04aabbcc\n";
  r.tx.push((const uint8_t *)a.data(), a.size());
  r.tx.push((const uint8_t *)b.data(), b.size());
  for (int i = 0; i < 3; i++) r.step(1);
  std::string all;
  for (auto &w : r.io.writes) all += w;
  CHECK(all.rfind(a + b, 0) == 0, "sent in order: '%s'", all.c_str());
}

// ---------- silence, the station's lines, refusals ----------
void test_five_seconds_of_silence_end_the_link() {
  Rig r;
  r.bringUp();
  for (int i = 0; i < 480; i++) r.step(10);  // 4.8 s: not yet
  CHECK(r.link.up(), "still up at 4.8 s");
  for (int i = 0; i < 40; i++) r.step(10);
  CHECK(!r.link.up() && r.link.why() == Why::Silent, "silent: phase %d why %d", (int)r.link.phase(), (int)r.link.why());
  CHECK(r.io.closes >= 1, "closed");
}

void test_any_line_from_the_station_keeps_it_alive() {
  Rig r;
  r.bringUp();
  for (int i = 0; i < 1000; i++) { if (i % 90 == 0) r.io.rxData += "L 1 0\n"; r.step(10); }
  CHECK(r.link.up(), "10 s with a line under every 1 s");
}

void test_the_stations_bytes_reach_the_loop_in_order() {
  Rig r;
  r.bringUp("L 1 2\nD 0 Hi\n");
  r.io.rxData = "A 4\n\x01\x02\x03\x04";
  for (int i = 0; i < 5; i++) r.step(1);
  uint8_t b[64];
  size_t n = r.rx.pop(b, sizeof b);
  std::string got((char *)b, n);
  CHECK(got == std::string("L 1 2\nD 0 Hi\nA 4\n\x01\x02\x03\x04"), "got %u bytes", (unsigned)n);
}

void test_refused_waits() {
  struct { const char *reply; uint32_t wait; Why why; } cases[] = {
      {"REFUSED usb\n", 30000, Why::RefusedUsb}, {"REFUSED token\n", 30000, Why::RefusedToken},
      {"REFUSED busy\n", 2000, Why::RefusedBusy}};
  for (auto &c : cases) {
    Rig r;
    r.until(Phase::Hello);
    r.io.rxData = c.reply;
    r.failed();
    CHECK(r.link.why() == c.why, "%s: why %d", c.reply, (int)r.link.why());
    uint32_t wait = r.link.waitUntil() - r.io.t;
    CHECK(wait >= c.wait - 30 && wait <= c.wait, "%s: waits %u ms", c.reply, (unsigned)wait);
    CHECK(r.link.failures() == 0, "a refusal does not grow the backoff");
    int connects = r.io.connects;
    for (uint32_t i = 0; i + 100 < c.wait; i += 100) r.step(100);
    CHECK(r.io.connects == connects, "%s: did not dial inside the wait", c.reply);
    for (int i = 0; i < 10; i++) r.step(100);
    CHECK(r.io.connects == connects + 1, "%s: dialled after it", c.reply);
  }
}

void test_refused_split_across_reads() {
  Rig r;
  r.until(Phase::Hello);
  r.io.rxData = "REFU";
  r.step(); r.step();
  CHECK(r.link.phase() == Phase::Hello, "still waiting for the rest");
  r.io.rxData = "SED to";
  r.step();
  CHECK(r.link.phase() == Phase::Hello, "and for the end of the line");
  r.io.rxData = "ken\r\n";
  r.step();
  CHECK(r.link.why() == Why::RefusedToken && r.link.phase() == Phase::Waiting, "token refusal: why %d", (int)r.link.why());
}

void test_other_refusals_are_failed_connections() {
  Rig r;
  r.until(Phase::Hello);
  r.io.rxData = "REFUSED hello\n";
  r.step(); r.step();  // the HELLO goes out, then the answer is read
  CHECK(r.link.why() == Why::RefusedOther && r.link.waitUntil() - r.io.t == 1000, "wait %u", (unsigned)(r.link.waitUntil() - r.io.t));
}

void test_a_first_line_that_only_starts_like_refused_is_data() {
  Rig r;
  r.bringUp("REFUSE\n");  // not REFUSED: whatever it is, it is passed on and the link is up
  CHECK(r.link.up(), "up");
}

void test_no_answer_to_the_hello_is_silence() {
  Rig r;
  r.until(Phase::Hello);
  r.step();  // HELLO written
  for (int i = 0; i < 480; i++) r.step(10);
  CHECK(r.link.phase() == Phase::Hello, "still waiting at 4.8 s");
  for (int i = 0; i < 40; i++) r.step(10);
  CHECK(r.link.phase() == Phase::Waiting && r.link.why() == Why::Silent, "gave up: phase %d", (int)r.link.phase());
}

void test_stale_lines_are_dropped_when_a_new_link_opens() {
  Rig r;
  uint8_t stale[] = "P D\n";
  r.tx.push(stale, 4);
  r.bringUp();
  for (auto &w : r.io.writes) CHECK(w.find("P D") == std::string::npos, "a stale line went out on the new link");
}

void test_each_link_up_is_counted() {
  Rig r;
  r.bringUp();
  CHECK(r.link.upCount() == 1, "1");
  r.io.readClosed = true; r.io.rxData.clear();
  r.step();
  r.io.readClosed = false;
  r.io.t = r.link.waitUntil();
  r.bringUp();
  CHECK(r.link.upCount() == 2, "2: %u", (unsigned)r.link.upCount());
}

// ---------- USB wins, set-up, the network ----------
void test_usb_wins_and_wifi_resumes() {
  Rig r;
  r.bringUp();
  r.link.setUsbActive(true);
  r.step();
  CHECK(r.link.phase() == Phase::UsbWins && !r.link.up() && r.io.closes >= 1, "closed for USB");
  int connects = r.io.connects;
  for (int i = 0; i < 300; i++) r.step(10);
  CHECK(r.io.connects == connects && r.io.waitingCalls == 0, "no dialling while USB carries the panel");
  size_t writes = r.io.writes.size();
  for (int i = 0; i < 100; i++) r.step(10);
  CHECK(r.io.writes.size() == writes, "nothing written to Wi-Fi while USB carries the panel");
  NetView v; v.configured = true; v.phase = r.link.phase();
  Screen s;
  CHECK(!netScreen(v, s), "USB's own texts are shown");
  r.link.setUsbActive(false);
  r.bringUp();
  CHECK(r.link.up(), "back on Wi-Fi once USB is quiet");
}

void test_usb_active_from_the_start_never_dials() {
  Rig r;
  r.link.setUsbActive(true);
  for (int i = 0; i < 100; i++) r.step(10);
  CHECK(r.io.lookups == 0 && r.io.connects == 0 && r.io.wifiBegins == 0, "no radio, no dial");
}

void test_unconfigured_panel_never_uses_the_radio() {
  ByteRing<4096> rx; ByteRing<1024> tx;
  Link link(rx, tx, "14");
  FakeIo io;
  for (int i = 0; i < 100; i++) { io.t += 10; link.step(io); }
  CHECK(io.wifiBegins == 0 && io.lookups == 0 && link.phase() == Phase::Off, "off");
  Settings s;  // a Wi-Fi line alone is not enough
  cfgParse("CFG WIFI 6e -", s);
  link.configure(s);
  for (int i = 0; i < 100; i++) { io.t += 10; link.step(io); }
  CHECK(io.wifiBegins == 0 && link.phase() == Phase::Off, "wifi without a link line stays off");
  NetView v;
  Screen sc;
  CHECK(!netScreen(v, sc), "not set up: the USB texts");
}

void test_clearing_the_settings_stops_the_radio_and_the_link() {
  Rig r;
  r.bringUp();
  Settings none;
  r.link.configure(none);
  r.step();
  CHECK(r.link.phase() == Phase::Off && r.io.wifiStops == 1 && r.io.closes >= 1, "stopped");
  int connects = r.io.connects;
  for (int i = 0; i < 200; i++) r.step(10);
  CHECK(r.io.connects == connects, "never dials again");
}

void test_new_settings_restart_the_join() {
  Rig r;
  r.bringUp();
  int begins = r.io.wifiBegins;
  Settings s;
  cfgParse("CFG WIFI 6f -", s);
  cfgParse(("CFG LINK other 5004 " + std::string(64, 'a') + " " + TOKEN).c_str(), s);
  r.link.configure(s);
  r.io.wifi = true;
  r.step();
  CHECK(!r.link.up() && r.io.wifiStops == 1, "the old link is dropped");
  r.io.wifi = true;
  r.bringUp();
  CHECK(r.io.wifiBegins == begins + 1, "it joined again with the new settings");
}

void test_wifi_join_stalls_then_is_retried() {
  Rig r;
  r.io.wifi = false;
  for (int i = 0; i < 500; i++) r.step(10);  // 5 s
  CHECK(r.link.phase() == Phase::Joining && !r.link.wifiStalled(), "joining, not stalled at 5 s");
  for (int i = 0; i < 600; i++) r.step(10);  // 11 s
  CHECK(r.link.wifiStalled(), "no network after 10 s");
  NetView v; v.configured = true; v.phase = r.link.phase(); v.wifiJoined = false; v.wifiStalled = true;
  Screen s;
  CHECK(netScreen(v, s) && std::string(s.rows[2]) == "Wi-Fi: no network", "screen: %s", s.rows[2]);
  for (int i = 0; i < 1000; i++) r.step(10);
  CHECK(r.io.wifiBegins >= 2, "the join was begun again (%d)", r.io.wifiBegins);
  CHECK(r.io.lookups == 0, "no dial without a network");
  r.io.wifi = true;
  r.bringUp();
  CHECK(r.link.up() && !r.link.wifiStalled(), "up once the network is there");
}

void test_losing_the_network_while_up_goes_back_to_joining() {
  Rig r;
  r.bringUp();
  r.io.wifi = false;
  r.step();
  CHECK(r.link.phase() == Phase::Joining && !r.link.up() && r.io.closes >= 1, "phase %d", (int)r.link.phase());
}

// ---------- the ring ----------
void test_ring_is_all_or_nothing_and_wraps() {
  ByteRing<8> ring;
  uint8_t a[5] = {1, 2, 3, 4, 5}, out[8];
  CHECK(ring.push(a, 5) && ring.used() == 5 && ring.room() == 2, "push");
  CHECK(!ring.push(a, 3), "3 more do not fit and none go in");
  CHECK(ring.used() == 5, "unchanged");
  CHECK(ring.pop(out, 3) == 3 && out[0] == 1 && out[2] == 3, "pop 3");
  CHECK(ring.push(a, 4) && ring.used() == 6, "wraps");
  CHECK(ring.pop(out, 8) == 6 && out[0] == 4 && out[1] == 5 && out[2] == 1 && out[5] == 4, "order across the wrap");
  ring.push(a, 2); ring.clear();
  CHECK(ring.used() == 0, "clear");
}

// ---------- the OLED's words ----------
void test_no_service_screen() {
  Settings s;
  cfgParse("CFG WIFI 486f6d65 -", s);   // "Home"
  NetView v; v.configured = true; v.settings = &s; v.phase = Phase::Waiting; v.wifiJoined = true; v.why = Why::NoConnect;
  Screen sc;
  CHECK(netScreen(v, sc), "a screen");
  CHECK(std::string(sc.rows[0]) + " " + sc.rows[1] == "No Weigh Station service", "title: '%s %s'", sc.rows[0], sc.rows[1]);
  CHECK(std::string(sc.rows[2]) == "Wi-Fi: connected" && std::string(sc.rows[3]) == "Net: Home", "'%s' '%s'", sc.rows[2], sc.rows[3]);
  v.wifiJoined = false;
  netScreen(v, sc);
  CHECK(std::string(sc.rows[2]) == "Wi-Fi: joining", "joining: %s", sc.rows[2]);
  v.wifiJoined = true; v.why = Why::RefusedToken;
  netScreen(v, sc);
  CHECK(std::string(sc.rows[3]) == "Refused: wrong token", "%s", sc.rows[3]);
  for (int i = 0; i < 5; i++) CHECK(strlen(sc.rows[i]) <= 21, "row %d is %d chars", i, (int)strlen(sc.rows[i]));
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_backoff_schedule);
  RUN_TEST(test_failed_connections_wait_1_2_4_8_15_15);
  RUN_TEST(test_a_failure_does_not_dial_again_early);
  RUN_TEST(test_success_resets_the_backoff);
  RUN_TEST(test_hello_and_token_go_only_after_the_pin);
  RUN_TEST(test_wrong_certificate_sends_nothing);
  RUN_TEST(test_unreadable_certificate_is_a_wrong_certificate);
  RUN_TEST(test_the_right_certificate_clears_the_warning);
  RUN_TEST(test_uppercase_stored_fingerprint_matches);
  RUN_TEST(test_handshake_failure_is_a_failed_connection_not_a_wrong_certificate);
  RUN_TEST(test_every_wait_has_its_bound);
  RUN_TEST(test_a_lookup_that_never_answers_ends_at_2_s);
  RUN_TEST(test_a_failed_lookup_is_a_failed_connection);
  RUN_TEST(test_a_stalled_write_ends_the_link_after_its_bound);
  RUN_TEST(test_a_stalled_hello_write_never_marks_it_sent);
  RUN_TEST(test_k_every_500_ms_when_idle);
  RUN_TEST(test_a_panel_line_counts_as_the_keepalive_and_goes_unchanged);
  RUN_TEST(test_queued_lines_keep_their_order_and_boundaries);
  RUN_TEST(test_five_seconds_of_silence_end_the_link);
  RUN_TEST(test_any_line_from_the_station_keeps_it_alive);
  RUN_TEST(test_the_stations_bytes_reach_the_loop_in_order);
  RUN_TEST(test_refused_waits);
  RUN_TEST(test_refused_split_across_reads);
  RUN_TEST(test_other_refusals_are_failed_connections);
  RUN_TEST(test_a_first_line_that_only_starts_like_refused_is_data);
  RUN_TEST(test_no_answer_to_the_hello_is_silence);
  RUN_TEST(test_stale_lines_are_dropped_when_a_new_link_opens);
  RUN_TEST(test_each_link_up_is_counted);
  RUN_TEST(test_usb_wins_and_wifi_resumes);
  RUN_TEST(test_usb_active_from_the_start_never_dials);
  RUN_TEST(test_unconfigured_panel_never_uses_the_radio);
  RUN_TEST(test_clearing_the_settings_stops_the_radio_and_the_link);
  RUN_TEST(test_new_settings_restart_the_join);
  RUN_TEST(test_wifi_join_stalls_then_is_retried);
  RUN_TEST(test_losing_the_network_while_up_goes_back_to_joining);
  RUN_TEST(test_ring_is_all_or_nothing_and_wraps);
  RUN_TEST(test_no_service_screen);
  return UNITY_END();
}
