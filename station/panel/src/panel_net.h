// The panel's Wi-Fi side (firmware 14): the Arduino implementation of panel::LinkIo (Wi-Fi station, a TLS client with
// the certificate pinned by its fingerprint, a name lookup that can be given up on), the stored settings in NVS, and
// the task that steps the link state machine (lib/panel_core/src/panel_link.h). Nothing in here is called until
// settings are stored: the radio stays off on a panel that is set up for USB only.
//
// Why a task and not loop(): a TLS handshake is some 0.3 s of CPU on a C3 and up to 3 s of waiting on a bad network, and
// loop() also runs the buttons, the lamps, the NFC reader and the display. The task owns every Wi-Fi and TLS object;
// loop() talks to it only through two byte rings (panel::ByteRing) and never waits for it.
#pragma once
#include <Arduino.h>
#include <WiFi.h>
#include <NetworkClientSecure.h>
#include <Preferences.h>
#include <memory>
#include "panel_link.h"
#include "panel_store.h"

namespace panelnet {

// ---------- stored settings (NVS namespace "panel"; the program-only update leaves it alone) ----------
// The layout, and why a failed save cannot leave a mix, is in lib/panel_core/src/panel_store.h; this is NVS under it.
struct NvsStore : panel::KvStore {
  Preferences p;
  bool open = false;
  explicit NvsStore(bool readOnly) { open = p.begin("panel", readOnly); }  // read-only fails on a panel that never stored anything
  ~NvsStore() { if (open) p.end(); }
  bool put(const char *key, const uint8_t *data, size_t len) override { return open && p.putBytes(key, data, len) == len; }
  size_t get(const char *key, uint8_t *buf, size_t cap) override { return open && p.isKey(key) ? p.getBytes(key, buf, cap) : 0; }
  bool has(const char *key) override { return open && p.isKey(key); }
  bool remove(const char *key) override { return open && p.remove(key); }
};

static bool loadSettings(panel::Settings &s) {
  NvsStore kv(true);
  s.clear();
  if (!kv.open) return false;
  panel::loadSettings(kv, s);
  return true;
}

// True only when every value was stored (the panel answers OK after this, not before); a false leaves the old set or
// nothing. A clear is a store of an empty set.
static bool storeSettings(const panel::Settings &s) {
  NvsStore kv(false);
  return kv.open && panel::storeSettings(kv, s);
}

// ---------- the world, as panel::LinkIo ----------
// The TLS client with a writable socket timeout: NetworkClientSecure uses the connect timeout for writes too, and a
// write is bounded by 1 s, the connect by 2 s.
struct Tls : NetworkClientSecure {
  void writeBoundMs(unsigned long ms) { sslclient->socket_timeout = ms; }
};

// A name lookup that can be abandoned: lwIP's resolver has no timeout of its own, so it runs in a short task and the
// state machine stops waiting for it after 2 s. At most one such task runs at a time.
struct Lookup {
  volatile uint32_t gen = 0;
  volatile int state = 0;        // 0 pending, 1 done, 2 failed
  volatile bool running = false;
  char host[panel::MAX_ADDR + 1] = "";
  IPAddress ip;
  static void run(void *arg) {
    Lookup *l = (Lookup *)arg;
    uint32_t mine = l->gen;
    IPAddress r;
    bool ok = Network.hostByName(l->host, r) == 1;
    if (l->gen == mine) { l->ip = r; l->state = ok ? 1 : 2; }  // an abandoned lookup (gen moved on) is dropped
    l->running = false;
    vTaskDelete(nullptr);
  }
};

class ArduinoIo : public panel::LinkIo {
 public:
  uint32_t now() override { return millis(); }

  void wifiBegin(const panel::Settings &s) override {
    char ssid[panel::MAX_SSID + 1], pass[panel::MAX_PASS + 1];
    memcpy(ssid, s.ssid, s.ssidLen); ssid[s.ssidLen] = 0;
    memcpy(pass, s.pass, s.passLen); pass[s.passLen] = 0;
    WiFi.persistent(false);       // the password lives in the panel's own namespace only
    WiFi.mode(WIFI_STA);
    WiFi.setAutoReconnect(true);
    WiFi.setSleep(false);         // mains powered: modem sleep would hold frames back by a DTIM interval (speech)
    WiFi.begin(ssid, s.passLen ? pass : nullptr);
    memset(pass, 0, sizeof pass);
  }
  void wifiStop() override {
    close();
    WiFi.disconnect(true, false);
    WiFi.mode(WIFI_OFF);
  }
  bool wifiConnected() override { return WiFi.status() == WL_CONNECTED; }

  void lookupStart(const char *host) override {
    lookup_.gen = lookup_.gen + 1;
    if (lookup_.ip.fromString(host)) { lookup_.state = 1; return; }  // an address needs no lookup
    lookup_.state = 0;
    if (lookup_.running) { lookup_.state = 2; return; }              // the last one is still stuck: fail, back off
    strncpy(lookup_.host, host, sizeof lookup_.host - 1);
    lookup_.running = true;
    if (xTaskCreate(Lookup::run, "dns", 4096, &lookup_, uxTaskPriorityGet(nullptr), nullptr) != pdPASS) {
      lookup_.running = false; lookup_.state = 2;
    }
  }
  panel::LookupState lookupPoll() override {
    return lookup_.state == 1 ? panel::LookupState::Done : lookup_.state == 2 ? panel::LookupState::Failed
                                                                                : panel::LookupState::Pending;
  }

  bool tcpConnect(uint16_t port, uint32_t timeoutMs) override {
    close();
    tls_.reset(new Tls());
    tls_->setInsecure();       // no chain check: the station's certificate is self-signed; the pin (below) is the check
    tls_->setPlainStart();     // connect now, handshake in tlsHandshake() so each has its own bound
    return tls_->connect(lookup_.ip, port, (int32_t)timeoutMs) == 1;
  }
  bool tlsHandshake(uint32_t timeoutMs) override {
    if (!tls_) return false;
    tls_->setHandshakeTimeout((timeoutMs + 999) / 1000);  // seconds
    if (!tls_->startTLS()) return false;
    tls_->writeBoundMs(panel::WRITE_MS);
    return true;
  }
  bool peerFingerprint(uint8_t sha256[32]) override { return tls_ && tls_->getFingerprintSHA256(sha256); }
  bool write(const uint8_t *p, size_t n, uint32_t timeoutMs) override {
    if (!tls_) return false;
    tls_->writeBoundMs(timeoutMs);
    return tls_->write(p, n) == n;
  }
  int read(uint8_t *p, size_t n) override {
    if (!tls_) return -1;
    int a = tls_->available();       // does not wait
    if (a > 0) {
      int got = tls_->read(p, n < (size_t)a ? n : (size_t)a);
      return got > 0 ? got : (tls_->connected() ? 0 : -1);
    }
    return tls_->connected() ? 0 : -1;
  }
  void close() override { tls_.reset(); }

 private:
  std::unique_ptr<Tls> tls_;
  Lookup lookup_;
};

static ArduinoIo io;

static void linkTask(void *arg) {
  panel::Link *link = (panel::Link *)arg;
  for (;;) {
    link->step(io);
    vTaskDelay(1);  // one tick (10 ms): the loop's lines go out and the station's lines come in at that pace
  }
}

// Starts the task once. Priority of loop() (one below the speech task): a handshake never preempts the speech.
static void startLinkTask(panel::Link &link) {
  static bool started = false;
  if (started) return;
  started = true;
  xTaskCreate(linkTask, "link", 12288, &link, uxTaskPriorityGet(nullptr), nullptr);
}

}  // namespace panelnet
