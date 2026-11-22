// The reader's settings, written into the device over USB at setup time (not compiled in), and the line protocol
// that carries them. Pure logic, host-tested (pio test -e native).
//
// The kit (`filament-kit reader setup`, or the setup page in a browser) talks to the reader over its USB serial
// port, one line each way at 115200 baud:
//   kit:    FK?                  reader: FK-READER <build> configured=<0|1> wifi=<ok|fail|-> server=<ok|fail|unauth|->
//   kit:    FK-SET <json>        reader: FK-OK (then it restarts with the new settings) or FK-ERR <reason>
// <json> is one line: {"ssid", "pass", "base", "token"[, "ota_key"][, "root_ca"][, "buzzer_low"][, "hub_ip"][, "printer"]}.
// The reader never sends a setting back. wifi/server say how the last start went ("-": not tried yet). The reader says
// wifi as soon as it knows it, before it calls the Hub (a second FK-READER line, unasked), then server when the Hub
// answered or the 5 s check ran out.
#pragma once

#include <cstddef>
#include <string>

namespace reader {

static const size_t MAX_LINE = 6144;       // an FK-SET line with a root CA and an OTA key fits
static const size_t MAX_PEM = 3000;
static const size_t MAX_PRINTER_ID = 64;

struct Settings {
  std::string ssid;      // Wi-Fi name, 1-32 bytes
  std::string pass;      // Wi-Fi password, at most 63 bytes (empty: an open network)
  std::string base;      // the kit's reader API, https://.../reader (no trailing slash)
  std::string token;     // the reader token (X-Auth-Token)
  std::string otaKey;    // PEM public key OTA images must be signed with; empty: every update is refused
  std::string rootCa;    // PEM root CA of the server; empty: ISRG Root X1 (Let's Encrypt)
  bool buzzerLow = false;  // a buzzer with a driver board (low-triggered); false: a bare buzzer (high-triggered)
  std::string hubIp;     // the Hub's home IPv4 address when the reader was set up; used only when base's ".local" name does not resolve
  std::string printer;   // the printer id the reader starts on; empty: the knob chooses
};

// The JSON of an FK-SET line into `out`. False with a short reason (sent back as FK-ERR) when a setting is missing,
// too long or the wrong shape; `out` is then unchanged.
bool parseSettings(const char* json, Settings& out, std::string& why);

enum Command { CMD_NONE, CMD_HELLO, CMD_SET, CMD_OTHER };

// One received line: FK? / FK-SET <json> / anything else. `arg` is the JSON of an FK-SET.
Command command(const std::string& line, std::string& arg);

// Collects serial bytes into lines (\n ends one, \r is dropped). A line longer than MAX_LINE is thrown away whole.
class LineReader {
 public:
  // True when `c` completed a line, which is then in `line`.
  bool push(char c, std::string& line);
 private:
  std::string buf_;
  bool overflow_ = false;
};

// A build with a secrets.h copies its compiled settings into flash, with a stamp of them (FNV-1a 64, hex) beside
// them. Settings written over USB later keep the stamp, so they win over the same build; a build whose compiled
// settings differ (a new token, Wi-Fi password, server or OTA key) has another stamp and is copied again.
std::string settingsStamp(const Settings& s);
// Whether a secrets build's compiled settings replace those in flash: flash holds none yet, or the stamp saved with
// the last copy is not this build's.
bool adoptCompiled(const Settings& flash, const std::string& savedStamp, const std::string& compiledStamp);

// Which printer the reader starts on: the one it was set up with when that is new (the settings were just written),
// otherwise the one remembered from the knob. The knob can change it afterwards.
std::string startPrinter(const std::string& remembered, const std::string& provisioned, bool provisionedIsNew);

// What the reader reports about its last start. Wi-Fi is known before the Hub is called, so wifi is set first and the
// server stays "-" until the Hub has answered (or not) within the bound.
static const int HTTP_TOO_BIG = -100;   // our code, not a status: a reply refused for its size
static const unsigned HUB_CHECK_MS = 5000;   // the Hub check at start: connect and read, each
class BootReport {
 public:
  void wifi(bool associated) { wifi_ = associated ? "ok" : "fail"; }
  // `httpCode` of the first Hub call (<= 0: no HTTP answer came). 401/403 is unauth. Any other status means the Hub was
  // reached (ok), also 409 (a feature off) or 500: fail is only for "no answer". HTTP_TOO_BIG (a reply refused for its
  // size) is an answer too.
  void server(int httpCode);
  const char* wifiText() const { return wifi_; }
  const char* serverText() const { return server_; }
 private:
  const char* wifi_ = "-";
  const char* server_ = "-";
};

// The FK-READER answer.
std::string helloLine(long build, bool configured, const char* wifi, const char* server);

}  // namespace reader
