// The stored Wi-Fi set-up of the intake panel (firmware 14), written all or none. No Arduino headers, so the host tests
// (test/test_store) run it against a fake store that fails on demand; src/panel_net.h backs KvStore with NVS.
//
// Two slots of keys ("a_ssid"... and "b_ssid"...) and a marker key "act" naming the live one. A save writes the whole new
// set into the slot that is NOT live and flips the marker last (one NVS write, atomic), so a save that fails anywhere
// leaves the live slot untouched: the stored set is entirely the old one, never a mix. A clear flips the marker to "none"
// first and then erases both slots: after a failed clear the stored set is empty (or, if the marker write itself failed,
// still the old one), and the caller is told.
#pragma once
#include <stddef.h>
#include <stdint.h>
#include "panel_cfg.h"

namespace panel {

// What the flash offers: bytes under a key. Each call is one write or erase that either happens or does not.
struct KvStore {
  virtual ~KvStore() {}
  virtual bool put(const char *key, const uint8_t *data, size_t len) = 0;  // true when all len bytes were written
  virtual size_t get(const char *key, uint8_t *buf, size_t cap) = 0;       // bytes read, 0 when absent
  virtual bool has(const char *key) = 0;
  virtual bool remove(const char *key) = 0;                                // true when the key is gone afterwards
};

// Reads the live set. False only when the marker names no slot (nothing stored); `s` is then cleared.
bool loadSettings(KvStore &kv, Settings &s);

// True only when the whole of `s` is stored (the panel answers OK after this, not before). A set with neither a network
// nor a station is a clear. On false the store holds the old set or nothing, see the top of this file.
bool storeSettings(KvStore &kv, const Settings &s);

}  // namespace panel
