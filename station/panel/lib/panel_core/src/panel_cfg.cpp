#include "panel_cfg.h"
#include <stdio.h>
#include <string.h>

namespace panel {

LineReader::Event LineReader::feed(char c) {
  if (done) { len = 0; done = false; }
  if (c == '\n' || c == '\r') {
    Event e = None;
    if (overflow) e = TooLong;
    else if (len) { buf[len] = 0; e = Line; done = true; }
    if (e != Line) len = 0;
    overflow = false;
    return e;
  }
  if (overflow) return None;
  if (len >= MAX_LINE) { overflow = true; len = 0; return None; }  // the 257th character: drop to the end of the line
  buf[len++] = c;
  return None;
}

static int nibble(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}

static bool allHex(const char *s, size_t n) {
  for (size_t i = 0; i < n; i++) if (nibble(s[i]) < 0) return false;
  return true;
}

// Decodes a hex token into out (capacity cap bytes; nothing is written past it). False when its length is odd or a
// character is not hex; *got is the byte count, which may exceed cap.
static bool decodeHex(const char *hex, uint8_t *out, size_t cap, size_t *got) {
  size_t n = strlen(hex);
  if (n % 2 || !allHex(hex, n)) return false;
  for (size_t i = 0; i < n / 2; i++)
    if (i < cap) out[i] = (uint8_t)(nibble(hex[2 * i]) << 4 | nibble(hex[2 * i + 1]));
  *got = n / 2;
  return true;
}

// Splits the line (in place) on runs of spaces. Returns the token count, capped at max + 1 (more than max tokens).
static size_t split(char *line, const char **tok, size_t max) {
  size_t n = 0;
  char *p = line;
  while (*p) {
    while (*p == ' ') *p++ = 0;
    if (!*p) break;
    if (n < max) tok[n] = p;
    n++;
    while (*p && *p != ' ') p++;
  }
  return n > max ? max + 1 : n;
}

static CfgResult fail(const char *why) { CfgResult r; r.isCfg = true; r.err = why; return r; }

static char lower(char c) { return (c >= 'A' && c <= 'F') ? (char)(c + 32) : c; }

CfgResult cfgParse(const char *line, Settings &out) {
  if (strncmp(line, "CFG", 3) != 0 || (line[3] != ' ' && line[3] != 0)) return CfgResult();
  if (strlen(line) > MAX_LINE) return fail("long");
  char copy[MAX_LINE + 1];
  strcpy(copy, line);
  const char *t[8] = {};
  size_t n = split(copy, t, 7);  // CFG LINK + four fields is 6 tokens; 8 are never needed
  if (n < 2) return fail("cfg");
  CfgResult r; r.isCfg = true;

  if (strcmp(t[1], "CLEAR") == 0) {
    if (n != 2) return fail("cfg");
    out.clear(); r.ok = true;
    return r;
  }
  if (strcmp(t[1], "WIFI") == 0) {  // CFG WIFI <ssid hex> <password hex, or - for an open network>
    Settings s = out;
    size_t got = 0;
    if (n < 3) return fail("ssid");
    if (!decodeHex(t[2], s.ssid, sizeof s.ssid, &got)) return fail("hex");
    if (got < 1 || got > MAX_SSID) return fail("ssid");
    s.ssidLen = (uint8_t)got;
    if (n < 4) return fail("password");
    if (strcmp(t[3], "-") == 0) {
      s.passLen = 0;
    } else {
      if (!decodeHex(t[3], s.pass, sizeof s.pass, &got)) return fail("hex");
      if (got < MIN_PASS || got > MAX_PASS) return fail("password");  // a 64-digit raw key is not taken either
      s.passLen = (uint8_t)got;
    }
    if (n > 4) return fail("password");  // more fields than the line has: not understood in full
    s.hasWifi = true;
    out = s; r.ok = true;
    return r;
  }
  if (strcmp(t[1], "LINK") == 0) {  // CFG LINK <address> <port> <fingerprint 64 hex> <token 32 hex>
    Settings s = out;
    if (n < 3) return fail("address");
    size_t al = strlen(t[2]);
    if (al < 1 || al > MAX_ADDR) return fail("address");
    for (size_t i = 0; i < al; i++)
      if ((unsigned char)t[2][i] < 0x21 || (unsigned char)t[2][i] > 0x7e) return fail("address");
    if (n < 4) return fail("port");
    size_t pl = strlen(t[3]);
    if (pl < 1 || pl > 5) return fail("port");
    uint32_t port = 0;
    for (size_t i = 0; i < pl; i++) {
      if (t[3][i] < '0' || t[3][i] > '9') return fail("port");
      port = port * 10 + (uint32_t)(t[3][i] - '0');
    }
    if (port < 1 || port > 65535) return fail("port");
    if (n < 5 || strlen(t[4]) != FP_HEX || !allHex(t[4], FP_HEX)) return fail("fingerprint");
    if (n < 6 || strlen(t[5]) != TOKEN_HEX || !allHex(t[5], TOKEN_HEX)) return fail("token");
    if (n > 6) return fail("token");  // more fields than the line has
    memcpy(s.addr, t[2], al); s.addr[al] = 0;
    s.port = (uint16_t)port;
    for (size_t i = 0; i < FP_HEX; i++) s.fp[i] = lower(t[4][i]);
    s.fp[FP_HEX] = 0;
    for (size_t i = 0; i < TOKEN_HEX; i++) s.token[i] = lower(t[5][i]);
    s.token[TOKEN_HEX] = 0;
    s.hasLink = true;
    out = s; r.ok = true;
    return r;
  }
  return fail("cfg");
}

size_t cfgReply(bool ok, const char *err, char *out, size_t n) {
  int w = ok ? snprintf(out, n, "OK") : snprintf(out, n, "ERR %s", err);
  return w < 0 ? 0 : (size_t)w < n ? (size_t)w : n - 1;
}

bool fingerprintMatches(const char *hex, const uint8_t sha[32]) {
  if (strlen(hex) != FP_HEX) return false;
  uint8_t diff = 0;
  for (size_t i = 0; i < 32; i++) {
    int hi = nibble(hex[2 * i]), lo = nibble(hex[2 * i + 1]);
    if (hi < 0 || lo < 0) return false;
    diff |= (uint8_t)((hi << 4 | lo) ^ sha[i]);
  }
  return diff == 0;
}

size_t helloLine(char *out, size_t n, const char *version, const char *token) {
  int w = token ? snprintf(out, n, "HELLO intake-panel %s esp32c3 %s", version, token)
                : snprintf(out, n, "HELLO intake-panel %s esp32c3", version);
  return w < 0 ? 0 : (size_t)w < n ? (size_t)w : n - 1;
}

const char *refusedReason(const char *line) {
  static const char P[] = "REFUSED ";
  if (strncmp(line, P, sizeof P - 1) != 0) return nullptr;
  return line + sizeof P - 1;
}

}  // namespace panel
