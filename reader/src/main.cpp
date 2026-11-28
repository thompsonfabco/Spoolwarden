// filament-kit reader — ESP32-C3 NFC tag reader for any printer, any slot layout
// -----------------------------------------------------------------------------
// Thin client. Reads an NFC tag UID over I2C (PN532), lets you pick a printer and
// one of its slots with a rotary encoder (shown on an SSD1306 OLED), and POSTs
// {uid, printer, slot} to the filament-kit server's reader API. The server does
// the Spoolman lookup + printer command and returns the result, which we beep
// and show. The printer and slot lists come from the server, so a new
// printer or AMS layout needs no firmware change. API contract: README.md.
//
// Target board: ESP32-C3 Super Mini. All wiring is in the CONFIG block below —
// change pins freely. PN532 and OLED share the I2C bus (different addresses), so
// the whole thing is 2 I2C pins + encoder (CLK/DT/SW) + buzzer.
//
// The pure logic (list parsing, scan statuses, knob menu, saved
// choice) lives in lib/reader_core and is unit-tested on the host (pio test -e native).
// -----------------------------------------------------------------------------
#include <Arduino.h>
#include <Wire.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <ESPmDNS.h>
#include <HTTPClient.h>
#include <Adafruit_PN532.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <ArduinoJson.h>
#include <Update.h>
#include <esp_ota_ops.h>
#include <mbedtls/pk.h>
#include <mbedtls/sha256.h>
#include <mbedtls/base64.h>
#include <Preferences.h>
#if __has_include("secrets.h")
#include "secrets.h"         // optional: a build with settings compiled in (copied into flash; see loadSettings)
#define HAVE_SECRETS 1
#endif
#ifndef READER_HUB_IP
#define READER_HUB_IP ""
#endif
#include "fw_build.h"        // FW_BUILD, generated per build by fw_build.py
#include "reader_api.h"
#include "menu.h"
#include "settings.h"
#include "hub_address.h"
#include "ota_digest.h"

using namespace reader;

// ======================= CONFIG (edit me) ====================================
// Wi-Fi, the server's base URL, the reader token, the OTA public key and the
// buzzer's trigger are written into the reader over USB by `filament-kit reader
// setup` or the setup page (settings.h: the FK-SET line) and kept in flash. A
// build with secrets.h copies its values in (again whenever they change). Everything the reader calls
// hangs off the base URL:
//   GET  printers                 GET  printers/<id>/slots      POST scan
//   GET  healthz (OTA rollback)   GET  fw/manifest.json + fw/firmware.bin (OTA)

// Root CA to validate the server cert. Default: ISRG Root X1 (Let's Encrypt
// chain); the settings' root_ca (or READER_ROOT_CA in secrets.h) for a server with another CA.
#ifdef READER_ROOT_CA
static const char* DEFAULT_ROOT_CA = READER_ROOT_CA;
#else
static const char* DEFAULT_ROOT_CA = R"CERT(-----BEGIN CERTIFICATE-----
MIIFazCCA1OgAwIBAgIRAIIQz7DSQONZRGPgu2OCiwAwDQYJKoZIhvcNAQELBQAw
TzELMAkGA1UEBhMCVVMxKTAnBgNVBAoTIEludGVybmV0IFNlY3VyaXR5IFJlc2Vh
cmNoIEdyb3VwMRUwEwYDVQQDEwxJU1JHIFJvb3QgWDEwHhcNMTUwNjA0MTEwNDM4
WhcNMzUwNjA0MTEwNDM4WjBPMQswCQYDVQQGEwJVUzEpMCcGA1UEChMgSW50ZXJu
ZXQgU2VjdXJpdHkgUmVzZWFyY2ggR3JvdXAxFTATBgNVBAMTDElTUkcgUm9vdCBY
MTCCAiIwDQYJKoZIhvcNAQEBBQADggIPADCCAgoCggIBAK3oJHP0FDfzm54rVygc
h77ct984kIxuPOZXoHj3dcKi/vVqbvYATyjb3miGbESTtrFj/RQSa78f0uoxmyF+
0TM8ukj13Xnfs7j/EvEhmkvBioZxaUpmZmyPfjxwv60pIgbz5MDmgK7iS4+3mX6U
A5/TR5d8mUgjU+g4rk8Kb4Mu0UlXjIB0ttov0DiNewNwIRt18jA8+o+u3dpjq+sW
T8KOEUt+zwvo/7V3LvSye0rgTBIlDHCNAymg4VMk7BPZ7hm/ELNKjD+Jo2FR3qyH
B5T0Y3HsLuJvW5iB4YlcNHlsdu87kGJ55tukmi8mxdAQ4Q7e2RCOFvu396j3x+UC
B5iPNgiV5+I3lg02dZ77DnKxHZu8A/lJBdiB3QW0KtZB6awBdpUKD9jf1b0SHzUv
KBds0pjBqAlkd25HN7rOrFleaJ1/ctaJxQZBKT5ZPt0m9STJEadao0xAH0ahmbWn
OlFuhjuefXKnEgV4We0+UXgVCwOPjdAvBbI+e0ocS3MFEvzG6uBQE3xDk3SzynTn
jh8BCNAw1FtxNrQHusEwMFxIt4I7mKZ9YIqioymCzLq9gwQbooMDQaHWBfEbwrbw
qHyGO0aoSCqI3Haadr8faqU9GY/rOPNk3sgrDQoo//fb4hVC1CLQJ13hef4Y53CI
rU7m2Ys6xt0nUW7/vGT1M0NPAgMBAAGjQjBAMA4GA1UdDwEB/wQEAwIBBjAPBgNV
HRMBAf8EBTADAQH/MB0GA1UdDgQWBBR5tFnme7bl5AFzgAiIyBpY9umbbjANBgkq
hkiG9w0BAQsFAAOCAgEAVR9YqbyyqFDQDLHYGmkgJykIrGF1XIpu+ILlaS/V9lZL
ubhzEFnTIZd+50xx+7LSYK05qAvqFyFWhfFQDlnrzuBZ6brJFe+GnY+EgPbk6ZGQ
3BebYhtF8GaV0nxvwuo77x/Py9auJ/GpsMiu/X1+mvoiBOv/2X/qkSsisRcOj/KK
NFtY2PwByVS5uCbMiogziUwthDyC3+6WVwW6LLv3xLfHTjuCvjHIInNzktHCgKQ5
ORAzI4JMPJ+GslWYHb4phowim57iaztXOoJwTdwJx4nLCgdNbOhdjsnvzqvHu7Ur
TkXWStAmzOVyyghqpZXjFaH3pO3JLF+l+/+sKAIuvtd7u+Nxe5AW0wdeRlN8NwdC
jNPElpzVmbUq4JUagEiuTDkHzsxHpFKVK7q4+63SM1N95R1NbdWhscdCb+ZAJzVc
oyi3B43njTOQ5yOf+1CceWxG1bQVs5ZufpsMljq4Ui0/1lvh+wjChP4kqKOJ2qxq
4RgqsahDYVvTH9w7jXbyLeiNdd8XM2w9U/t7y0Ff/9yi0GE44Za4rF2LN9d11TPA
mRGunUHBcnWEvgJBQl9nJEiU0Zsnvgc/ubhPgXRR4Xq37Z0j4r7g1SgEEzwxA57d
emyPxgcYxn/eR44/KJ4EBs+lVDR3veyJm+kXQ99b21/+jh5Xos1AnX5iItreGCc=
-----END CERTIFICATE-----
)CERT";
#endif

// --- Pin map: ESP32-C3 Super Mini. Only GPIO0-10,20,21 are broken out; avoid
//     GPIO8 (onboard LED + strap) and GPIO9 (BOOT button + strap). USB pins
//     18/19 and flash pins 12-17 are not on the header. ---

// --- I2C bus (PN532 + OLED share it) ---
static const int PIN_SDA     = 5;
static const int PIN_SCL     = 6;

// --- PN532 control pins: LEAVE THESE UNCONNECTED. The library's constructor
//     wants pin numbers, but over I2C it polls readiness on the bus (IRQ unused),
//     and the header's RSTO is the PN532's reset *output* (the real input, RSTPDN,
//     is tied high on-board). Wiring them on the red V3 board jammed the bus
//     (SCL held low, error 263). These GPIOs just stay unwired. ---
static const int PN532_IRQ   = 7;
static const int PN532_RESET = 10;

// --- OLED ---
static const int OLED_ADDR   = 0x3C;
static const int OLED_W      = 128;
static const int OLED_H      = 64;
static const int OLED_COLS   = OLED_W / 6;   // 21 columns at text size 1

// --- Rotary encoder (KY-040 style: CLK/DT quadrature + SW push).
//     Rotate = change slot; press = Actions menu (Spool info, Re-send last tag,
//     Unload slot, Status, Update FW); hold 1 s = printer list; hold 4 s = OTA check.
//     If rotation feels reversed, swap CLK and DT. ---
static const int PIN_ENC_CLK = 3;
static const int PIN_ENC_DT  = 4;
static const int PIN_ENC_SW  = 1;
// Push switch wiring: true = switch between GPIO1 and 3V3 (this build; internal
// pull-down, pressed = HIGH). false = switch to GND (internal pull-up, pressed = LOW).
static const bool ENC_SW_TO_VCC = true;

// --- Buzzer (on a PWM-capable pin) ---
//     Bare buzzer (the default, high-triggered): a magnetic buzzer switched by an
//     N-channel MOSFET such as a BS170, pin HIGH = on, so the pin idles LOW. A
//     reader built with a buzzer with a driver board (the 3-pin GND, I/O, VCC
//     module, PNP driver) is low-triggered: the settings' buzzer_low
//     (`reader setup --buzzer low`). A build from a secrets.h without
//     READER_BUZZER_ACTIVE_LOW predates the setting, and those readers were all
//     built with that module, so it means low.
static const int  PIN_BUZZER        = 0;
#if defined(READER_BUZZER_ACTIVE_LOW)
static const bool COMPILED_BUZZER_LOW = READER_BUZZER_ACTIVE_LOW;
#elif defined(HAVE_SECRETS)
static const bool COMPILED_BUZZER_LOW = true;
#else
static const bool COMPILED_BUZZER_LOW = false;
#endif

static const uint32_t SETTLE_MS = 600;   // knob idle time before the slot choice is saved

// Re-scan guard: ignore the same tag if re-read within this window.
static const uint32_t SCAN_COOLDOWN_MS = 2500;
// How long a result stays on screen before reverting to the slot picker.
static const uint32_t RESULT_HOLD_MS   = 4000;
// Longer hold for multi-line Spool info / Status screens (dismissible by press/turn).
static const uint32_t INFO_HOLD_MS     = 12000;
// "Load filament" (queued) prompt stays up longer — you're walking to the AMS.
static const uint32_t QUEUED_HOLD_MS   = 15000;
// Printer + slot lists: refreshed at boot, every LIST_REFRESH_MS while the screen
// is lit (backing off from 10 s to 60 s while the server is down), and LIST_AFTER_SET_MS after a set (to show the new contents).
static const uint32_t LIST_REFRESH_MS   = 60000;
static const uint32_t LIST_AFTER_SET_MS = 5000;
// While the server is down the lists are retried after 10 s, 20 s, 40 s, then every
// 60 s (listRetryMs), screen on or off.
// A tap does not re-check when the last refresh failed less than this ago.
static const uint32_t TAP_RECHECK_MS    = 5000;
// Long labels/names scroll one column per MARQUEE_STEP_MS.
static const uint32_t MARQUEE_STEP_MS  = 350;
// =============================================================================

Adafruit_PN532 nfc(PN532_IRQ, PN532_RESET);
Adafruit_SSD1306 display(OLED_W, OLED_H, &Wire, -1);

// URLs, built from the settings' base in setup()
String urlPrinters, urlScan, urlUnload, urlHealthz, urlManifest, urlBin, urlConfig, urlConfigAck;

std::vector<PrinterEntry> printers;   // from GET printers
SlotCache slotCache;                  // GET printers/<id>/slots, tagged with that printer's id
std::vector<SlotEntry>& slots = slotCache.slots;
Menu   menu;
Button button;
bool   serverOk = false;              // the last list fetch worked
bool   nfcOk    = false;              // PN532 initialized cleanly

enum UiState { UI_NORMAL, UI_ACTIONS, UI_CONFIRM_UNLOAD };
UiState  uiState = UI_NORMAL;
int      uiCursor = 0;
uint32_t uiLastInput = 0;

TagGate  tagGate;                     // which tag reads count as a new scan
String   lastUid = "";
uint32_t resultUntil = 0;

const PrinterEntry* curPrinter() {
  return (menu.printer >= 0 && menu.printer < (int)printers.size()) ? &printers[menu.printer] : nullptr;
}
// Only slots fetched for the selected printer count; after a printer change whose
// fetch failed there are none (the screen says "No slots" and scans are refused).
bool slotsUsable() {
  const PrinterEntry* p = curPrinter();
  return p && slotCache.usableFor(p->id);
}
const SlotEntry* curSlot() {
  if (!slotsUsable()) return nullptr;
  return (menu.slot >= 0 && menu.slot < (int)slots.size()) ? &slots[menu.slot] : nullptr;
}

// ---- buzzer patterns --------------------------------------------------------
// Park the pin at its "off" level (LOW for a high-triggered buzzer, HIGH for the
// low-triggered module). Beeps drive LEDC directly rather than tone(): tone()'s
// async task ends each note at duty 0 and can detach the pin after we've parked it.
// ---- settings (written over USB, kept in flash) ---------------------------------
Settings cfg;              // what the reader runs with
RemoteConfig g_rcfg;       // remote config pushed from the Hub (NVS "fk-rcfg")
bool     configured = false;
BootReport boot;           // how the last start went, for FK? (wifi ok / fail / -, server ok / fail / unauth / -)
bool printerNew = false;   // the settings were just written over USB with a printer: start on it (see setup)
bool pendingCompiledSave = false;   // compiled-in secrets adopted in RAM; persist only once OTA is confirmed

const char* rootCa() { return cfg.rootCa.empty() ? DEFAULT_ROOT_CA : cfg.rootCa.c_str(); }

bool saveSettings(const Settings& s);

void loadSettings() {
  Preferences p;
  p.begin("fk-cfg", true);
  cfg.ssid = p.getString("ssid", "").c_str();
  cfg.pass = p.getString("pass", "").c_str();
  cfg.base = p.getString("base", "").c_str();
  cfg.token = p.getString("token", "").c_str();
  cfg.otaKey = p.getString("ota_key", "").c_str();
  cfg.rootCa = p.getString("root_ca", "").c_str();
  cfg.buzzerLow = p.getBool("buzzer_low", COMPILED_BUZZER_LOW);
  cfg.hubIp = p.getString("hub_ip", "").c_str();
  cfg.printer = p.getString("printer", "").c_str();
  printerNew = p.getBool("printer_new", false);
  std::string savedStamp = p.getString("src_stamp", "").c_str();
  p.end();
#ifdef HAVE_SECRETS
  // The compiled-in settings are adopted only when flash holds none, and persisted only after
  // esp_ota_mark_app_valid_cancel_rollback() (or on a non-pending-verify boot) so a bad OTA
  // build never writes its secrets.h over NVS before rolling back.
  Settings compiled;
  compiled.ssid = WIFI_SSID; compiled.pass = WIFI_PASS; compiled.base = READER_BASE; compiled.token = READER_TOKEN;
  compiled.otaKey = OTA_PUBKEY;
  if (compiled.otaKey.find("REPLACE-WITH") != std::string::npos) compiled.otaKey.clear();
#ifdef READER_ROOT_CA
  compiled.rootCa = READER_ROOT_CA;
#endif
  compiled.buzzerLow = COMPILED_BUZZER_LOW;
  compiled.hubIp = READER_HUB_IP;   // copied into flash like the rest, so an update built without secrets.h keeps it
  std::string stamp = settingsStamp(compiled);
  if (adoptCompiled(cfg, savedStamp, stamp)) {
    cfg = compiled;
    printerNew = false;
    pendingCompiledSave = true;
  }
#endif
  configured = !cfg.base.empty() && !cfg.token.empty() && !cfg.ssid.empty();
}

bool saveSettings(const Settings& s) {
  Preferences p;
  if (!p.begin("fk-cfg", false)) return false;
  struct PrevStr  { const char* key; bool had; String val; };
  struct PrevBool { const char* key; bool had; bool val; };
  auto snapStr = [&](const char* k) -> PrevStr {
    bool had = p.isKey(k);
    return {k, had, had ? p.getString(k, "") : String()};
  };
  auto snapBool = [&](const char* k) -> PrevBool {
    bool had = p.isKey(k);
    return {k, had, had ? p.getBool(k, false) : false};
  };
  PrevStr prevStrs[] = {
    snapStr("ssid"), snapStr("pass"), snapStr("base"), snapStr("token"),
    snapStr("ota_key"), snapStr("root_ca"), snapStr("hub_ip"), snapStr("printer"),
  };
  PrevBool prevBools[] = {snapBool("buzzer_low"), snapBool("printer_new")};
  auto writeStr = [&](const char* k, const std::string& v) -> bool {
    if (v.empty()) return !p.isKey(k) || p.remove(k);
    return p.putString(k, v.c_str()) == v.size();
  };
  bool ok = writeStr("ssid", s.ssid) &&
            writeStr("pass", s.pass) &&
            writeStr("base", s.base) &&
            writeStr("token", s.token) &&
            writeStr("ota_key", s.otaKey) &&
            writeStr("root_ca", s.rootCa) &&
            p.putBool("buzzer_low", s.buzzerLow) == 1 &&
            writeStr("hub_ip", s.hubIp) &&
            writeStr("printer", s.printer) &&
            p.putBool("printer_new", !s.printer.empty()) == 1;
  if (!ok) {
    for (const auto& prev : prevStrs) {
      if (!prev.had) p.remove(prev.key);
    }
    for (const auto& prev : prevBools) {
      if (!prev.had) p.remove(prev.key);
    }
    for (const auto& prev : prevStrs) {
      if (prev.had) p.putString(prev.key, prev.val);
    }
    for (const auto& prev : prevBools) {
      if (prev.had) p.putBool(prev.key, prev.val);
    }
  }
  p.end();
  return ok;
}

void loadRemoteConfig() {
  g_rcfg = RemoteConfig{};
  g_rcfg.buzzerLow = cfg.buzzerLow;
  Preferences p;
  if (!p.begin("fk-rcfg", true)) return;
  g_rcfg.configVersion = p.getUInt("ver", 0);
  String dp = p.getString("def_printer", "");
  strncpy(g_rcfg.defaultPrinter, dp.c_str(), sizeof(g_rcfg.defaultPrinter) - 1);
  g_rcfg.defaultPrinter[sizeof(g_rcfg.defaultPrinter) - 1] = '\0';
  g_rcfg.buzzerEnabled = p.getBool("buz_en", true);
  g_rcfg.buzzerLow = p.getBool("buz_low", cfg.buzzerLow);
  g_rcfg.autoOta = p.getBool("auto_ota", true);
  g_rcfg.dimAfterS = p.getUShort("dim_s", 120);
  g_rcfg.offAfterS = p.getUShort("off_s", 600);
  g_rcfg.scanCooldownMs = p.getUShort("cool_ms", 2500);
  g_rcfg.listRefreshS = p.getUShort("list_s", 60);
  g_rcfg.otaCheckHours = p.getUShort("ota_h", 24);
  g_rcfg.encoderInvert = p.getBool("enc_inv", false);
  String hi = p.getString("hub_ip", "");
  strncpy(g_rcfg.hubIp, hi.c_str(), sizeof(g_rcfg.hubIp) - 1);
  g_rcfg.hubIp[sizeof(g_rcfg.hubIp) - 1] = '\0';
  p.end();
}

void saveRemoteConfig(const RemoteConfig& c) {
  Preferences p;
  if (!p.begin("fk-rcfg", false)) return;
  p.putUInt("ver", c.configVersion);
  p.putString("def_printer", c.defaultPrinter);
  p.putBool("buz_en", c.buzzerEnabled);
  p.putBool("buz_low", c.buzzerLow);
  p.putBool("auto_ota", c.autoOta);
  p.putUShort("dim_s", c.dimAfterS);
  p.putUShort("off_s", c.offAfterS);
  p.putUShort("cool_ms", c.scanCooldownMs);
  p.putUShort("list_s", c.listRefreshS);
  p.putUShort("ota_h", c.otaCheckHours);
  p.putBool("enc_inv", c.encoderInvert);
  p.putString("hub_ip", c.hubIp);
  p.end();
}

// FK? and FK-SET over USB (settings.h). Never echoes a setting.
LineReader serialLines;
void serialPoll() {
  std::string line, arg;
  while (Serial.available() > 0) {
    if (!serialLines.push((char)Serial.read(), line)) continue;
    switch (command(line, arg)) {
      case CMD_HELLO:
        Serial.println(helloLine((int64_t)FW_BUILD, configured, boot.wifiText(), boot.serverText()).c_str());
        break;
      case CMD_SET: {
        Settings s; std::string why;
        if (!parseSettings(arg.c_str(), s, why)) { Serial.printf("FK-ERR %s\n", why.c_str()); break; }
        if (!saveSettings(s)) { Serial.println("FK-ERR could not save the settings"); break; }
        Serial.println("FK-OK");
        Serial.flush();
        delay(300);
        ESP.restart();
        break;
      }
      default:
        break;
    }
  }
}

void buzzIdle() { pinMode(PIN_BUZZER, OUTPUT); digitalWrite(PIN_BUZZER, g_rcfg.buzzerLow ? HIGH : LOW); }
void beep(int freq, int ms) {
  if (!g_rcfg.buzzerEnabled) return;
  if (!ledcAttach(PIN_BUZZER, freq, 10))  // 10-bit keeps the divider legal down to ~300 Hz
    Serial.printf("beep: ledcAttach(%d Hz) failed\n", freq);
  ledcWrite(PIN_BUZZER, 512);            // 50% square wave (polarity-agnostic)
  delay(ms);
  ledcDetach(PIN_BUZZER); buzzIdle();
}
// The 3.3 V buzzer is loud only near its resonance: 2 kHz (pleasant) and 4 kHz
// (shrill) tested loudest, so patterns differ by rhythm, with 4 kHz for errors.
void buzzOk()      { beep(2000, 90); delay(60); beep(2000, 90); }          // set OK
void buzzUnknown() { beep(2000, 600); }                                    // tag/spool not usable
void buzzError()   { for (int i=0;i<3;i++){ beep(4000,120); delay(80);} }  // unreachable / error
void buzzTick()    { beep(2000, 15); }                                     // knob/button
void buzzQueued()  { beep(2000, 80); delay(80); beep(2000, 350); }         // queued: short-long
void buzzStuck()   { for (int i=0;i<2;i++){ beep(4000,600); delay(200);} } // I2C bus stuck: unplug to power-cycle

// ---- OLED screens -----------------------------------------------------------
bool     updateReady = false;     // a newer signed build is on the server
uint32_t scrollStep = 0;          // marquee position for long names/labels
uint32_t scrollAt = 0;

bool needsScroll() {
  if (menu.mode == MODE_PRINTERS) {
    for (auto& p : printers) if (p.name.size() > (size_t)OLED_COLS - 2) return true;
    return false;
  }
  if (menu.mode == MODE_ACTIONS) return false;
  const PrinterEntry* p = curPrinter();
  const SlotEntry* s = curSlot();
  if (p && p->name.size() > (size_t)OLED_COLS - 6) return true;
  if (s) {
    if (s->label.size() > labelColumns(labelTextSize(s->label.size()))) return true;
    if (slotContents(*s).size() > (size_t)OLED_COLS) return true;
  }
  return false;
}

void header(const String& left, const String& right) {
  display.setTextSize(1);
  int maxLeft = right.length() ? max(0, OLED_COLS - (int)right.length() - 1) : OLED_COLS;
  String clipped = left.length() > (unsigned int)maxLeft ? left.substring(0, maxLeft) : left;
  display.setCursor(0, 0);
  display.print(clipped);
  if (right.length()) {
    display.setCursor(OLED_W - 6 * right.length(), 0);
    display.print(right);
  }
  display.drawFastHLine(0, 11, OLED_W, SSD1306_WHITE);
}

void showPicker() {
  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);
  const PrinterEntry* p = curPrinter();
  const SlotEntry* s = curSlot();
  if (!p) {
    header("Spoolwarden reader", !nfcOk ? "NFC err" : "");
    display.setTextSize(1);
    display.setCursor(0, 20);
    if (WiFi.status() != WL_CONNECTED) display.print("No WiFi");
    else if (!serverOk)                display.print("Server unreachable\nretrying...");
    else                               display.print("No printers set up\non the server");
    if (!nfcOk) { display.setCursor(0, 42); display.print("NFC error"); }
    if (updateReady) { display.setCursor(0, 54); display.print("update: hold knob 4s"); }
    display.display();
    return;
  }
  String right;
  if (!nfcOk) right = "NFC err";
  else if (WiFi.status() != WL_CONNECTED || !serverOk) right = "Offline";
  else if (updateReady) right = "UPD";
  else if (s) right = String(menu.slot + 1) + "/" + String(slots.size());
  size_t nameCols = OLED_COLS - (right.length() ? right.length() + 1 : 0);
  header(marquee(p->name, nameCols, scrollStep).c_str(), right);
  if (!s) {
    display.setTextSize(2);
    display.setCursor(0, 20);
    display.print("No slots");
    display.setTextSize(1);
    display.setCursor(0, 54);
    display.print(serverOk ? "none reported" : "server unreachable");
    display.display();
    return;
  }
  int ts = labelTextSize(s->label.size());
  display.setTextSize(ts);
  display.setCursor(0, ts == 3 ? 20 : 24);
  display.print(marquee(s->label, labelColumns(ts), scrollStep).c_str());
  display.setTextSize(1);
  display.setCursor(0, 54);
  display.print(marquee(slotContents(*s), OLED_COLS, scrollStep).c_str());
  display.display();
}

// Printer list: up to 4 rows around the cursor, the cursor row inverted.
void showPrinterList() {
  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);
  header("Pick printer", String(menu.cursor + 1) + "/" + String(printers.size()));
  const int rows = 4;
  int first = menu.cursor - rows / 2;
  if (first > (int)printers.size() - rows) first = (int)printers.size() - rows;
  if (first < 0) first = 0;
  for (int r = 0; r < rows && first + r < (int)printers.size(); r++) {
    int i = first + r;
    int y = 14 + r * 12;
    bool on = i == menu.cursor;
    if (on) display.fillRect(0, y - 1, OLED_W, 11, SSD1306_WHITE);
    display.setTextColor(on ? SSD1306_BLACK : SSD1306_WHITE);
    display.setCursor(2, y + 1);
    std::string name = on ? marquee(printers[i].name, OLED_COLS - 1, scrollStep)
                          : printers[i].name.substr(0, OLED_COLS - 1);
    display.print(name.c_str());
  }
  display.setTextColor(SSD1306_WHITE);
  display.display();
}

enum ActionItemId {
  ACT_ID_INFO,
  ACT_ID_RESEND,
  ACT_ID_UNLOAD,
  ACT_ID_UPDATE,
  ACT_ID_STATUS,
  ACT_ID_BACK,
};

struct ActionItem {
  const char* label;
  ActionItemId id;
};

int buildActionItems(ActionItem* out, int maxItems) {
  int n = 0;
  if (n < maxItems) out[n++] = {"Spool info", ACT_ID_INFO};
  if (lastUid.length() && n < maxItems) out[n++] = {"Re-send last tag", ACT_ID_RESEND};
  if (n < maxItems) out[n++] = {"Unload slot", ACT_ID_UNLOAD};
  if (updateReady && n < maxItems) out[n++] = {"Update FW", ACT_ID_UPDATE};
  if (n < maxItems) out[n++] = {"Status", ACT_ID_STATUS};
  if (n < maxItems) out[n++] = {"Back", ACT_ID_BACK};
  return n;
}

void showActionList() {
  ActionItem items[6];
  int n = buildActionItems(items, 6);
  if (uiCursor < 0) uiCursor = 0;
  if (uiCursor >= n) uiCursor = n - 1;
  const SlotEntry* s = curSlot();
  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);
  String right = String(uiCursor + 1) + "/" + String(n);
  size_t titleCols = OLED_COLS - right.length() - 1;
  header(marquee(s ? s->label : "Slot", titleCols, scrollStep).c_str(), right);
  const int rows = 4;
  int first = uiCursor - rows / 2;
  if (first > n - rows) first = n - rows;
  if (first < 0) first = 0;
  for (int r = 0; r < rows && first + r < n; r++) {
    int i = first + r;
    int y = 14 + r * 12;
    bool on = i == uiCursor;
    if (on) display.fillRect(0, y - 1, OLED_W, 11, SSD1306_WHITE);
    display.setTextColor(on ? SSD1306_BLACK : SSD1306_WHITE);
    display.setCursor(2, y + 1);
    display.print(items[i].label);
  }
  display.setTextColor(SSD1306_WHITE);
  display.display();
}

void showUnloadConfirm() {
  static const char* const opts[2] = {"No", "Yes"};
  const SlotEntry* s = curSlot();
  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);
  String title = s ? String("Unload ") + s->label.c_str() : String("Unload slot");
  header(title, "");
  display.setTextSize(1);
  display.setCursor(0, 16);
  display.print("Unload this slot?");
  for (int i = 0; i < 2; i++) {
    int y = 30 + i * 12;
    bool on = i == uiCursor;
    if (on) display.fillRect(0, y - 1, OLED_W, 11, SSD1306_WHITE);
    display.setTextColor(on ? SSD1306_BLACK : SSD1306_WHITE);
    display.setCursor(2, y + 1);
    display.print(opts[i]);
  }
  display.setTextColor(SSD1306_WHITE);
  display.display();
}

void redraw() {
  if (uiState == UI_ACTIONS) showActionList();
  else if (uiState == UI_CONFIRM_UNLOAD) showUnloadConfirm();
  else if (menu.mode == MODE_PRINTERS) showPrinterList();
  else showPicker();
}

// line1 on top, then either one big word (big2) or small wrapped text.
void showResult(const char* line1, const char* line2, bool big2, uint32_t holdMs = RESULT_HOLD_MS) {
  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);
  display.setTextSize(1);
  char clipped[OLED_COLS + 1];
  strncpy(clipped, line1, OLED_COLS);
  clipped[OLED_COLS] = '\0';
  display.setCursor(0, 0);
  display.print(clipped);
  display.drawFastHLine(0, 11, OLED_W, SSD1306_WHITE);
  display.setTextSize(big2 ? 2 : 1);
  display.setCursor(0, big2 ? 24 : 16);
  display.print(line2);
  display.display();
  resultUntil = millis() + holdMs;
}

// ---- UID formatting: match nfc2klipper / Spoolman extra.nfc_id --------------
// lowercase hex, NO leading zeros, colon-separated  ->  "4:c7:96:11:9:22:89"
String formatUid(const uint8_t* uid, uint8_t len) {
  String s;
  for (uint8_t i = 0; i < len; i++) {
    if (i) s += ':';
    s += String(uid[i], HEX);   // String(HEX) already drops the leading zero
  }
  return s;
}

// ---- HTTPS: one kept-alive TLS connection to the server --------------------
// Taps, list refreshes and update checks share it, so a tap skips the ~1 s TLS
// handshake while the server holds the connection open. A stale socket fails
// fast and is retried once on a fresh connection (a scan is idempotent: the same
// tag on the same slot just sets it again).
WiFiClientSecure apiTls;
HTTPClient       apiHttp;

// ---- where the Hub is -----------------------------------------------------------
// base's host is usually "<name>.local": looked up by mDNS (bounded), else the settings' hub_ip. Either way the TLS
// handshake checks the certificate against root_ca and that NAME (connect(ip, port, host, ca, ...) sets the SNI and
// the name checked; setInsecure is never used). A base whose host is not ".local" is not touched: HTTPClient connects
// by name as it always did. The address that worked is kept until a connect fails; a failed address is followed by
// the next one (the mDNS answer, then hub_ip) inside the same bound.
//
// The bound (HUB_CONNECT_BUDGET_MS or the caller's smaller one) covers the mDNS lookup and the TCP connects of all the
// addresses: NetworkClientSecure::connect(ip, port, host, ...) takes its TCP timeout from NetworkClient::_timeout
// (NetworkClientSecure.cpp:148, default 30000 from :33), which setConnectionTimeout sets (NetworkClient.cpp:349); the
// Stream::setTimeout the code used before does not reach it. The handshake that follows a TCP connect is bounded by
// sslclient->handshake_timeout (ssl_client.cpp:319, default 120000 from NetworkClientSecure.cpp:41), which
// setHandshakeTimeout(seconds) sets (NetworkClientSecure.cpp:445).
bool          mdnsUp = false;
volatile bool wifiGotIp = false;   // Wi-Fi got an address (set by the event, taken by ensureMdns)
std::string   hubIpNow;            // the address in use ("" = look it up on the next connect)
RTC_DATA_ATTR bool hasConnectedOnce = false;   // cold boot beeps on first connect; reconnects stay quiet

void onWifiGotIp(arduino_event_id_t, arduino_event_info_t) { wifiGotIp = true; }

void startMdns();
// mDNS comes up whenever Wi-Fi is up (boot or a reconnect), not only when it was up at boot.
void ensureMdns() {
  if (!mdnsNeedsStart(WiFi.status() == WL_CONNECTED, mdnsUp, wifiGotIp)) return;
  wifiGotIp = false;
  if (mdnsUp) MDNS.end();
  startMdns();
}

bool mdnsLookup(const std::string& host, uint32_t timeoutMs, std::string& ip) {
  ensureMdns();
  if (!mdnsUp) return false;
  std::string name = host;
  if (!name.empty() && name.back() == '.') name.pop_back();
  name.erase(name.size() - 6);   // ".local": the library takes the bare name
  IPAddress a = MDNS.queryHost(name.c_str(), timeoutMs);
  if (a == IPAddress((uint32_t)0)) return false;
  ip = a.toString().c_str();
  return true;
}

void startMdns() {
  String name = "fk-reader-" + String((uint32_t)(ESP.getEfuseMac() >> 24) & 0xFFFFFF, HEX);
  mdnsUp = MDNS.begin(name.c_str());
}

// Opens `tls` to the Hub when base needs the lookup above and `tls` is not connected yet; true when HTTPClient may go
// on (it reuses a connected client). Call after http.begin(tls, url): begin() drops a connection made before it.
// budgetMs bounds the whole of it (lookup and TCP connects); a peer that takes the TCP connection and then says
// nothing is cut by the handshake limit (HANDSHAKE_S) on top.
bool hubConnect(WiFiClientSecure& tls, uint32_t budgetMs) {
  BaseParts bp = parseBase(cfg.base);
  if (!bp.ok || !isLocalName(bp.host)) return true;      // old behaviour: by name, in HTTPClient
  ensureMdns();
  if (tls.connected()) return true;
  uint32_t t0 = millis();
  std::string hubIp = g_rcfg.hubIp[0] ? std::string(g_rcfg.hubIp) : cfg.hubIp;
  std::vector<HubAddress> list = hubAddresses(cfg.base, hubIp, hubIpNow, mdnsSliceMs(budgetMs), mdnsLookup);
  tls.setHandshakeTimeout(HANDSHAKE_S);
  for (size_t i = 0; i < list.size(); i++) {
    uint32_t ms = connectSliceMs(budgetMs, millis() - t0, list.size() - i);
    IPAddress ip;
    if (!ms || !ip.fromString(list[i].ip.c_str())) continue;
    tls.setConnectionTimeout(ms);
    if (tls.connect(ip, list[i].port, list[i].host.c_str(), rootCa(), nullptr, nullptr)) {
      if (list[i].ip != hubIpNow)
        Serial.printf("hub %s -> %s (%s)\n", bp.host.c_str(), list[i].ip.c_str(), list[i].source == ADDR_MDNS ? "mDNS" : "hub_ip");
      hubIpNow = list[i].ip;
      return true;
    }
    char tlsErr[120] = "";
    int tlsCode = tls.lastError(tlsErr, sizeof(tlsErr));
    Serial.printf("hub %s: %s did not answer (mbedtls=%d %s, ip=%s, rssi=%d)\n",
                  bp.host.c_str(), list[i].ip.c_str(), tlsCode, tlsErr,
                  WiFi.localIP().toString().c_str(), WiFi.RSSI());
  }
  if (list.empty()) Serial.printf("hub %s: no address\n", bp.host.c_str());
  hubIpNow.clear();                                      // look it up again on the next connect
  return false;
}

// A String sink that refuses bytes past `cap`, so a huge (or endless chunked)
// reply cannot exhaust the heap; HTTPClient::writeToStream stops on the refusal.
class CappedString : public Stream {
 public:
  CappedString(String& s, size_t cap) : s_(s), cap_(cap) {}
  bool over = false;
  size_t write(uint8_t c) override { return write(&c, 1); }
  size_t write(const uint8_t* b, size_t n) override {
    if (s_.length() + n > cap_) { over = true; return 0; }
    s_.concat((const char*)b, n);
    return n;
  }
  int available() override { return 0; }
  int read() override { return -1; }
  int peek() override { return -1; }
 private:
  String& s_;
  size_t cap_;
};

// quick: one short attempt (background refreshes and taps), see LIST_QUICK_TIMEOUT_MS.
// boundMs: one attempt with that connect and read time each (the Hub check at start, HUB_CHECK_MS).
int apiRequest(const String& url, const char* body, String& resp, bool quick = false, uint32_t boundMs = 0) {
  int code = -1;
  uint32_t readMs = boundMs ? boundMs : quick ? LIST_QUICK_TIMEOUT_MS : 12000;
  uint32_t connectMs = boundMs ? boundMs : quick ? LIST_QUICK_TIMEOUT_MS : 10000;
  int maxAttempts = (quick || boundMs) ? 1 : 2;
  for (int attempt = 0; attempt < maxAttempts; attempt++) {
    bool reused = apiTls.connected();
    apiHttp.setReuse(true);
    apiHttp.setTimeout(readMs);
    apiHttp.setConnectTimeout(connectMs);
    if (!apiHttp.begin(apiTls, url) || !hubConnect(apiTls, connectMs)) {
      apiHttp.end(); apiTls.stop();
      if (reused && maxAttempts == 1) maxAttempts = 2;
      continue;
    }
    apiHttp.addHeader("X-Auth-Token", cfg.token.c_str());
    char buildHdr[24];
    snprintf(buildHdr, sizeof(buildHdr), "%lld", (long long)FW_BUILD);
    apiHttp.addHeader("X-Reader-Build", buildHdr);
    if (body) { apiHttp.addHeader("Content-Type", "application/json"); code = apiHttp.POST(body); }
    else      code = apiHttp.GET();
    if (code <= 0) {
      char tlsErr[120] = "";
      int tlsCode = apiTls.lastError(tlsErr, sizeof(tlsErr));
      Serial.printf("api %s failed: %d | mbedtls=%d %s | ip=%s | WiFi %s RSSI %d\n",
                    url.c_str(), code, tlsCode, tlsErr, WiFi.localIP().toString().c_str(),
                    WiFi.status() == WL_CONNECTED ? "up" : "DOWN", WiFi.RSSI());
    }
    if (code > 0) {
      resp = "";
      int len = apiHttp.getSize();          // -1 = unknown (chunked)
      if (responseTooBig(len)) {
        Serial.printf("%s: reply of %d bytes refused\n", url.c_str(), len);
        apiHttp.end(); apiTls.stop();
        return HTTP_TOO_BIG;
      }
      if (len > 0) resp.reserve(len);
      CappedString sink(resp, MAX_RESPONSE_BYTES);
      int r = apiHttp.writeToStream(&sink);
      if (r < 0 || sink.over) {
        Serial.printf("%s: reply over %u bytes or cut (%d)\n", url.c_str(), (unsigned)MAX_RESPONSE_BYTES, r);
        resp = "";
        apiHttp.end(); apiTls.stop();
        return sink.over ? HTTP_TOO_BIG : r;
      }
      apiHttp.end();
      return code;
    }
    apiHttp.end(); apiTls.stop();
    if (reused && maxAttempts == 1) maxAttempts = 2;
  }
  return code;
}

// ---- remembered choice (NVS: survives reboots and OTA updates) --------------
// The printer by id and the slot by LABEL, so a layout change (an AMS added or
// removed) keeps the choice whenever that label still exists.
Preferences prefs;
std::string savedPrinter, savedLabel;
void savePrinter(const std::string& id) {
  if (id == savedPrinter) return;
  prefs.putString("printer", id.c_str());
  savedPrinter = id;
}
void saveSlot(const std::string& label) {
  if (label == savedLabel) return;
  prefs.putString("slot_label", label.c_str());
  savedLabel = label;
}

// ---- printer + slot lists ---------------------------------------------------
uint32_t listsAt = 0;       // when the last refresh ended
int      listFails = 0;     // failed refreshes in a row

// Slots of the selected printer; keeps `label` selected when it still exists.
// A failed fetch keeps the last good list only when it is this printer's.
bool fetchSlots(const std::string& label, bool quick) {
  const PrinterEntry* p = curPrinter();
  if (!p) { slotCache.apply("", false, {}); menu.slot = 0; return false; }
  String resp;
  String url = joinUrl(cfg.base.c_str(), "printers/" + urlEncode(p->id) + "/slots").c_str();
  int code = apiRequest(url, nullptr, resp, quick);
  std::vector<SlotEntry> fresh;
  bool ok = code == 200 && parseSlots(resp.c_str(), fresh);
  resp = "";                                   // free the text before keeping the list
  if (!ok) Serial.printf("slots %s: HTTP %d\n", p->id.c_str(), code);
  slotCache.apply(p->id, ok, std::move(fresh));
  if (ok) {
    int i = findSlot(slots, label);
    menu.slot = i < 0 ? 0 : i;
  }
  return ok;
}

uint32_t settleAt = 0;   // when to save the selection after the knob stops (0 = none)

// Both lists. Re-selects the SAVED printer and slot label (the current ones only
// while a knob turn is not saved yet), so a unit that drops out for one refresh
// does not move the choice for good. On a failure the last good lists stay.
bool refreshListsNow(bool quick, uint32_t boundMs = 0) {
  if (WiFi.status() != WL_CONNECTED) { serverOk = false; return false; }
  std::string wantPrinter = !savedPrinter.empty() ? savedPrinter
                                                  : (g_rcfg.defaultPrinter[0] ? std::string(g_rcfg.defaultPrinter)
                                                                              : (curPrinter() ? curPrinter()->id : std::string()));
  std::string wantLabel = wantedLabel(savedLabel, curSlot() ? curSlot()->label : std::string(),
                                      settleAt != 0 && menu.mode == MODE_SLOTS);
  String resp;
  int code = apiRequest(urlPrinters, nullptr, resp, quick, boundMs);
  boot.server(code);
  if (boundMs) Serial.println(helloLine((int64_t)FW_BUILD, true, boot.wifiText(), boot.serverText()).c_str());   // the Hub's answer, as soon as it is known
  bool ok = code == 200 && parsePrinters(resp.c_str(), printers);
  resp = "";
  if (!ok) {
    Serial.printf("printers: HTTP %d\n", code);
    serverOk = false;
    return false;
  }
  int i = findPrinter(printers, wantPrinter);
  menu.printer = i < 0 ? 0 : i;
  serverOk = fetchSlots(wantLabel, quick || boundMs != 0);
  return serverOk;
}

// listsAt is stamped when the refresh ENDS, so a slow failing one cannot be due again
// at once; listFails drives the retry back-off (listRetryMs).
// quick: one short attempt, used for retries and taps so a dead server cannot hold the loop.
void refreshLists(bool quick = false, uint32_t boundMs = 0) {
  bool ok = refreshListsNow(quick, boundMs);
  listsAt = millis();
  listFails = ok ? 0 : listFails + 1;
}

// ---- POST a scan --------------------------------------------------------------
void showProgress(const char* title, const char* line, int pct);
void sendScan(const String& uid) {
  const PrinterEntry* p = curPrinter();
  const SlotEntry* s = curSlot();
  if (WiFi.status() != WL_CONNECTED) { showResult("Scan", "No WiFi", true); buzzError(); return; }
  if (!p || !s) {
    // No usable slot (server was down at the last refresh): one quick refresh before
    // refusing, unless one failed a moment ago. The scan is NOT posted after it: the
    // refresh may have moved the choice, so the user sees it first and taps again.
    if (!(listFails > 0 && millis() - listsAt < TAP_RECHECK_MS)) {
      showResult("Scan", "Checking server...", false);
      refreshLists(true);
      if (curPrinter() && curSlot()) {
        showResult("Server back", "check printer and slot,\ntap again", false);
        buzzTick();
        tagGate.requireLift();            // a tag left on the reader must not post
        return;
      }
    }
    tagGate.requireLift();                // no repeat refusals from a held tag
    showResult("Scan", "No slot", true); buzzError(); return;
  }
  // copy: a refresh after this call must not pull the strings out from under us
  PrinterEntry printer = *p;
  SlotEntry slot = *s;

  buzzTick();
  showProgress(slot.label.c_str(), "Scanning...", -1);

  std::string payload = scanBody(uid.c_str(), printer.id, slot.label);
  String resp;
  uint32_t t0 = millis();
  int code = apiRequest(urlScan, payload.c_str(), resp);
  Serial.printf("scan round trip %lu ms\n", millis() - t0);
  if (code <= 0) {
    char tlsErr[120] = "";
    int tlsCode = apiTls.lastError(tlsErr, sizeof(tlsErr));
    Serial.printf("POST failed: %d %s | mbedtls=%d %s | ip=%s | WiFi %s RSSI %d | heap %u\n", code,
                  HTTPClient::errorToString(code).c_str(), tlsCode, tlsErr, WiFi.localIP().toString().c_str(),
                  WiFi.status() == WL_CONNECTED ? "up" : "DOWN", WiFi.RSSI(), ESP.getFreeHeap());
  } else {
    Serial.printf("POST %d: %s\n", code, resp.c_str());
  }

  ScanResult r = parseScanResult(code, resp.c_str());
  char head[48];
  snprintf(head, sizeof(head), "%s  %s", slot.label.c_str(), headline(r.status));
  switch (feedbackFor(r.status)) {
    case FB_OK: {
      const std::string& what = !r.filament.empty() ? r.filament : r.message;
      showResult(head, what.empty() ? "set" : what.c_str(), false);
      buzzOk();
      listsAt = millis() - LIST_REFRESH_MS + LIST_AFTER_SET_MS;   // re-read contents in ~5 s
      break;
    }
    case FB_QUEUED: {
      // Empty slot: the server holds the setting and applies it once the printer
      // reads the loaded filament. A success, just pending.
      char line1[48], line2[96];
      snprintf(line1, sizeof(line1), "Load into %s", slot.label.c_str());
      snprintf(line2, sizeof(line2), "%s\nwill set when loaded",
               !r.filament.empty() ? r.filament.c_str() : "filament");
      showResult(line1, line2, false, QUEUED_HOLD_MS);
      buzzQueued();
      break;
    }
    case FB_UNKNOWN:
      showResult(head, r.message.empty() ? headline(r.status) : r.message.c_str(), r.message.empty());
      buzzUnknown();
      break;
    default:
      showResult(head, r.message.empty() ? headline(r.status) : r.message.c_str(), r.message.empty());
      buzzError();
      break;
  }
}

// ---- POST an unload -----------------------------------------------------------
void sendUnload() {
  const PrinterEntry* p = curPrinter();
  const SlotEntry* s = curSlot();
  if (WiFi.status() != WL_CONNECTED) { showResult("Unload", "No WiFi", true); buzzError(); return; }
  if (!p || !s) { showResult("Unload", "No slot", true); buzzError(); return; }
  PrinterEntry printer = *p;
  SlotEntry slot = *s;
  showProgress(slot.label.c_str(), "Unloading...", -1);
  std::string payload = unloadBody(printer.id, slot.label);
  String resp;
  int code = apiRequest(urlUnload, payload.c_str(), resp);
  ScanResult r = parseScanResult(code, resp.c_str());
  char head[48];
  snprintf(head, sizeof(head), "%s  %s", slot.label.c_str(), headline(r.status));
  if (r.status == SCAN_UNLOADED) {
    const std::string& what = !r.message.empty() ? r.message : r.filament;
    showResult(head, what.empty() ? "unloaded" : what.c_str(), false);
    buzzOk();
    listsAt = millis() - LIST_REFRESH_MS + LIST_AFTER_SET_MS;
  } else {
    showResult(head, r.message.empty() ? headline(r.status) : r.message.c_str(), r.message.empty());
    buzzError();
  }
}

// ---- rotary encoder (interrupt-driven quadrature) --------------------------
// Full-step Gray-code transition table, indexed [prevCLKDT<<2 | currCLKDT]:
// sums valid quarter-steps (4 per detent); invalid transitions return 0, which
// gives free contact debounce.
static const int8_t ENC_TABLE[16] = {
   0, -1,  1,  0,
   1,  0,  0, -1,
  -1,  0,  0,  1,
   0,  1, -1,  0
};
volatile uint8_t encState   = 0;    // last (CLK,DT) in the low 2 bits
volatile int8_t  encAccum   = 0;    // quarter-steps since last detent
volatile int16_t encDetents = 0;    // whole detents waiting for loop()

void IRAM_ATTR encISR() {
  encState = ((encState << 2) |
              (digitalRead(PIN_ENC_CLK) << 1) |
               digitalRead(PIN_ENC_DT)) & 0x0F;
  encAccum += ENC_TABLE[encState];
  if      (encAccum >=  4) { encDetents++; encAccum = 0; }
  else if (encAccum <= -4) { encDetents--; encAccum = 0; }
}

bool swPressed() { return digitalRead(PIN_ENC_SW) == (ENC_SW_TO_VCC ? HIGH : LOW); }

// ---- OTA --------------------------------------------------------------------
// Manifest {"build": <unix time>, "size": N, "sha256": "<hex>", "sig": "<base64 DER>",
// "sig2": "<base64 DER>"}. The image is streamed into the inactive OTA slot while
// hashing; it's only committed if size, SHA-256 and the ECDSA signature sig2 (over
// the SHA-256 together with the build number, so an older signed image can't be
// served as a newer build) all check out. "sig" (hash only) is for readers flashed
// before sig2 and is ignored here. The new image boots "pending verify" and marks itself valid once
// Wi-Fi is up and <READER_BASE>/healthz answers 200 (see confirmOtaImage);
// otherwise the bootloader rolls back to the previous image on the next reset.
// core: don't auto-mark the app valid. extern "C": the core's weak default is C.
extern "C" bool verifyRollbackLater() { return true; }

void showProgress(const char* title, const char* line, int pct) {
  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);
  display.setTextSize(1);
  char clipped[OLED_COLS + 1];
  strncpy(clipped, title, OLED_COLS);
  clipped[OLED_COLS] = '\0';
  display.setCursor(0, 0);  display.print(clipped);
  display.drawFastHLine(0, 11, OLED_W, SSD1306_WHITE);
  display.setCursor(0, 20); display.print(line);
  if (pct >= 0) {
    display.drawRect(0, 44, OLED_W, 12, SSD1306_WHITE);
    display.fillRect(2, 46, (OLED_W - 4) * pct / 100, 8, SSD1306_WHITE);
  }
  display.display();
}

bool httpsGet(HTTPClient& http, WiFiClientSecure& tls, const String& url) {
  tls.setCACert(rootCa());
  http.setTimeout(15000);
  if (!http.begin(tls, url) || !hubConnect(tls, 15000)) return false;
  http.addHeader("X-Auth-Token", cfg.token.c_str());
  return http.GET() == 200;
}

// sig2 signs SHA-256("fk-reader-ota\x01" || image SHA-256 || build, 8 bytes big-endian);
// tools/ota_publish.py build_digest() makes the same bytes (otaSigPreimage, pinned by a test on both sides).
bool verifySig(const uint8_t imgHash[32], int64_t build, const char* sigB64) {
  std::string pre = otaSigPreimage(imgHash, build);
  uint8_t hash[32];
  mbedtls_sha256_context c; mbedtls_sha256_init(&c); mbedtls_sha256_starts(&c, 0);
  mbedtls_sha256_update(&c, (const uint8_t*)pre.data(), pre.size());
  mbedtls_sha256_finish(&c, hash); mbedtls_sha256_free(&c);
  uint8_t sig[80]; size_t sigLen = 0;
  if (mbedtls_base64_decode(sig, sizeof(sig), &sigLen, (const uint8_t*)sigB64, strlen(sigB64)) != 0)
    return false;
  if (cfg.otaKey.empty()) return false;          // no key was set up: every update is refused
  mbedtls_pk_context pk; mbedtls_pk_init(&pk);
  bool ok = mbedtls_pk_parse_public_key(&pk, (const uint8_t*)cfg.otaKey.c_str(), cfg.otaKey.size() + 1) == 0 &&
            mbedtls_pk_verify(&pk, MBEDTLS_MD_SHA256, hash, 32, sig, sigLen) == 0;
  mbedtls_pk_free(&pk);
  return ok;
}

void otaFail(const char* why) {
  Serial.printf("OTA failed: %s\n", why);
  showResult("Update failed", why, false);
  buzzError();
}

struct Manifest { int64_t build = 0; size_t size = 0; String sha, sig; };   // sig = the manifest's sig2
bool fetchManifest(Manifest& m) {
  String resp;
  if (apiRequest(urlManifest, nullptr, resp) != 200) return false;
  JsonDocument j;
  if (deserializeJson(j, resp)) return false;
  m.build = j["build"] | 0LL; m.size = j["size"] | 0;
  m.sha = (const char*)(j["sha256"] | ""); m.sig = (const char*)(j["sig2"] | "");
  return true;
}

// Quiet check (boot + daily): just flags "UPD" on the picker; installing stays
// a deliberate knob hold.
uint32_t updCheckAt = 0;
static const uint32_t UPD_CHECK_MS = 24UL * 3600 * 1000;
void checkForUpdate() {
  updCheckAt = millis();
  if (cfg.otaKey.empty()) { updateReady = false; return; }
  Manifest m;
  if (WiFi.status() == WL_CONNECTED && fetchManifest(m)) {
    updateReady = m.build > (int64_t)FW_BUILD;
    Serial.printf("update check: running %lld, server %lld%s\n", (long long)FW_BUILD, (long long)m.build,
                  updateReady ? " -> UPDATE READY" : "");
  }
}

void otaCheck() {
  if (cfg.otaKey.empty()) { otaFail("no OTA key"); return; }
  if (WiFi.status() != WL_CONNECTED) { otaFail("no WiFi"); return; }
  showProgress("Firmware update", "checking...", -1);

  Manifest m;
  if (!fetchManifest(m)) { otaFail("no manifest"); return; }
  int64_t build = m.build; size_t size = m.size; String shaHex = m.sha, sigB64 = m.sig;
  Serial.printf("OTA: running build %lld, server build %lld (%u bytes)\n", (long long)FW_BUILD, (long long)build, size);
  apiTls.stop();   // free the kept-alive TLS session's heap for the download
  if (build <= (int64_t)FW_BUILD) {
    updateReady = false;
    char l[40]; snprintf(l, sizeof(l), "up to date\nbuild %lld", (long long)FW_BUILD);
    showResult("Firmware update", l, false); buzzOk();
    return;
  }
  if (!size || shaHex.length() != 64 || !sigB64.length()) { otaFail("bad manifest"); return; }
  if (!Update.begin(size)) { otaFail("too big / no slot"); return; }

  WiFiClientSecure tls; HTTPClient http;
  if (!httpsGet(http, tls, urlBin) || (size_t)http.getSize() != size) {
    http.end(); Update.abort(); otaFail("download"); return;
  }
  mbedtls_sha256_context sha; mbedtls_sha256_init(&sha); mbedtls_sha256_starts(&sha, 0);
  WiFiClient* s = http.getStreamPtr();
  std::vector<uint8_t> buf(2048);
  size_t got = 0; int lastPct = -1; uint32_t lastData = millis();
  while (got < size) {
    size_t avail = s->available();
    if (!avail) {
      if (!http.connected() || millis() - lastData > 15000) break;
      delay(2); continue;
    }
    int n = s->readBytes(buf.data(), min(avail, min(buf.size(), size - got)));
    if (n <= 0) continue;
    lastData = millis();
    mbedtls_sha256_update(&sha, buf.data(), n);
    if (Update.write(buf.data(), n) != (size_t)n) break;
    got += n;
    int pct = got * 100 / size;
    if (pct != lastPct) {
      lastPct = pct;
      char l[32]; snprintf(l, sizeof(l), "downloading %d%%", pct);
      showProgress("Firmware update", l, pct);
    }
  }
  http.end();
  uint8_t hash[32]; mbedtls_sha256_finish(&sha, hash); mbedtls_sha256_free(&sha);
  if (got != size) { Update.abort(); otaFail("download cut"); return; }

  char hex[65];
  for (int i = 0; i < 32; i++) sprintf(hex + 2 * i, "%02x", hash[i]);
  if (!shaHex.equalsIgnoreCase(hex)) { Update.abort(); otaFail("hash mismatch"); return; }
  if (!verifySig(hash, build, sigB64.c_str())) { Update.abort(); otaFail("bad signature"); return; }
  if (!Update.end(true)) { otaFail(Update.errorString()); return; }

  showProgress("Firmware update", "installed\nrestarting...", 100);
  buzzOk();
  // Reboot only now, with no I2C transfer in flight, so the PN532 isn't left
  // holding SCL (it has no reset line we can drive).
  delay(500);
  ESP.restart();
}

void persistCompiledSettings() {
  if (!pendingCompiledSave) return;
  pendingCompiledSave = false;
  if (saveSettings(cfg)) {
    Preferences w;
    if (w.begin("fk-cfg", false)) { w.putString("src_stamp", settingsStamp(cfg).c_str()); w.end(); }
  }
}

// New OTA image: keep it only if it can reach the server's reader API.
static const uint32_t CONFIRM_WINDOW_MS = 60000;   // how long to keep trying /healthz
static const uint32_t CONFIRM_RETRY_MS  = 2000;
void confirmOtaImage() {
  const esp_partition_t* run = esp_ota_get_running_partition();
  esp_ota_img_states_t st;
  if (esp_ota_get_state_partition(run, &st) != ESP_OK) st = ESP_OTA_IMG_UNDEFINED;
  Serial.printf("running %s, OTA state %d\n", run->label, (int)st);
  if (st != ESP_OTA_IMG_PENDING_VERIFY) { persistCompiledSettings(); return; }
  bool ok = false;
  showProgress("Firmware update", "checking update...", -1);   // this can wait up to CONFIRM_WINDOW_MS
  // A slow router or a server restarting right now must not roll back a healthy build:
  // keep trying for CONFIRM_WINDOW_MS (the bootloader's rollback is the fallback after that).
#ifndef OTA_TEST_FAIL_HEALTH  // rollback drill: PLATFORMIO_BUILD_FLAGS=-DOTA_TEST_FAIL_HEALTH
  for (uint32_t t0 = millis(); !ok && millis() - t0 < CONFIRM_WINDOW_MS; ) {
    if (WiFi.status() == WL_CONNECTED) {
      String resp;
      ok = apiRequest(urlHealthz, nullptr, resp) == 200;
    }
    if (!ok) delay(CONFIRM_RETRY_MS);
  }
#endif
  if (ok) {
    esp_ota_mark_app_valid_cancel_rollback();
    persistCompiledSettings();
    Serial.printf("OTA image build %lld confirmed\n", (long long)FW_BUILD);
  } else {
    Serial.println("OTA image failed its health check -> rolling back");
    showProgress("Firmware update", "new build failed\nrolling back...", -1);
    buzzError();
    delay(1500);
    esp_ota_mark_app_invalid_rollback_and_reboot();
  }
}

uint32_t rcfgSyncAt = 0;
uint32_t otaBackoffUntil = 0;
void syncRemoteConfig() {
  rcfgSyncAt = millis();
  if (WiFi.status() != WL_CONNECTED) return;
  String resp;
  if (apiRequest(urlConfig, nullptr, resp, true) != 200) return;
  RemoteConfig next;
  if (!parseRemoteConfig(resp.c_str(), next, cfg.buzzerLow)) return;
  bool changed = next.configVersion != g_rcfg.configVersion;
  bool buzzerLowChanged = next.buzzerLow != g_rcfg.buzzerLow;
  g_rcfg = next;
  if (changed) {
    saveRemoteConfig(g_rcfg);
    Serial.printf("remote config v%lu applied\n", (unsigned long)g_rcfg.configVersion);
  }
  if (buzzerLowChanged) buzzIdle();
  bool autoOtaDue = g_rcfg.autoOta && updateReady &&
                    (!otaBackoffUntil || (int32_t)(millis() - otaBackoffUntil) >= 0);
  if (g_rcfg.otaNow || autoOtaDue) {
    String ackResp;
    apiRequest(urlConfigAck, R"({"cmd":"ota"})", ackResp, true);
    otaCheck();
    if (updateReady) {
      uint32_t nextTry = millis() + 15UL * 60 * 1000;
      otaBackoffUntil = nextTry ? nextTry : 1;
    } else {
      otaBackoffUntil = 0;
    }
    return;
  }
  if (g_rcfg.rebootNow) {
    String ackResp;
    apiRequest(urlConfigAck, R"({"cmd":"reboot"})", ackResp, true);
    Serial.println("remote reboot requested");
    Serial.flush();
    delay(200);
    ESP.restart();
  }
}

// ---- screen sleep (OLED burn-in) --------------------------------------------
// Dim after 2 min idle, off after 10. The first knob turn / press on a dark
// screen only wakes it; a tag tap wakes it and is processed as usual.
static const uint32_t DIM_AFTER_MS = 2UL * 60 * 1000;
static const uint32_t OFF_AFTER_MS = 10UL * 60 * 1000;
enum Screen { SCR_ON, SCR_DIM, SCR_OFF };
Screen   scr = SCR_ON;
uint32_t lastActivity = 0;
bool wake() {                       // returns true if the screen was off
  lastActivity = millis();
  bool wasOff = scr == SCR_OFF;
  if (wasOff) display.ssd1306_command(SSD1306_DISPLAYON);
  if (scr != SCR_ON) display.dim(false);
  scr = SCR_ON;
  return wasOff;
}
void screenIdle() {
  uint32_t idle = millis() - lastActivity;
  uint32_t dimMs = (uint32_t)g_rcfg.dimAfterS * 1000UL;
  uint32_t offMs = (uint32_t)g_rcfg.offAfterS * 1000UL;
  if (scr != SCR_OFF && idle > offMs) { display.ssd1306_command(SSD1306_DISPLAYOFF); scr = SCR_OFF; }
  else if (scr == SCR_ON && idle > dimMs) { display.dim(true); scr = SCR_DIM; }
}

// Print every I2C address that ACKs (PN532 = 0x24, OLED = 0x3C).
void i2cScan() {
  Serial.print("I2C scan:");
  int n = 0;
  for (uint8_t a = 1; a < 127; a++) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) { Serial.printf(" 0x%02X", a); n++; }
  }
  Serial.println(n ? "" : " nothing answered");
}

void setup() {
  // An FK-SET line can be MAX_LINE bytes (a root CA and an OTA key) and arrives while the loop is busy elsewhere:
  // the USB serial's default receive buffer is a few hundred bytes. Before begin(), or it keeps the default.
  Serial.setRxBufferSize(MAX_LINE + 64);
  Serial.begin(115200);
  loadSettings();
  loadRemoteConfig();
  pinMode(PIN_ENC_CLK, INPUT_PULLUP);
  pinMode(PIN_ENC_DT,  INPUT_PULLUP);
  pinMode(PIN_ENC_SW,  ENC_SW_TO_VCC ? INPUT_PULLDOWN : INPUT_PULLUP);
  buzzIdle();
  // seed encoder state and watch both quadrature lines for edges
  encState = ((digitalRead(PIN_ENC_CLK) << 1) | digitalRead(PIN_ENC_DT)) & 0x03;
  attachInterrupt(digitalPinToInterrupt(PIN_ENC_CLK), encISR, CHANGE);
  attachInterrupt(digitalPinToInterrupt(PIN_ENC_DT),  encISR, CHANGE);

  urlPrinters = joinUrl(cfg.base.c_str(), "printers").c_str();
  urlScan     = joinUrl(cfg.base.c_str(), "scan").c_str();
  urlUnload   = joinUrl(cfg.base.c_str(), "unload").c_str();
  urlHealthz  = joinUrl(cfg.base.c_str(), "healthz").c_str();
  urlManifest  = joinUrl(cfg.base.c_str(), "fw/manifest.json").c_str();
  urlBin       = joinUrl(cfg.base.c_str(), "fw/firmware.bin").c_str();
  urlConfig    = joinUrl(cfg.base.c_str(), "config").c_str();
  urlConfigAck = joinUrl(cfg.base.c_str(), "config/ack").c_str();

  // Idle I2C lines must read HIGH (pulled up). LOW with no internal pull-up means
  // no pull-up resistors; LOW even with the pull-up means something holds it down.
  pinMode(PIN_SDA, INPUT); pinMode(PIN_SCL, INPUT); delay(5);
  int sdaF = digitalRead(PIN_SDA), sclF = digitalRead(PIN_SCL);
  pinMode(PIN_SDA, INPUT_PULLUP); pinMode(PIN_SCL, INPUT_PULLUP); delay(5);
  Serial.printf("I2C idle: SDA=%d SCL=%d (no pull)  SDA=%d SCL=%d (internal pull-up)\n",
                sdaF, sclF, digitalRead(PIN_SDA), digitalRead(PIN_SCL));
  // A PN532 left mid-transfer by a C3-only reset can hold SCL low; only a power
  // cycle clears it (RSTPDN isn't on the header). Say so instead of hanging.
  uint32_t w0 = millis();
  while (digitalRead(PIN_SCL) == LOW && millis() - w0 < 500) delay(1);
  if (digitalRead(PIN_SCL) == LOW) {
    Serial.println("I2C SCL stuck LOW (PN532 wedged?) -> unplug USB to power-cycle");
    buzzStuck();                   // the OLED shares the stuck bus: two long beeps (and serial) say it
  }
  Wire.begin(PIN_SDA, PIN_SCL);
  i2cScan();

  if (!display.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR)) {
    Serial.println("SSD1306 init failed");
  }
  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);
  display.setTextSize(1);
  display.setCursor(0, 0);
  display.print("filament-kit reader\nconnecting WiFi...");
  display.display();

  nfc.begin();
  uint32_t ver = nfc.getFirmwareVersion();
  nfcOk = (ver != 0);
  if (!ver) Serial.println("PN532 not found");
  else {
    Serial.printf("PN532 found: chip PN5%02X fw %d.%d\n",
                  (ver >> 24) & 0xFF, (ver >> 16) & 0xFF, (ver >> 8) & 0xFF);
    nfc.SAMConfig();
  }

  Serial.printf("firmware build %lld\n", (long long)FW_BUILD);
  if (!configured) {
    const esp_partition_t* run = esp_ota_get_running_partition();
    esp_ota_img_states_t st;
    if (esp_ota_get_state_partition(run, &st) == ESP_OK && st == ESP_OTA_IMG_PENDING_VERIFY) {
      Serial.println("unconfigured OTA image pending verify -> rolling back");
      showProgress("Firmware update", "unconfigured\nrolling back...", -1);
      buzzError();
      delay(1500);
      esp_ota_mark_app_invalid_rollback_and_reboot();
    }
    // A new board from the kit's image: nothing to join yet. Wait for the kit to write the settings over USB.
    auto drawUnconfigured = [&]() {
      display.clearDisplay();
      display.setTextColor(SSD1306_WHITE);
      display.setTextSize(1);
      display.setCursor(0, 0);
      display.print("Not set up yet.\nPlug me into the kit\nserver and run:\nreader setup\n(or the setup page)");
      display.display();
    };
    drawUnconfigured();
    Serial.println(helloLine((int64_t)FW_BUILD, false, "-", "-").c_str());
    lastActivity = millis();
    for (;;) {
      serialPoll();
      screenIdle();
      if (button.update(swPressed(), millis()) != BTN_NONE) {
        if (wake()) drawUnconfigured();
      }
      delay(10);
    }
  }

  WiFi.mode(WIFI_STA);
  WiFi.onEvent(onWifiGotIp, ARDUINO_EVENT_WIFI_STA_GOT_IP);
  WiFi.begin(cfg.ssid.c_str(), cfg.pass.c_str());
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 15000) { serialPoll(); delay(250); }
  boot.wifi(WiFi.status() == WL_CONNECTED);
  // Wi-Fi is known before the Hub is called: say so now (the Hub check below can take its full bound).
  Serial.println(helloLine((int64_t)FW_BUILD, true, boot.wifiText(), boot.serverText()).c_str());
  ensureMdns();

  apiTls.setCACert(rootCa());
  confirmOtaImage();
  prefs.begin("reader", false);
  savedPrinter = prefs.getString("printer", "").c_str();
  if (printerNew) {   // set up with a printer just now: start on it, the knob can change it later
    savePrinter(startPrinter(savedPrinter, cfg.printer, printerNew));
    Preferences w;
    if (w.begin("fk-cfg", false)) { w.putBool("printer_new", false); w.end(); }
    printerNew = false;
  }
  savedLabel   = prefs.getString("slot_label", "").c_str();
  if (prefs.isKey("slot")) prefs.remove("slot");   // the H2D-only firmware's slot index
  uint32_t reachStart = millis();
  refreshLists(false, HUB_CHECK_MS);   // bounded; reports server= as soon as the Hub answers
  while (WiFi.status() == WL_CONNECTED && !serverOk && millis() - reachStart < SETUP_REACH_WINDOW_MS) {
    serialPoll();
    delay(HUB_CHECK_RETRY_MS);
    refreshLists(false, HUB_CHECK_MS);
  }
  checkForUpdate();
  syncRemoteConfig();
  lastActivity = millis();
  redraw();
  if (WiFi.status() != WL_CONNECTED || !serverOk) buzzError();
  else if (!hasConnectedOnce) { hasConnectedOnce = true; buzzOk(); }
  Serial.println(helloLine((int64_t)FW_BUILD, true, boot.wifiText(), boot.serverText()).c_str());
}

bool swallowPress = false;  // this press woke a dark screen: ignore the rest of it

void handleAction(Action a) {
  switch (a) {
    case ACT_SLOT_MOVED:
      buzzTick(); scrollStep = 0; redraw();
      settleAt = millis() + SETTLE_MS;        // re-armed by every detent
      break;
    case ACT_CURSOR_MOVED:
      buzzTick(); scrollStep = 0; redraw();
      break;
    case ACT_RESEND:
      buzzTick();
      if (lastUid.length() && curSlot()) {
        Serial.printf("re-send %s -> %s\n", lastUid.c_str(), curSlot()->label.c_str());
        sendScan(lastUid);
      } else {
        redraw();
      }
      break;
    case ACT_OPEN_LIST:
      buzzTick(); scrollStep = 0; redraw();
      break;
    case ACT_CLOSE_LIST:
      settleAt = 0; scrollStep = 0; redraw();
      break;
    case ACT_PICK_PRINTER:
      buzzTick(); settleAt = 0; scrollStep = 0;
      if (curPrinter()) savePrinter(curPrinter()->id);
      showProgress(curPrinter() ? curPrinter()->name.c_str() : "Printer", "loading slots...", -1);
      serverOk = fetchSlots(savedLabel, false);   // fails -> no slots, no scans (never the old printer's)
      listsAt = millis();
      redraw();
      break;
    case ACT_OTA:
      buzzTick(); otaCheck();
      break;
    case ACT_NO_PRINTERS:
      showResult("Printers", serverOk ? "none on the server" : "server unreachable", false);
      buzzError();
      break;
    case ACT_OPEN_ACTIONS:
      buzzTick(); scrollStep = 0; redraw();
      break;
    case ACT_ACTION_SP_INFO:
      buzzTick();
      if (curSlot()) {
        showResult(curSlot()->label.c_str(), spoolInfoText(*curSlot()).c_str(), false, INFO_HOLD_MS);
      } else {
        redraw();
      }
      break;
    case ACT_ACTION_UNLOAD:
      buzzTick();
      sendUnload();
      break;
    default:
      break;
  }
}

void showStatusScreen() {
  char buf[160];
  String ip = (WiFi.status() == WL_CONNECTED) ? WiFi.localIP().toString() : String("down");
  String hub = cfg.base.empty() ? String("-") : String(cfg.base.c_str());
  snprintf(buf, sizeof(buf), "IP: %s\nRSSI: %d dBm  NFC: %s\nHub: %s\nBuild: %lld",
           ip.c_str(),
           (WiFi.status() == WL_CONNECTED) ? WiFi.RSSI() : 0,
           nfcOk ? "ok" : "err",
           hub.c_str(),
           (long long)FW_BUILD);
  showResult("Status", buf, false, INFO_HOLD_MS);
}

void loop() {
  serialPoll();
  uint32_t now = millis();
  int np = (int)printers.size(), ns = slotsUsable() ? (int)slots.size() : 0;

  // rotary encoder: consume pending detents (CW = next, CCW = prev)
  int16_t d;
  noInterrupts(); d = encDetents; encDetents = 0; interrupts();
  if (d) {
    if (g_rcfg.encoderInvert) d = -d;
    if (wake()) redraw();                     // dark screen: this turn only wakes it
    else {
      resultUntil = 0;
      if (uiState == UI_ACTIONS) {
        ActionItem items[6];
        int n = buildActionItems(items, 6);
        uiCursor = ((uiCursor + d) % n + n) % n;
        uiLastInput = now;
        buzzTick(); scrollStep = 0; redraw();
      } else if (uiState == UI_CONFIRM_UNLOAD) {
        uiCursor = ((uiCursor + d) % 2 + 2) % 2;
        uiLastInput = now;
        buzzTick(); redraw();
      } else {
        handleAction(menu.rotate(d, np, ns, now));
      }
    }
  }
  if (settleAt && (int32_t)(millis() - settleAt) >= 0) {
    settleAt = 0;
    if (menu.mode == MODE_SLOTS && curSlot()) saveSlot(curSlot()->label);
  }

  // encoder push: short = Actions menu (or confirm/select in overlay);
  // hold 1 s = printer list; hold 4 s = OTA
  BtnEvent ev = button.update(swPressed(), millis());
  if (ev != BTN_NONE) {
    Serial.printf("button: %s\n", ev == BTN_SHORT ? "short" : ev == BTN_CUE ? "1 s" :
                                  ev == BTN_MENU ? "menu" : "ota");
    if (wake()) { redraw(); swallowPress = ev == BTN_CUE; ev = BTN_NONE; }
    else if (swallowPress && ev != BTN_CUE) { swallowPress = false; ev = BTN_NONE; }
  }
  if (ev == BTN_CUE) { if (!swallowPress) buzzTick(); }
  else if (ev != BTN_NONE) {
    bool hadResult = (resultUntil != 0);
    resultUntil = 0;
    if (ev == BTN_SHORT) {
      if (hadResult) {
        buzzTick();
        redraw();
      } else if (uiState == UI_CONFIRM_UNLOAD) {
        bool yes = (uiCursor == 1);
        uiState = UI_NORMAL;
        buzzTick();
        if (yes) sendUnload();
        else redraw();
      } else if (uiState == UI_ACTIONS) {
        ActionItem items[6];
        int n = buildActionItems(items, 6);
        int idx = (uiCursor >= 0 && uiCursor < n) ? uiCursor : 0;
        ActionItemId chosen = items[idx].id;
        if (chosen == ACT_ID_UNLOAD) {
          uiState = UI_CONFIRM_UNLOAD;
          uiCursor = 0;
          uiLastInput = now;
          buzzTick();
          redraw();
        } else {
          uiState = UI_NORMAL;
          switch (chosen) {
            case ACT_ID_INFO:   handleAction(ACT_ACTION_SP_INFO); break;
            case ACT_ID_RESEND: handleAction(ACT_RESEND); break;
            case ACT_ID_UPDATE: buzzTick(); otaCheck(); break;
            case ACT_ID_STATUS: buzzTick(); showStatusScreen(); break;
            default:            buzzTick(); redraw(); break;
          }
        }
      } else if (menu.mode == MODE_SLOTS) {
        uiState = UI_ACTIONS;
        uiCursor = 0;
        uiLastInput = now;
        buzzTick();
        scrollStep = 0;
        redraw();
      } else {
        handleAction(menu.button(ev, np, now));
      }
    } else if (ev == BTN_MENU && uiState != UI_NORMAL) {
      uiState = UI_NORMAL;
      buzzTick();
      redraw();
    } else {
      uiState = UI_NORMAL;
      handleAction(menu.button(ev, np, now));
    }
  }
  if (uiState != UI_NORMAL && now - uiLastInput >= LIST_TIMEOUT_MS) {
    uiState = UI_NORMAL;
    redraw();
  }
  if (menu.tick(millis()) == ACT_CLOSE_LIST) redraw();

  // revert from a result screen after the hold window
  if (resultUntil && (int32_t)(millis() - resultUntil) >= 0) { resultUntil = 0; redraw(); }

  // background refreshes (skipped while a result, overlay, or the printer list is showing)
  if (!resultUntil && uiState == UI_NORMAL && menu.mode == MODE_SLOTS && !settleAt) {
    uint32_t listRefreshMs = (uint32_t)g_rcfg.listRefreshS * 1000UL;
    if (listsDue(millis(), listsAt, serverOk, scr != SCR_OFF, listRefreshMs, listRetryMs(listFails))) {
      static int bgRefreshCount = 0;
      bool quick = (++bgRefreshCount % LIST_FULL_EVERY) != 0;
      refreshLists(quick); if (scr != SCR_OFF) redraw();
    }
    if (millis() - updCheckAt > (uint32_t)g_rcfg.otaCheckHours * 3600000UL) {
      checkForUpdate(); if (scr != SCR_OFF) redraw();
    }
    if (millis() - rcfgSyncAt > listRefreshMs) {
      syncRemoteConfig();
    }
  }
  // scroll long names/labels
  if (!resultUntil && scr != SCR_OFF && millis() - scrollAt > MARQUEE_STEP_MS) {
    scrollAt = millis();
    if (needsScroll()) { scrollStep++; redraw(); }
  }
  screenIdle();

  // poll for a tag (short timeout keeps buttons responsive)
  uint8_t uid[7]; uint8_t uidLen = 0;
  if (nfc.readPassiveTargetID(PN532_MIFARE_ISO14443A, uid, &uidLen, 80)) {
    String u = formatUid(uid, uidLen);
    uint32_t t = millis();
    if (tagGate.accept(u.c_str(), t, (uint32_t)g_rcfg.scanCooldownMs)) {
      lastUid = u;
      wake();
      uiState = UI_NORMAL;
      if (menu.mode == MODE_PRINTERS || menu.mode == MODE_ACTIONS) { menu.mode = MODE_SLOTS; }   // a tap closes the list
      Serial.printf("tag %s -> %s / %s\n", u.c_str(),
                    curPrinter() ? curPrinter()->id.c_str() : "-", curSlot() ? curSlot()->label.c_str() : "-");
      sendScan(u);
    }
  } else {
    tagGate.noTag();
  }
}
