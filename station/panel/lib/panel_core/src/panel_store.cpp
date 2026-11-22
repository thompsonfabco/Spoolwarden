#include "panel_store.h"
#include <stdio.h>
#include <string.h>

namespace panel {

static const char *MARKER = "act";                 // 'a' or 'b': the live slot; anything else or absent: none
static const char *FIELDS[] = {"ssid", "pass", "addr", "port", "fp", "token"};
static const size_t NFIELDS = sizeof FIELDS / sizeof FIELDS[0];

static void keyOf(char slot, const char *field, char *out, size_t n) { snprintf(out, n, "%c_%s", slot, field); }

static char liveSlot(KvStore &kv) {
  uint8_t m = 0;
  if (kv.get(MARKER, &m, 1) != 1) return 0;
  return (m == 'a' || m == 'b') ? (char)m : 0;
}

bool loadSettings(KvStore &kv, Settings &s) {
  s.clear();
  char slot = liveSlot(kv);
  if (!slot) return false;
  char k[16];
  keyOf(slot, "ssid", k, sizeof k);
  s.ssidLen = (uint8_t)kv.get(k, s.ssid, sizeof s.ssid);
  s.hasWifi = s.ssidLen > 0;
  keyOf(slot, "pass", k, sizeof k);
  s.passLen = (uint8_t)kv.get(k, s.pass, sizeof s.pass);
  uint8_t addr[MAX_ADDR + 1] = {}, fp[FP_HEX + 1] = {}, token[TOKEN_HEX + 1] = {}, port[2] = {};
  keyOf(slot, "addr", k, sizeof k);  size_t al = kv.get(k, addr, MAX_ADDR);
  keyOf(slot, "fp", k, sizeof k);    size_t fl = kv.get(k, fp, FP_HEX);
  keyOf(slot, "token", k, sizeof k); size_t tl = kv.get(k, token, TOKEN_HEX);
  keyOf(slot, "port", k, sizeof k);  size_t pl = kv.get(k, port, 2);
  s.port = pl == 2 ? (uint16_t)(port[0] | (port[1] << 8)) : 0;
  s.hasLink = al > 0 && fl == FP_HEX && tl == TOKEN_HEX && s.port != 0;
  if (s.hasLink) {
    memcpy(s.addr, addr, al); s.addr[al] = 0;
    memcpy(s.fp, fp, fl); s.fp[fl] = 0;
    memcpy(s.token, token, tl); s.token[tl] = 0;
  } else {
    s.port = 0;
  }
  return true;
}

// Erases every key of one slot; false if any erase failed (the others are still tried).
static bool eraseSlot(KvStore &kv, char slot) {
  bool ok = true;
  char k[16];
  for (size_t i = 0; i < NFIELDS; i++) {
    keyOf(slot, FIELDS[i], k, sizeof k);
    if (kv.has(k) && !kv.remove(k)) ok = false;
  }
  return ok;
}

// Sets one field of a slot: written when it has a value, erased when it has none.
static bool setField(KvStore &kv, char slot, const char *field, const uint8_t *v, size_t len) {
  char k[16];
  keyOf(slot, field, k, sizeof k);
  if (len) return kv.put(k, v, len);
  return !kv.has(k) || kv.remove(k);
}

bool storeSettings(KvStore &kv, const Settings &s) {
  if (!s.hasWifi && !s.hasLink) {  // a clear: marker first, so that a failure part way leaves nothing usable
    uint8_t none = 'n';
    bool wasSet = liveSlot(kv) != 0 || kv.has(MARKER);
    if (wasSet && !kv.put(MARKER, &none, 1)) return false;  // nothing changed: still the old set
    bool ok = eraseSlot(kv, 'a');
    ok = eraseSlot(kv, 'b') && ok;
    return ok;
  }
  char old = liveSlot(kv);
  char slot = old == 'a' ? 'b' : 'a';
  uint8_t port[2] = {(uint8_t)(s.port & 0xff), (uint8_t)(s.port >> 8)};
  bool ok = true;
  ok = ok && setField(kv, slot, "ssid", s.hasWifi ? s.ssid : nullptr, s.hasWifi ? s.ssidLen : 0);
  ok = ok && setField(kv, slot, "pass", s.hasWifi ? s.pass : nullptr, s.hasWifi ? s.passLen : 0);
  ok = ok && setField(kv, slot, "addr", (const uint8_t *)s.addr, s.hasLink ? strlen(s.addr) : 0);
  ok = ok && setField(kv, slot, "port", port, s.hasLink ? 2 : 0);
  ok = ok && setField(kv, slot, "fp", (const uint8_t *)s.fp, s.hasLink ? strlen(s.fp) : 0);
  ok = ok && setField(kv, slot, "token", (const uint8_t *)s.token, s.hasLink ? strlen(s.token) : 0);
  if (!ok) return false;  // the live slot was not touched
  uint8_t m = (uint8_t)slot;
  if (!kv.put(MARKER, &m, 1)) return false;  // the flip is the last write: failing it leaves the old set live
  if (old) eraseSlot(kv, old);               // the old password is not kept; a failure here costs nothing but flash
  return true;
}

}  // namespace panel
