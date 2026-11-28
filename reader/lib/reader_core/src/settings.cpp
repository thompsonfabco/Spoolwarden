#include "settings.h"

#include "hub_address.h"

#include <ArduinoJson.h>
#include <cstdio>
#include <cstring>

namespace reader {

namespace {

bool tokenChars(const std::string& s) {
  for (char c : s)
    if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_' || c == '-'))
      return false;
  return true;
}

bool pem(const std::string& s, const char* kind) {
  return s.size() <= MAX_PEM && s.rfind(std::string("-----BEGIN ") + kind + "-----", 0) == 0 &&
         s.find(std::string("-----END ") + kind + "-----") != std::string::npos;
}

std::string str(JsonVariantConst v) { return v.is<const char*>() ? std::string(v.as<const char*>()) : std::string(); }

}  // namespace

bool parseSettings(const char* json, Settings& out, std::string& why) {
  JsonDocument doc;
  if (deserializeJson(doc, json) != DeserializationError::Ok || !doc.is<JsonObjectConst>()) {
    why = "not a JSON object";
    return false;
  }
  JsonObjectConst o = doc.as<JsonObjectConst>();
  Settings s;
  s.ssid = str(o["ssid"]);
  s.pass = str(o["pass"]);
  s.base = str(o["base"]);
  s.token = str(o["token"]);
  s.otaKey = str(o["ota_key"]);
  s.rootCa = str(o["root_ca"]);
  s.buzzerLow = o["buzzer_low"].is<bool>() && o["buzzer_low"].as<bool>();
  s.hubIp = str(o["hub_ip"]);
  s.printer = str(o["printer"]);
  while (!s.base.empty() && s.base.back() == '/') s.base.pop_back();
  if (s.ssid.empty() || s.ssid.size() > 32) { why = "ssid must be 1 to 32 bytes"; return false; }
  if (!s.pass.empty() && (s.pass.size() < 8 || s.pass.size() > 63)) { why = "pass must be 8 to 63 characters, or empty"; return false; }
  if (s.base.rfind("https://", 0) != 0 || s.base.size() > 200) { why = "base must be an https address"; return false; }
  if (s.token.size() < 16 || s.token.size() > 128 || !tokenChars(s.token)) { why = "token is not a reader token"; return false; }
  if (!s.otaKey.empty() && !pem(s.otaKey, "PUBLIC KEY")) { why = "ota_key is not a PEM public key"; return false; }
  if (!s.rootCa.empty() && !pem(s.rootCa, "CERTIFICATE")) { why = "root_ca is not a PEM certificate"; return false; }
  if (!s.hubIp.empty() && !isIpv4(s.hubIp)) { why = "hub_ip is not an IPv4 address"; return false; }
  if (s.printer.size() > MAX_PRINTER_ID) { why = "printer is too long"; return false; }
  out = s;
  return true;
}

Command command(const std::string& line, std::string& arg) {
  if (line.empty()) return CMD_NONE;
  if (line == "FK?") return CMD_HELLO;
  if (line.rfind("FK-SET ", 0) == 0) { arg = line.substr(7); return CMD_SET; }
  return CMD_OTHER;
}

bool LineReader::push(char c, std::string& line) {
  if (c == '\r') return false;
  if (c == '\n') {
    bool whole = !overflow_;
    overflow_ = false;
    if (whole) line.swap(buf_);
    buf_.clear();
    return whole;
  }
  if (overflow_) return false;
  if (buf_.size() >= MAX_LINE) { overflow_ = true; buf_.clear(); return false; }
  buf_.push_back(c);
  return false;
}

std::string settingsStamp(const Settings& s) {
  unsigned long long h = 1469598103934665603ULL;
  auto mix = [&h](const std::string& f) {
    for (unsigned char c : f) { h ^= c; h *= 1099511628211ULL; }
    h ^= 0xff; h *= 1099511628211ULL;   // field separator: ("ab","c") and ("a","bc") differ
  };
  mix(s.ssid); mix(s.pass); mix(s.base); mix(s.token); mix(s.otaKey); mix(s.rootCa);
  mix(s.buzzerLow ? "1" : "0");
  if (!s.hubIp.empty()) mix(s.hubIp);   // only when set, so the stamp of a build without it is unchanged
  char out[17];
  snprintf(out, sizeof(out), "%016llx", h);
  return out;
}

bool adoptCompiled(const Settings& flash, const std::string& savedStamp, const std::string& compiledStamp) {
  (void)savedStamp;
  (void)compiledStamp;
  return flash.base.empty();
}

std::string startPrinter(const std::string& remembered, const std::string& provisioned, bool provisionedIsNew) {
  return provisionedIsNew && !provisioned.empty() ? provisioned : remembered;
}

void BootReport::server(int httpCode) {
  if (httpCode == 401 || httpCode == 403) server_ = "unauth";
  else if (httpCode > 0 || httpCode == HTTP_TOO_BIG) server_ = "ok";
  else server_ = "fail";
}

std::string helloLine(int64_t build, bool configured, const char* wifi, const char* server) {
  char out[96];
  snprintf(out, sizeof(out), "FK-READER %lld configured=%d wifi=%s server=%s", (long long)build, configured ? 1 : 0, wifi, server);
  return out;
}

}  // namespace reader
