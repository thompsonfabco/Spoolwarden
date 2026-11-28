// The panel's Wi-Fi link to the station service as a state machine: join the
// network, dial the station's TLS port, check the certificate's fingerprint BEFORE anything is sent, say
// "HELLO intake-panel <version> esp32c3 <token>", then carry the panel's lines both ways and send "K" when idle.
// No Arduino headers: every wait goes through LinkIo with its bound passed in, so the host tests (test/test_link)
// can check that no step ever asks for more than its bound, and what is (not) sent on a wrong certificate.
//
// The machine is stepped by one task (src/panel_net.h); it never blocks longer than one LinkIo call, and each call is
// given the bound below. The panel's loop() talks to it only through two byte rings.
#pragma once
#include <atomic>
#include <stddef.h>
#include <stdint.h>
#include "panel_cfg.h"

namespace panel {

// ---- the timings (ms) ----
static const uint32_t LOOKUP_MS = 2000;       // a name lookup
static const uint32_t CONNECT_MS = 2000;      // the TCP connect
static const uint32_t HANDSHAKE_MS = 3000;    // the TLS handshake (the station allows 5 s for the first line)
static const uint32_t WRITE_MS = 1000;        // one write
static const uint32_t HELLO_REPLY_MS = 5000;  // after the HELLO, the station's first bytes
static const uint32_t KEEPALIVE_MS = 500;     // K when nothing else was sent for this long
static const uint32_t SILENCE_MS = 5000;      // nothing heard from the station for this long: the link is over
static const uint32_t REFUSED_LONG_MS = 30000;  // after REFUSED usb or REFUSED token
static const uint32_t REFUSED_BUSY_MS = 2000;   // after REFUSED busy
static const uint32_t BACKOFF_MAX_MS = 15000;
static const uint32_t WIFI_STALL_MS = 10000;  // joining for this long without success: "no network"
static const uint32_t WIFI_RETRY_MS = 20000;  // begin the join again this often while it has not succeeded

// After the nth failed connection in a row (n >= 1): 1, 2, 4, 8, then 15 s.
uint32_t backoffMs(uint32_t failures);

// A single-producer single-consumer byte ring (the producer and the consumer are different tasks; no lock).
template <size_t N>
struct ByteRing {
  volatile size_t head = 0, tail = 0;  // head: written by the producer, tail: by the consumer
  uint8_t data[N];
  size_t used() const { size_t h = head, t = tail; return h >= t ? h - t : N - t + h; }
  size_t room() const { return N - 1 - used(); }
  // All or nothing: false (nothing written) when n bytes do not fit.
  bool push(const uint8_t *p, size_t n) {
    if (n > room()) return false;
    size_t h = head;
    for (size_t i = 0; i < n; i++) { data[h] = p[i]; h = (h + 1) % N; }
    head = h;
    return true;
  }
  // Copies up to n bytes out without removing them; returns the count.
  size_t peek(uint8_t *p, size_t n) const {
    size_t t = tail, u = used();
    if (n > u) n = u;
    for (size_t i = 0; i < n; i++) { p[i] = data[t]; t = (t + 1) % N; }
    return n;
  }
  void drop(size_t n) { tail = (tail + n) % N; }
  size_t pop(uint8_t *p, size_t n) { n = peek(p, n); drop(n); return n; }
  void clear() { tail = head; }  // consumer side
};

enum class LookupState : uint8_t { Pending, Done, Failed };

// What the machine needs from the world. Every call that can wait takes its bound; none may take longer.
struct LinkIo {
  virtual ~LinkIo() {}
  virtual uint32_t now() = 0;                                   // ms, wraps at 2^32
  virtual void wifiBegin(const Settings &s) = 0;                // start (or restart) joining; does not wait
  virtual void wifiStop() = 0;                                  // radio off
  virtual bool wifiConnected() = 0;
  virtual void lookupStart(const char *host) = 0;               // does not wait
  virtual LookupState lookupPoll() = 0;                         // does not wait
  virtual bool tcpConnect(uint16_t port, uint32_t timeoutMs) = 0;     // to the looked-up address
  virtual bool tlsHandshake(uint32_t timeoutMs) = 0;
  virtual bool peerFingerprint(uint8_t sha256[32]) = 0;         // of the station's certificate
  virtual bool write(const uint8_t *p, size_t n, uint32_t timeoutMs) = 0;  // all n bytes or false
  virtual int read(uint8_t *p, size_t n) = 0;                   // does not wait: bytes, 0 none, -1 closed
  virtual void close() = 0;
};

enum class Phase : uint8_t {
  Off,         // no Wi-Fi settings (or cleared): the radio is not used
  UsbWins,     // a station is talking to the panel over USB: no Wi-Fi link
  Joining,     // joining the Wi-Fi network
  Waiting,     // between two attempts (a backoff or a refusal's wait)
  Resolving, Connecting, Handshaking,
  Hello,       // HELLO sent, the station's answer not yet seen
  Up,
};

// Why the last attempt ended (for the display and the tests).
enum class Why : uint8_t {
  None, NoRoute, NoConnect, NoHandshake, WrongCert, WriteFailed, Silent, Closed,
  RefusedUsb, RefusedToken, RefusedBusy, RefusedOther,
};

class Link {
 public:
  Link(ByteRing<4096> &rx, ByteRing<1024> &tx, const char *version) : rx_(rx), tx_(tx), version_(version) {}

  // From the main loop's side (a different task): the latest settings and whether USB is carrying the panel.
  void configure(const Settings &s) {
    cfgGen_ = cfgGen_ + 1;
    std::atomic_signal_fence(std::memory_order_acq_rel);
    pending_ = s;
    std::atomic_signal_fence(std::memory_order_acq_rel);
    cfgGen_ = cfgGen_ + 1;
  }  // a seqlock: odd while writing
  void setUsbActive(bool on) { usb_ = on; }

  // One pass: at most one LinkIo call that waits (each with its bound), plus the non-waiting ones.
  void step(LinkIo &io);

  Phase phase() const { return phase_; }
  Why why() const { return why_; }
  bool up() const { return phase_ == Phase::Up; }
  uint32_t upCount() const { return upCount_; }       // how many times the link has come up
  bool wifiJoined() const { return joined_; }
  bool wifiStalled() const { return stalled_; }       // joining for WIFI_STALL_MS or more without success
  bool wrongCert() const { return certBad_; }         // the last connection ended on a certificate that is not the pinned one
  bool configured() const { return cfg_.configured(); }
  const Settings &settings() const { return cfg_; }
  uint32_t failures() const { return failures_; }
  uint32_t waitUntil() const { return waitUntil_; }

 private:
  void fail(LinkIo &io, Why why, uint32_t waitMs, bool count);
  void goPhase(Phase p, uint32_t now) { phase_ = p; phaseAt_ = now; }
  bool sendHello(LinkIo &io);
  void helloStep(LinkIo &io);
  void upStep(LinkIo &io);

  ByteRing<4096> &rx_;
  ByteRing<1024> &tx_;
  const char *version_;
  Settings cfg_, pending_;
  volatile uint32_t cfgGen_ = 0;
  uint32_t seenGen_ = 0;
  volatile bool usb_ = false;
  Phase phase_ = Phase::Off;
  Why why_ = Why::None;
  uint32_t phaseAt_ = 0, waitUntil_ = 0, failures_ = 0, upCount_ = 0;
  uint32_t lastTx_ = 0, lastRx_ = 0, joinAt_ = 0, beginAt_ = 0;
  bool joined_ = false, stalled_ = false, certBad_ = false, radioOn_ = false;
  uint8_t first_[40];       // the station's first bytes, until we know whether they are a REFUSED line
  size_t firstLen_ = 0;
  bool helloSent_ = false;
  uint32_t helloAt_ = 0;
};

// ---- what the OLED says while there is no station (the panel's lamps are dark) ----
// Rows as Display holds them: 0 the title (the big font), 1-4 the body (21 characters at most).
struct Screen { char rows[5][22]; };
struct NetView {
  bool configured = false;   // Wi-Fi settings are stored
  Phase phase = Phase::Off;
  Why why = Why::None;
  bool wifiJoined = false, wifiStalled = false, wrongCert = false;
  const Settings *settings = nullptr;  // for the network's name
};
// True and fills `out` when the Wi-Fi link has something to say instead of the USB texts ("Station offline",
// "Waiting for PC"); false when the panel is not set up for Wi-Fi or USB carries it.
bool netScreen(const NetView &v, Screen &out);

}  // namespace panel
