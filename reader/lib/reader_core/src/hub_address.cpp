#include "hub_address.h"

#include <cctype>

namespace reader {

BaseParts parseBase(const std::string& base) {
  BaseParts out;
  static const std::string scheme = "https://";
  if (base.rfind(scheme, 0) != 0) return out;
  size_t end = base.find_first_of("/?#", scheme.size());
  std::string auth = base.substr(scheme.size(), end == std::string::npos ? std::string::npos : end - scheme.size());
  if (auth.find('@') != std::string::npos) return out;
  std::string portText;
  size_t colon = auth.rfind(':');
  if (!auth.empty() && auth[0] == '[') return out;   // IPv6 literals are not supported
  if (colon != std::string::npos) { portText = auth.substr(colon + 1); auth.erase(colon); }
  if (auth.empty()) return out;
  if (colon != std::string::npos) {
    if (portText.empty() || portText.size() > 5) return out;
    unsigned long p = 0;
    for (char c : portText) {
      if (c < '0' || c > '9') return out;
      p = p * 10 + (unsigned long)(c - '0');
    }
    if (p == 0 || p > 65535) return out;
    out.port = (uint16_t)p;
  }
  out.host = auth;
  out.ok = true;
  return out;
}

bool isLocalName(const std::string& host) {
  std::string h = host;
  if (!h.empty() && h.back() == '.') h.pop_back();
  static const std::string suffix = ".local";
  if (h.size() <= suffix.size()) return false;
  for (size_t i = 0; i < suffix.size(); i++)
    if (std::tolower((unsigned char)h[h.size() - suffix.size() + i]) != suffix[i]) return false;
  return true;
}

bool isIpv4(const std::string& s) {
  int parts = 0;
  size_t i = 0;
  while (i <= s.size()) {
    size_t j = i;
    int v = 0;
    while (j < s.size() && s[j] >= '0' && s[j] <= '9') { v = v * 10 + (s[j] - '0'); if (v > 255) return false; j++; }
    if (j == i || j - i > 3) return false;
    parts++;
    if (j == s.size()) break;
    if (s[j] != '.') return false;
    i = j + 1;
  }
  return parts == 4;
}

std::vector<HubAddress> hubAddresses(const std::string& base, const std::string& hubIp, const std::string& cachedIp,
                                     uint32_t mdnsMs, const MdnsLookup& lookup, const std::string& compiledHubIp) {
  std::vector<HubAddress> out;
  BaseParts p = parseBase(base);
  if (!p.ok) return out;                     // nothing to connect to
  HubAddress a;
  a.host = p.host;
  a.port = p.port;
  if (!isLocalName(p.host)) { a.source = ADDR_BY_NAME; out.push_back(a); return out; }
  std::string ip;
  if (isIpv4(cachedIp)) {
    a.source = ADDR_CACHED; a.ip = cachedIp; out.push_back(a);
  } else if (lookup && lookup(p.host, mdnsMs, ip) && isIpv4(ip)) {
    a.source = ADDR_MDNS; a.ip = ip; out.push_back(a);
  }
  const auto& fallbackIp = hubIp.empty() ? compiledHubIp : hubIp;
  if (isIpv4(fallbackIp) && (out.empty() || out[0].ip != fallbackIp)) {
    a.source = ADDR_HUB_IP; a.ip = fallbackIp; out.push_back(a);
  }
  return out;
}

HubAddress chooseHubAddress(const std::string& base, const std::string& hubIp, const MdnsLookup& lookup) {
  std::vector<HubAddress> list = hubAddresses(base, hubIp, "", MDNS_TIMEOUT_MS, lookup);
  if (!list.empty()) return list[0];
  HubAddress none;                           // ADDR_NONE
  BaseParts p = parseBase(base);
  if (p.ok) { none.host = p.host; none.port = p.port; }
  return none;
}

uint32_t mdnsSliceMs(uint32_t budgetMs) {
  return budgetMs / 2 < MDNS_TIMEOUT_MS ? budgetMs / 2 : MDNS_TIMEOUT_MS;
}

uint32_t connectSliceMs(uint32_t budgetMs, uint32_t elapsedMs, size_t addressesLeft) {
  if (addressesLeft == 0 || elapsedMs >= budgetMs) return 0;
  uint32_t slice = (budgetMs - elapsedMs) / (uint32_t)addressesLeft;
  return slice < 100 ? 0 : slice;
}

bool mdnsNeedsStart(bool wifiUp, bool mdnsUp, bool gotIpSinceStart) {
  return wifiUp && (!mdnsUp || gotIpSinceStart);
}

}  // namespace reader
