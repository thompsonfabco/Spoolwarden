// Wi-Fi set-up of the intake panel (firmware 14): the line reader, the three set-up lines the station writes over USB
// (CFG WIFI, CFG LINK, CFG CLEAR), the settings they carry and the first line the panel says to the station service.
// No Arduino headers, so it runs in the host tests (test/test_cfg). The station's side of the same lines is
// server/filament_kit/station/panel_setup.py.
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace panel {

static const size_t MAX_LINE = 256;     // the longest line either way (the station's own limit); never truncated
static const size_t MAX_SSID = 32;      // bytes
static const size_t MIN_PASS = 8, MAX_PASS = 63;  // bytes; none = an open network
static const size_t MAX_ADDR = 64;      // characters (an IP address or a name)
static const size_t FP_HEX = 64, TOKEN_HEX = 32;

// Collects bytes into lines of at most MAX_LINE characters. A longer line is dropped whole (to its end) and reported
// once as TooLong: nothing is ever cut short and passed on.
struct LineReader {
  enum Event : uint8_t { None, Line, TooLong };
  char buf[MAX_LINE + 1];
  size_t len = 0;
  bool overflow = false, done = false;
  // Feed one byte. Line: buf holds the NUL-terminated line (empty lines are not reported); it stays valid until the
  // next feed().
  Event feed(char c);
};

struct Settings {
  uint8_t ssid[MAX_SSID] = {}; uint8_t ssidLen = 0;   // 1-32 bytes (any byte values)
  uint8_t pass[MAX_PASS] = {}; uint8_t passLen = 0;   // 8-63 bytes, 0 = open network
  bool hasWifi = false;
  char addr[MAX_ADDR + 1] = "";                       // the station service's address
  uint16_t port = 0;
  char fp[FP_HEX + 1] = "";                           // SHA-256 of the station certificate, lower-case hex
  char token[TOKEN_HEX + 1] = "";
  bool hasLink = false;
  bool configured() const { return hasWifi && hasLink; }
  void clear() { *this = Settings(); }
};

// What the panel makes of a line that begins with CFG.
struct CfgResult {
  bool isCfg = false;       // the line is a CFG line at all (else it is an ordinary command)
  bool ok = false;          // understood in full: `out` changed and is to be stored before the panel says OK
  const char *err = "";     // the reason word when !ok: long hex ssid password address port fingerprint token cfg
                            // (the caller adds "store" when the flash write fails)
};

// Parses a line. On ok the settings take the new values; otherwise `out` is untouched. CFG CLEAR leaves `out` cleared.
CfgResult cfgParse(const char *line, Settings &out);

// "OK" or "ERR <reason>" (without its newline) into out, capacity n. Returns the length.
size_t cfgReply(bool ok, const char *err, char *out, size_t n);

// `hex` is 64 hex digits (either case), `sha` the 32 bytes the station's certificate hashes to.
bool fingerprintMatches(const char *hex, const uint8_t sha[32]);

// "HELLO intake-panel <version> esp32c3", with " <token>" appended when token is not null (the line that opens the
// link to the station service). Returns the length.
size_t helloLine(char *out, size_t n, const char *version, const char *token);

// The reason word of a "REFUSED <reason>" line from the station service, or null when the line is not one. It points
// into the line.
const char *refusedReason(const char *line);

}  // namespace panel
