// Host-side (PlatformIO native) tests for lib/panel_core/panel_cfg: the 256-character line reader, the three set-up
// lines (CFG WIFI, CFG LINK, CFG CLEAR) with every ERR reason and the boundary lengths, the fingerprint check and the
// HELLO line. The grammar is the station's (server/filament_kit/station/panel_setup.py: LINE_WIFI, LINE_LINK,
// LINE_CLEAR). Run: pio test -e native
#include <unity.h>
#include <cstdio>
#include <cstring>
#include <string>
#include "panel_cfg.h"

using namespace panel;

void setUp() {}
void tearDown() {}

#define CHECK(cond, ...) do { char msg_[400]; snprintf(msg_, sizeof msg_, __VA_ARGS__); TEST_ASSERT_TRUE_MESSAGE(cond, msg_); } while (0)

static std::string hex(const std::string &s) {
  static const char *d = "0123456789abcdef";
  std::string o;
  for (unsigned char c : s) { o += d[c >> 4]; o += d[c & 15]; }
  return o;
}
static const std::string FP(64, 'a'), TOKEN(32, '7');
static std::string wifi(const std::string &ssid, const std::string &pass) {
  return "CFG WIFI " + hex(ssid) + " " + (pass.empty() ? "-" : hex(pass));
}
static std::string link(const std::string &addr, const std::string &port, const std::string &fp = FP,
                        const std::string &token = TOKEN) {
  return "CFG LINK " + addr + " " + port + " " + fp + " " + token;
}
// Returns the answer line the panel would send.
static std::string answer(const std::string &line, Settings &s) {
  CfgResult r = cfgParse(line.c_str(), s);
  CHECK(r.isCfg, "not taken as a CFG line: %s", line.c_str());
  char out[40];
  cfgReply(r.ok, r.err, out, sizeof out);
  return out;
}

// ---------- the line reader ----------
static LineReader::Event feedAll(LineReader &r, const std::string &s, std::string *line = nullptr) {
  LineReader::Event last = LineReader::None;
  for (char c : s) {
    LineReader::Event e = r.feed(c);
    if (e != LineReader::None) { last = e; if (e == LineReader::Line && line) *line = r.buf; }
  }
  return last;
}

void test_reader_lines_and_boundaries() {
  for (size_t n : {1u, 100u, 255u, 256u}) {
    LineReader r; std::string got;
    CHECK(feedAll(r, std::string(n, 'x') + "\n", &got) == LineReader::Line, "%u chars should be a line", (unsigned)n);
    CHECK(got == std::string(n, 'x'), "%u chars changed", (unsigned)n);
  }
  for (size_t n : {257u, 258u, 1000u}) {
    LineReader r;
    CHECK(feedAll(r, std::string(n, 'x') + "\n") == LineReader::TooLong, "%u chars should be too long", (unsigned)n);
  }
}

void test_reader_long_line_is_dropped_whole_and_reported_once() {
  LineReader r;
  int tooLong = 0, lines = 0;
  std::string stream = std::string(600, 'z') + "\nL 1 2\n";
  std::string last;
  for (char c : stream) {
    auto e = r.feed(c);
    if (e == LineReader::TooLong) tooLong++;
    if (e == LineReader::Line) { lines++; last = r.buf; }
  }
  CHECK(tooLong == 1 && lines == 1 && last == "L 1 2", "tooLong %d lines %d last '%s'", tooLong, lines, last.c_str());
}

void test_reader_crlf_and_empty_lines() {
  LineReader r;
  int lines = 0;
  for (char c : std::string("?\r\n\r\n\nL 0 0\r\n")) if (r.feed(c) == LineReader::Line) lines++;
  CHECK(lines == 2, "%d lines", lines);
}

void test_reader_a_line_after_a_long_one_is_not_polluted() {
  LineReader r; std::string got;
  feedAll(r, std::string(300, 'q') + "\r\n");
  CHECK(feedAll(r, "?\n", &got) == LineReader::Line && got == "?", "got '%s'", got.c_str());
}

// ---------- CFG WIFI ----------
void test_wifi_valid_and_stored() {
  Settings s;
  CHECK(answer(wifi("Home Net", "hunter2hunter2"), s) == "OK", "wifi");
  CHECK(s.hasWifi && s.ssidLen == 8 && memcmp(s.ssid, "Home Net", 8) == 0, "ssid stored");
  CHECK(s.passLen == 14 && memcmp(s.pass, "hunter2hunter2", 14) == 0, "password stored");
  CHECK(!s.hasLink && !s.configured(), "wifi alone is not configured");
}

void test_wifi_open_network() {
  Settings s;
  s.passLen = 9;
  CHECK(answer(wifi("Cafe", ""), s) == "OK" && s.passLen == 0, "open network");
}

void test_wifi_ssid_bounds() {
  Settings s;
  CHECK(answer(wifi(std::string(1, 'a'), "password1"), s) == "OK", "1 byte");
  CHECK(answer(wifi(std::string(32, 'a'), "password1"), s) == "OK" && s.ssidLen == 32, "32 bytes");
  CHECK(answer(wifi(std::string(33, 'a'), "password1"), s) == "ERR ssid", "33 bytes");
}

void test_wifi_name_may_hold_any_bytes() {
  Settings s;
  std::string odd("a\0b\xff z", 6);
  CHECK(answer(wifi(odd, "password1"), s) == "OK" && s.ssidLen == 6 && s.ssid[1] == 0 && s.ssid[3] == 0xff, "bytes");
}

void test_wifi_password_bounds() {
  Settings s;
  CHECK(answer(wifi("n", std::string(7, 'p')), s) == "ERR password", "7 bytes");
  CHECK(answer(wifi("n", std::string(8, 'p')), s) == "OK" && s.passLen == 8, "8 bytes");
  CHECK(answer(wifi("n", std::string(63, 'p')), s) == "OK" && s.passLen == 63, "63 bytes");
  CHECK(answer(wifi("n", std::string(64, 'p')), s) == "ERR password", "64 bytes (a raw key) is not taken");
  CHECK(answer(wifi("n", std::string(65, 'p')), s) == "ERR password", "65 bytes");
}

void test_wifi_hex_errors() {
  Settings s;
  CHECK(answer("CFG WIFI 6g 70617373776f7264", s) == "ERR hex", "bad digit in the name");
  CHECK(answer("CFG WIFI 616 70617373776f7264", s) == "ERR hex", "odd-length name");
  CHECK(answer("CFG WIFI 61 7061737377x6f7264", s) == "ERR hex", "bad digit in the password");
  CHECK(answer("CFG WIFI 61 7061737377616", s) == "ERR hex", "odd-length password");
  CHECK(!s.hasWifi, "a refused line stores nothing");
}

void test_wifi_missing_and_extra_fields() {
  Settings s;
  CHECK(answer("CFG WIFI", s) == "ERR ssid", "no name");
  CHECK(answer("CFG WIFI 61", s) == "ERR password", "no password field");
  CHECK(answer(wifi("n", "password1") + " extra", s) == "ERR password", "an extra field");
  CHECK(!s.hasWifi, "nothing stored");
}

void test_refused_line_leaves_old_settings_alone() {
  Settings s;
  answer(wifi("old", "password1"), s);
  answer(wifi("new", std::string(64, 'p')), s);  // refused
  CHECK(s.ssidLen == 3 && memcmp(s.ssid, "old", 3) == 0, "old name kept");
  CHECK(s.passLen == 9, "old password kept");
}

// ---------- CFG LINK ----------
void test_link_valid() {
  Settings s;
  CHECK(answer(link("192.168.1.50", "5004"), s) == "OK", "link");
  CHECK(s.hasLink && strcmp(s.addr, "192.168.1.50") == 0 && s.port == 5004, "address and port");
  CHECK(FP == s.fp && TOKEN == s.token, "fingerprint and token");
}

void test_link_upper_case_digits_are_stored_lower_case() {
  Settings s;
  CHECK(answer(link("h", "1", std::string(64, 'A'), std::string(32, 'B')), s) == "OK", "upper case");
  CHECK(std::string(s.fp) == std::string(64, 'a') && std::string(s.token) == std::string(32, 'b'), "lowered");
}

void test_link_address_bounds() {
  Settings s;
  CHECK(answer(link("a", "5004"), s) == "OK", "1 char");
  CHECK(answer(link(std::string(64, 'a'), "5004"), s) == "OK" && strlen(s.addr) == 64, "64 chars");
  CHECK(answer(link(std::string(65, 'a'), "5004"), s) == "ERR address", "65 chars");
  CHECK(answer(link("host.local", "5004"), s) == "OK", "a name");
  CHECK(answer(link("fe80::1", "5004"), s) == "OK", "an IPv6 literal");
}

void test_link_port_bounds() {
  Settings s;
  CHECK(answer(link("h", "0"), s) == "ERR port", "0");
  CHECK(answer(link("h", "1"), s) == "OK" && s.port == 1, "1");
  CHECK(answer(link("h", "65535"), s) == "OK" && s.port == 65535, "65535");
  CHECK(answer(link("h", "65536"), s) == "ERR port", "65536");
  CHECK(answer(link("h", "999999"), s) == "ERR port", "6 digits");
  CHECK(answer(link("h", "50x4"), s) == "ERR port", "not a number");
  CHECK(answer(link("h", "-5"), s) == "ERR port", "negative");
}

void test_link_fingerprint_and_token_bounds() {
  Settings s;
  CHECK(answer(link("h", "5", std::string(63, 'a')), s) == "ERR fingerprint", "63 digits");
  CHECK(answer(link("h", "5", std::string(65, 'a')), s) == "ERR fingerprint", "65 digits");
  CHECK(answer(link("h", "5", std::string(63, 'a') + "g"), s) == "ERR fingerprint", "a non-hex digit");
  CHECK(answer(link("h", "5", FP, std::string(31, '1')), s) == "ERR token", "31 digits");
  CHECK(answer(link("h", "5", FP, std::string(33, '1')), s) == "ERR token", "33 digits");
  CHECK(answer(link("h", "5", FP, std::string(31, '1') + "z"), s) == "ERR token", "a non-hex token digit");
  CHECK(!s.hasLink, "nothing stored");
}

void test_link_missing_and_extra_fields() {
  Settings s;
  CHECK(answer("CFG LINK", s) == "ERR address", "nothing");
  CHECK(answer("CFG LINK host", s) == "ERR port", "no port");
  CHECK(answer("CFG LINK host 5004", s) == "ERR fingerprint", "no fingerprint");
  CHECK(answer("CFG LINK host 5004 " + FP, s) == "ERR token", "no token");
  CHECK(answer(link("h", "5") + " extra", s) == "ERR token", "an extra field");
}

// ---------- CFG CLEAR and the rest ----------
void test_clear() {
  Settings s;
  answer(wifi("n", "password1"), s);
  answer(link("h", "5"), s);
  CHECK(s.configured(), "configured");
  CHECK(answer("CFG CLEAR", s) == "OK", "clear");
  CHECK(!s.hasWifi && !s.hasLink && s.ssidLen == 0 && s.passLen == 0 && s.addr[0] == 0 && s.token[0] == 0, "gone");
  CHECK(answer("CFG CLEAR now", s) == "ERR cfg", "clear with a field");
}

void test_configured_needs_both_lines() {
  Settings s;
  answer(link("h", "5"), s);
  CHECK(!s.configured(), "link only");
  answer(wifi("n", "password1"), s);
  CHECK(s.configured(), "both");
}

void test_other_lines_are_not_cfg() {
  Settings s;
  for (const char *l : {"?", "L 1 2", "D 0 CFG", "CFGWIFI", "cfg wifi", "V 50", "A 16"}) {
    CHECK(!cfgParse(l, s).isCfg, "'%s' is not a CFG line", l);
  }
  CHECK(answer("CFG", s) == "ERR cfg" && answer("CFG FOO", s) == "ERR cfg", "unknown CFG words");
}

void test_longest_lines_fit() {
  Settings s;
  std::string w = wifi(std::string(32, 'n'), std::string(63, 'p'));
  std::string l = link(std::string(64, 'h'), "65535");
  CHECK(w.size() <= 256 && l.size() <= 256, "longest CFG WIFI %u, CFG LINK %u", (unsigned)w.size(), (unsigned)l.size());
  // R88 03:10 says 200 and 178; counted here: CFG WIFI 200 and CFG LINK 177 (without the newline)
  CHECK(w.size() == 200 && l.size() == 177, "CFG WIFI %u, CFG LINK %u", (unsigned)w.size(), (unsigned)l.size());
  LineReader r;
  std::string got;
  CHECK(feedAll(r, w + "\n", &got) == LineReader::Line && got == w, "reader keeps CFG WIFI whole");
  CHECK(answer(got, s) == "OK", "and it parses");
  CHECK(cfgParse(std::string(257, 'x').c_str(), s).isCfg == false, "(too long lines never reach the parser)");
}

void test_a_cfg_line_over_the_cap_is_long() {
  Settings s;
  CHECK(answer("CFG WIFI " + std::string(260, 'a'), s) == "ERR long", "parser also refuses over 256");
}

// ---------- fingerprint, HELLO, REFUSED ----------
void test_fingerprint_check() {
  uint8_t sha[32];
  for (int i = 0; i < 32; i++) sha[i] = (uint8_t)(i * 7 + 3);
  char h[65], H[65];
  for (int i = 0; i < 32; i++) { snprintf(h + 2 * i, 3, "%02x", sha[i]); snprintf(H + 2 * i, 3, "%02X", sha[i]); }
  CHECK(fingerprintMatches(h, sha) && fingerprintMatches(H, sha), "lower and upper case match");
  sha[31] ^= 1;
  CHECK(!fingerprintMatches(h, sha), "one bit off");
  sha[31] ^= 1; h[0] = h[0] == '0' ? '1' : '0';
  CHECK(!fingerprintMatches(h, sha), "first digit off");
  CHECK(!fingerprintMatches("", sha) && !fingerprintMatches(std::string(63, 'a').c_str(), sha), "short strings");
  char bad[65]; memcpy(bad, H, 65); bad[10] = 'z';
  CHECK(!fingerprintMatches(bad, sha), "non-hex");
}

void test_hello_lines() {
  char l[300];
  helloLine(l, sizeof l, "14", nullptr);
  CHECK(std::string(l) == "HELLO intake-panel 14 esp32c3", "USB hello: %s", l);
  helloLine(l, sizeof l, "14", TOKEN.c_str());
  CHECK(std::string(l) == "HELLO intake-panel 14 esp32c3 " + TOKEN, "Wi-Fi hello: %s", l);
  CHECK(strlen(l) <= MAX_LINE, "fits the station's HELLO limit");
}

void test_refused_reason() {
  CHECK(std::string(refusedReason("REFUSED usb")) == "usb", "usb");
  CHECK(std::string(refusedReason("REFUSED token")) == "token", "token");
  CHECK(refusedReason("L 0 0") == nullptr && refusedReason("REFUSED") == nullptr, "not refused lines");
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_reader_lines_and_boundaries);
  RUN_TEST(test_reader_long_line_is_dropped_whole_and_reported_once);
  RUN_TEST(test_reader_crlf_and_empty_lines);
  RUN_TEST(test_reader_a_line_after_a_long_one_is_not_polluted);
  RUN_TEST(test_wifi_valid_and_stored);
  RUN_TEST(test_wifi_open_network);
  RUN_TEST(test_wifi_ssid_bounds);
  RUN_TEST(test_wifi_name_may_hold_any_bytes);
  RUN_TEST(test_wifi_password_bounds);
  RUN_TEST(test_wifi_hex_errors);
  RUN_TEST(test_wifi_missing_and_extra_fields);
  RUN_TEST(test_refused_line_leaves_old_settings_alone);
  RUN_TEST(test_link_valid);
  RUN_TEST(test_link_upper_case_digits_are_stored_lower_case);
  RUN_TEST(test_link_address_bounds);
  RUN_TEST(test_link_port_bounds);
  RUN_TEST(test_link_fingerprint_and_token_bounds);
  RUN_TEST(test_link_missing_and_extra_fields);
  RUN_TEST(test_clear);
  RUN_TEST(test_configured_needs_both_lines);
  RUN_TEST(test_other_lines_are_not_cfg);
  RUN_TEST(test_longest_lines_fit);
  RUN_TEST(test_a_cfg_line_over_the_cap_is_long);
  RUN_TEST(test_fingerprint_check);
  RUN_TEST(test_hello_lines);
  RUN_TEST(test_refused_reason);
  return UNITY_END();
}
