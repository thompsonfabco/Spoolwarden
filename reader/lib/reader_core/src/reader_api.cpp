#include "reader_api.h"

#include <ArduinoJson.h>
#include <cstdio>
#include <cstring>

namespace reader {

namespace {

// A JSON value as display text: strings as-is, numbers printed, else "".
std::string text(JsonVariantConst v) {
  if (v.is<const char*>()) return v.as<const char*>();
  if (v.is<long long>()) return std::to_string(v.as<long long>());
  if (v.is<double>()) {
    char b[32];
    snprintf(b, sizeof(b), "%g", v.as<double>());
    return b;
  }
  return "";
}

// At most MAX_FIELD bytes, never ending inside a UTF-8 multi-byte character.
std::string cut(std::string s) {
  if (s.size() <= MAX_FIELD) return s;
  size_t n = MAX_FIELD;
  while (n > 0 && ((unsigned char)s[n] & 0xC0) == 0x80) n--;  // s[n] continues a character
  s.resize(n);
  return s;
}


// spool: {"id":12,"name":"Polymaker Grey",...} | "name" | 12 | null
std::string spoolText(JsonVariantConst v) {
  if (v.is<JsonObjectConst>()) {
    std::string name = text(v["name"]);
    if (!name.empty()) return name;
    std::string id = text(v["id"]);
    return id.empty() ? "" : "#" + id;
  }
  if (v.is<const char*>()) return v.as<const char*>();
  std::string id = text(v);
  return id.empty() ? "" : "#" + id;
}

// Only the known fields are kept (filter), and an oversize reply is refused.
bool parseArray(const char* json, JsonDocument& doc, const JsonDocument& filter) {
  if (!json || strlen(json) > MAX_RESPONSE_BYTES) return false;
  if (deserializeJson(doc, json, DeserializationOption::Filter(filter))) return false;
  return doc.is<JsonArrayConst>();
}

}  // namespace

bool responseTooBig(int contentLength) {
  return contentLength > (int)MAX_RESPONSE_BYTES;
}

bool parsePrinters(const char* json, std::vector<PrinterEntry>& out) {
  JsonDocument filter;
  JsonObject f = filter[0].to<JsonObject>();
  f["id"] = true; f["name"] = true;
  JsonDocument doc;
  if (!parseArray(json, doc, filter)) return false;
  std::vector<PrinterEntry> list;
  for (JsonVariantConst p : doc.as<JsonArrayConst>()) {
    if ((int)list.size() >= MAX_PRINTERS) break;
    if (!p.is<JsonObjectConst>()) continue;
    PrinterEntry e;
    e.id = text(p["id"]);
    if (e.id.empty() || e.id.size() > MAX_ID) continue;
    e.name = cut(text(p["name"]));
    if (e.name.empty()) e.name = cut(e.id);
    list.push_back(std::move(e));
  }
  out.swap(list);
  return true;
}

bool parseSlots(const char* json, std::vector<SlotEntry>& out) {
  JsonDocument filter;
  JsonObject f = filter[0].to<JsonObject>();
  f["label"] = true;
  f["present"] = true; f["type"] = true; f["spool"] = true;
  JsonDocument doc;
  if (!parseArray(json, doc, filter)) return false;
  std::vector<SlotEntry> list;
  for (JsonVariantConst s : doc.as<JsonArrayConst>()) {
    if ((int)list.size() >= MAX_SLOTS) break;
    if (!s.is<JsonObjectConst>()) continue;
    SlotEntry e;
    e.label = cut(text(s["label"]));
    if (e.label.empty()) continue;
    JsonVariantConst pr = s["present"];
    if (pr.is<bool>()) e.present = pr.as<bool>() ? PRESENT_YES : PRESENT_NO;
    e.type = cut(text(s["type"]));
    e.spool = cut(spoolText(s["spool"]));
    if (s["spool"].is<JsonObjectConst>()) {
      JsonVariantConst rg = s["spool"]["remaining_g"];
      if (rg.is<long long>()) {
        long long v = rg.as<long long>();
        if (v >= 0) e.remainingGrams = (int)v;
      } else if (rg.is<double>()) {
        double v = rg.as<double>();
        if (v >= 0.0) e.remainingGrams = (int)(v + 0.5);
      }
    }
    list.push_back(std::move(e));
  }
  out.swap(list);
  return true;
}

void SlotCache::apply(const std::string& printerId, bool ok, std::vector<SlotEntry> fresh) {
  if (ok) {
    printer = printerId;
    slots.swap(fresh);
  } else if (printer != printerId) {
    printer.clear();
    slots.clear();
  }
}

std::string wantedLabel(const std::string& saved, const std::string& current, bool turnedUnsaved) {
  if (turnedUnsaved && !current.empty()) return current;
  return saved.empty() ? current : saved;
}

bool listsDue(uint32_t now, uint32_t lastAt, bool serverOk, bool screenOn,
              uint32_t refreshMs, uint32_t retryMs) {
  uint32_t age = now - lastAt;
  if (!serverOk) return age > retryMs;
  return screenOn && age > refreshMs;
}

uint32_t listRetryMs(int failures) {
  uint32_t ms = 10000;
  for (int i = 1; i < failures && ms < 60000; i++) ms *= 2;
  return ms > 60000 ? 60000 : ms;
}

std::string slotContents(const SlotEntry& s) {
  if (s.present == PRESENT_NO) return "empty";
  if (!s.spool.empty()) return s.type.empty() ? s.spool : s.type + " " + s.spool;
  if (!s.type.empty()) return s.type + " (no spool)";
  if (s.present == PRESENT_YES) return "loaded, not set";
  return "?";
}

std::string spoolInfoText(const SlotEntry& s) {
  std::string base = slotContents(s);
  if (s.present == PRESENT_NO || s.remainingGrams < 0) return base;
  return base + "\n" + std::to_string(s.remainingGrams) + " g left";
}

ScanResult parseScanResult(int httpCode, const char* body) {
  ScanResult r;
  if (httpCode <= 0) {
    r.status = SCAN_UNREACHABLE;
    r.message = "no reply";
    return r;
  }
  bool ok2xx = httpCode >= 200 && httpCode < 300;
  JsonDocument doc;
  std::string status;
  if (body && !deserializeJson(doc, body) && doc.is<JsonObjectConst>()) {
    status = text(doc["status"]);
    r.message = text(doc["message"]);
    r.filament = text(doc["filament"]);
  }
  struct { const char* name; ScanStatus st; bool need2xx; } map[] = {
      {"set", SCAN_SET, true},
      {"queued", SCAN_QUEUED, true},
      {"unloaded", SCAN_UNLOADED, true},
      {"unknown_tag", SCAN_UNKNOWN_TAG, false},
      {"unknown_spool", SCAN_UNKNOWN_SPOOL, false},
      {"archived_spool", SCAN_ARCHIVED_SPOOL, false},
      {"no_preset", SCAN_NO_PRESET, false},
      {"offline", SCAN_OFFLINE, false},
      {"error", SCAN_ERROR, false},
  };
  r.status = SCAN_ERROR;
  for (auto& m : map) {
    if (status == m.name && (ok2xx || !m.need2xx)) {
      r.status = m.st;
      break;
    }
  }
  if (r.message.empty() && !ok2xx) r.message = "HTTP " + std::to_string(httpCode);
  return r;
}

Feedback feedbackFor(ScanStatus s) {
  switch (s) {
    case SCAN_SET:
    case SCAN_UNLOADED: return FB_OK;
    case SCAN_QUEUED: return FB_QUEUED;
    case SCAN_UNKNOWN_TAG:
    case SCAN_UNKNOWN_SPOOL:
    case SCAN_ARCHIVED_SPOOL: return FB_UNKNOWN;
    default: return FB_ERROR;
  }
}

const char* headline(ScanStatus s) {
  switch (s) {
    case SCAN_SET: return "Set";
    case SCAN_QUEUED: return "Load it";
    case SCAN_UNLOADED: return "Unloaded";
    case SCAN_UNKNOWN_TAG: return "Unknown tag";
    case SCAN_UNKNOWN_SPOOL: return "No spool";
    case SCAN_ARCHIVED_SPOOL: return "Archived";
    case SCAN_NO_PRESET: return "No preset";
    case SCAN_OFFLINE: return "Printer off";
    case SCAN_UNREACHABLE: return "Server down";
    default: return "Error";
  }
}

std::string scanBody(const std::string& uid, const std::string& printer, const std::string& slot) {
  JsonDocument doc;
  doc["uid"] = uid;
  doc["printer"] = printer;
  doc["slot"] = slot;
  std::string out;
  serializeJson(doc, out);
  return out;
}

std::string unloadBody(const std::string& printer, const std::string& slot) {
  JsonDocument doc;
  doc["printer"] = printer;
  doc["slot"] = slot;
  std::string out;
  serializeJson(doc, out);
  return out;
}

std::string urlEncode(const std::string& s) {
  static const char* hex = "0123456789ABCDEF";
  std::string out;
  for (unsigned char c : s) {
    if (isalnum(c) || c == '-' || c == '_' || c == '.' || c == '~') {
      out += (char)c;
    } else {
      out += '%';
      out += hex[c >> 4];
      out += hex[c & 15];
    }
  }
  return out;
}

std::string joinUrl(const std::string& base, const std::string& path) {
  std::string b = base;
  while (!b.empty() && b.back() == '/') b.pop_back();
  size_t i = 0;
  while (i < path.size() && path[i] == '/') i++;
  return b + "/" + path.substr(i);
}

namespace {

void copyStr(JsonVariantConst v, char* dst, size_t cap) {
  if (!v.is<const char*>() || cap == 0) return;
  const char* s = v.as<const char*>();
  if (!s) return;
  size_t i = 0;
  for (; i + 1 < cap && s[i] != '\0'; i++) dst[i] = s[i];
  dst[i] = '\0';
}

void readBool(JsonVariantConst v, bool& dst) {
  if (v.is<bool>()) dst = v.as<bool>();
}

void readClampedU16(JsonVariantConst v, uint16_t& dst, uint16_t lo, uint16_t hi) {
  if (!v.is<long long>() && !v.is<double>()) return;
  long long val = v.is<long long>() ? v.as<long long>() : (long long)v.as<double>();
  if (val < lo) val = lo;
  if (val > hi) val = hi;
  dst = (uint16_t)val;
}

}  // namespace

bool parseRemoteConfig(const char* json, RemoteConfig& out) {
  if (!json || strlen(json) > MAX_RESPONSE_BYTES) return false;
  JsonDocument doc;
  if (deserializeJson(doc, json) != DeserializationError::Ok || !doc.is<JsonObjectConst>()) {
    return false;
  }
  JsonObjectConst obj = doc.as<JsonObjectConst>();
  RemoteConfig cfg;
  JsonVariantConst cv = obj["config_version"];
  if (cv.is<long long>()) {
    long long val = cv.as<long long>();
    if (val >= 0) cfg.configVersion = (uint32_t)val;
  }
  copyStr(obj["default_printer"], cfg.defaultPrinter, sizeof(cfg.defaultPrinter));
  readBool(obj["buzzer_enabled"], cfg.buzzerEnabled);
  readBool(obj["buzzer_low"], cfg.buzzerLow);
  readBool(obj["auto_ota"], cfg.autoOta);
  readClampedU16(obj["dim_after_s"], cfg.dimAfterS, 10, 3600);
  readClampedU16(obj["off_after_s"], cfg.offAfterS, 30, 7200);
  readClampedU16(obj["scan_cooldown_ms"], cfg.scanCooldownMs, 500, 10000);
  readClampedU16(obj["list_refresh_s"], cfg.listRefreshS, 10, 600);
  readClampedU16(obj["ota_check_hours"], cfg.otaCheckHours, 1, 168);
  readBool(obj["encoder_invert"], cfg.encoderInvert);
  readBool(obj["ota_now"], cfg.otaNow);
  readBool(obj["reboot_now"], cfg.rebootNow);
  copyStr(obj["hub_ip"], cfg.hubIp, sizeof(cfg.hubIp));
  out = cfg;
  return true;
}

}  // namespace reader
