// Host-side (PlatformIO native) tests for lib/panel_core/panel_store: a save is all or none, a clear says so when it
// failed. The fake store fails the Nth write or erase (counting from 1), so every position of a save is tried: the first
// key, the middle ones, the last key and the marker flip. Run: pio test -e native
#include <unity.h>
#include <cstdio>
#include <cstring>
#include <map>
#include <string>
#include <vector>
#include "panel_store.h"

using namespace panel;

void setUp() {}
void tearDown() {}

#define CHECK(cond, ...) do { char msg_[400]; snprintf(msg_, sizeof msg_, __VA_ARGS__); TEST_ASSERT_TRUE_MESSAGE(cond, msg_); } while (0)

struct FakeKv : KvStore {
  std::map<std::string, std::vector<uint8_t>> m;
  int ops = 0;       // writes and erases attempted so far
  int failAt = 0;    // the op number that fails (0: none); a failed op changes nothing
  bool tick() { ops++; return failAt == 0 || ops != failAt; }
  bool put(const char *k, const uint8_t *d, size_t n) override {
    if (!tick()) return false;
    m[k] = std::vector<uint8_t>(d, d + n);
    return true;
  }
  size_t get(const char *k, uint8_t *b, size_t cap) override {
    auto it = m.find(k);
    if (it == m.end()) return 0;
    size_t n = it->second.size() < cap ? it->second.size() : cap;
    memcpy(b, it->second.data(), n);
    return n;
  }
  bool has(const char *k) override { return m.count(k) != 0; }
  bool remove(const char *k) override {
    if (!tick()) return false;
    m.erase(k);
    return true;
  }
};

static Settings make(const char *ssid, const char *pass, const char *addr, uint16_t port, char fpc, char tokc) {
  Settings s;
  s.ssidLen = (uint8_t)strlen(ssid); memcpy(s.ssid, ssid, s.ssidLen); s.hasWifi = true;
  s.passLen = (uint8_t)strlen(pass); memcpy(s.pass, pass, s.passLen);
  strcpy(s.addr, addr); s.port = port;
  memset(s.fp, fpc, FP_HEX); s.fp[FP_HEX] = 0;
  memset(s.token, tokc, TOKEN_HEX); s.token[TOKEN_HEX] = 0;
  s.hasLink = true;
  return s;
}
static bool same(const Settings &a, const Settings &b) {
  return a.hasWifi == b.hasWifi && a.hasLink == b.hasLink && a.ssidLen == b.ssidLen && a.passLen == b.passLen &&
         memcmp(a.ssid, b.ssid, a.ssidLen) == 0 && memcmp(a.pass, b.pass, a.passLen) == 0 && strcmp(a.addr, b.addr) == 0 &&
         a.port == b.port && strcmp(a.fp, b.fp) == 0 && strcmp(a.token, b.token) == 0;
}
static Settings loaded(FakeKv &kv) { Settings s; loadSettings(kv, s); return s; }
static bool empty(const Settings &s) { return !s.hasWifi && !s.hasLink && s.ssidLen == 0 && s.passLen == 0; }

static const Settings OLD = make("oldnet", "oldpassword", "10.0.0.5", 8443, 'a', '1');
static const Settings NEW = make("newnet2", "newpassword1", "station.lan", 9443, 'b', '2');

void test_store_then_load_round_trips() {
  FakeKv kv;
  Settings none;
  CHECK(!loadSettings(kv, none), "nothing stored yet");
  CHECK(storeSettings(kv, OLD), "stored");
  CHECK(same(loaded(kv), OLD), "round trip");
  CHECK(storeSettings(kv, NEW), "stored again");
  CHECK(same(loaded(kv), NEW), "the second save replaced the first");
  CHECK(storeSettings(kv, OLD), "and a third, back to the first slot");
  CHECK(same(loaded(kv), OLD), "third");
}

void test_open_network_and_wifi_only_round_trip() {
  FakeKv kv;
  Settings s = make("cafe", "", "x", 1, 'c', '3');
  s.hasLink = false; s.port = 0; s.addr[0] = s.fp[0] = s.token[0] = 0;
  CHECK(storeSettings(kv, s), "stored");
  Settings g = loaded(kv);
  CHECK(g.hasWifi && g.passLen == 0 && !g.hasLink && !g.configured(), "network only, open");
  CHECK(storeSettings(kv, OLD) && same(loaded(kv), OLD), "then a full set");
  Settings u = OLD; u.passLen = 0;
  CHECK(storeSettings(kv, u) && loaded(kv).passLen == 0, "an open network after a password: the old password is gone");
}

// The number of ops a successful save takes.
static int ops_of_save(const Settings &from, const Settings &to) {
  FakeKv kv; storeSettings(kv, from); kv.ops = 0; storeSettings(kv, to); return kv.ops;
}

// A save that fails at op k (every position, the marker flip included) leaves entirely the old set.
void test_failed_save_leaves_the_old_set_entire_at_every_position() {
  int n = ops_of_save(OLD, NEW);
  CHECK(n >= 7, "a save is at least six keys and the flip (%d ops)", n);
  for (int k = 1; k <= n; k++) {
    FakeKv kv;
    CHECK(storeSettings(kv, OLD), "old stored");
    kv.ops = 0; kv.failAt = k;
    bool ok = storeSettings(kv, NEW);
    kv.failAt = 0;
    Settings g = loaded(kv);
    if (k > 7) {
      // an op after the flip (cleanup of the old slot): the save already took, and its answer is still OK
      CHECK(ok && same(g, NEW), "failure of the cleanup (op %d) must not undo the save", k);
    } else {
      CHECK(!ok, "op %d failed: the save must say so", k);
      CHECK(same(g, OLD), "op %d failed: the stored set is not entirely the old one (ssid len %d pass len %d addr %s)", k,
            g.ssidLen, g.passLen, g.addr);
    }
  }
}

void test_failed_first_save_leaves_nothing_at_every_position() {
  int n;
  { FakeKv kv; storeSettings(kv, NEW); n = kv.ops; }
  for (int k = 1; k <= n; k++) {
    FakeKv kv; kv.failAt = k;
    CHECK(!storeSettings(kv, NEW), "op %d failed: the save must say so", k);
    kv.failAt = 0;
    Settings g = loaded(kv);
    CHECK(empty(g), "op %d failed: nothing may be live", k);
  }
}

void test_failed_save_of_a_shorter_set_never_mixes() {
  Settings wifiOnly = make("justwifi", "", "", 0, 'a', '1');
  wifiOnly.hasLink = false; wifiOnly.port = 0; wifiOnly.addr[0] = wifiOnly.fp[0] = wifiOnly.token[0] = 0;
  int n = ops_of_save(OLD, wifiOnly);
  CHECK(n >= 3, "ops %d", n);
  for (int k = 1; k <= n; k++) {  // every write, the flip and the cleanup
    FakeKv kv; storeSettings(kv, OLD);
    kv.ops = 0; kv.failAt = k;
    bool ok = storeSettings(kv, wifiOnly);
    kv.failAt = 0;
    Settings g = loaded(kv);
    CHECK(ok ? (g.hasWifi && g.ssidLen == 8 && !g.hasLink) : same(g, OLD), "op %d: ok=%d, neither the new set nor entirely the old one", k, (int)ok);
  }
}

void test_clear_ok_only_when_everything_is_gone() {
  FakeKv kv;
  storeSettings(kv, OLD); storeSettings(kv, NEW);
  Settings none;
  CHECK(storeSettings(kv, none), "cleared");
  CHECK(empty(loaded(kv)), "empty after a clear");
  for (auto &e : kv.m) CHECK(e.first == "act", "no key but the marker may remain: %s", e.first.c_str());
  CHECK(storeSettings(kv, none), "clearing an empty panel is fine");
}

void test_clear_that_fails_says_so_at_every_position() {
  int n;
  { FakeKv kv; storeSettings(kv, OLD); storeSettings(kv, NEW); kv.ops = 0; storeSettings(kv, Settings()); n = kv.ops; }
  CHECK(n >= 2, "a clear is the marker and the erases (%d ops)", n);
  for (int k = 1; k <= n; k++) {
    FakeKv kv; storeSettings(kv, OLD); storeSettings(kv, NEW);
    kv.ops = 0; kv.failAt = k;
    bool ok = storeSettings(kv, Settings());
    kv.failAt = 0;
    CHECK(!ok, "op %d of the clear failed: it must not say OK", k);
    // op 1 is the marker: if it fails nothing changed (the set as before); after it the settings read as empty
    if (k == 1) CHECK(same(loaded(kv), NEW), "marker failed: the set is as before");
    else CHECK(empty(loaded(kv)), "op %d failed after the marker: the set reads as empty", k);
  }
}

void test_reply_words() {
  char r[24];
  cfgReply(false, "store", r, sizeof r);
  CHECK(std::string(r) == "ERR store", "ERR store: %s", r);
  cfgReply(true, "", r, sizeof r);
  CHECK(std::string(r) == "OK", "OK");
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_store_then_load_round_trips);
  RUN_TEST(test_open_network_and_wifi_only_round_trip);
  RUN_TEST(test_failed_save_leaves_the_old_set_entire_at_every_position);
  RUN_TEST(test_failed_first_save_leaves_nothing_at_every_position);
  RUN_TEST(test_failed_save_of_a_shorter_set_never_mixes);
  RUN_TEST(test_clear_ok_only_when_everything_is_gone);
  RUN_TEST(test_clear_that_fails_says_so_at_every_position);
  RUN_TEST(test_reply_words);
  return UNITY_END();
}
