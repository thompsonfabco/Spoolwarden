// Where the reader connects to the Hub (pure logic, host-tested: pio test -e native).
//
// The reader's `base` is https://<name>.local:<port>/reader. A ".local" name is looked up by mDNS (bounded); when that
// finds nothing the settings' hub_ip (the Hub's home address when the reader was set up) is used. Either way the TLS
// handshake checks the certificate against the settings' root CA and the NAME from `base`, never the address.
// A reader set up earlier has a base that is not a ".local" name (the public address) and no hub_ip: it connects by
// name exactly as before.
#pragma once

#include <cstdint>
#include <functional>
#include <string>
#include <vector>

namespace reader {

// How long an mDNS lookup may take before hub_ip is used instead.
static const uint32_t MDNS_TIMEOUT_MS = 2000;

// The whole bound for opening the connection to the Hub: the mDNS lookup and the TCP connects of every candidate
// address share it (connectSliceMs). The TLS handshake after a TCP connect has its own limit, HANDSHAKE_S.
static const uint32_t HUB_CONNECT_BUDGET_MS = 5000;
static const unsigned HANDSHAKE_S = 5;

struct BaseParts {
  bool ok = false;
  std::string host;     // as written in base, no brackets
  uint16_t port = 443;  // 443 when base has none
};

// host and port of an https:// base ("https://hub.local:8443/reader"). ok=false when it has no host or a bad port.
BaseParts parseBase(const std::string& base);

// True for "x.local" (any case, one trailing dot allowed).
bool isLocalName(const std::string& host);

// True for a dotted-quad IPv4 address ("192.168.1.20").
bool isIpv4(const std::string& s);

// An mDNS lookup: fills `ip` (dotted quad) and returns true when `host` answered within timeoutMs.
using MdnsLookup = std::function<bool(const std::string& host, uint32_t timeoutMs, std::string& ip)>;

enum AddrSource {
  ADDR_BY_NAME,   // connect by name through the normal resolver (today's behaviour)
  ADDR_MDNS,      // the address mDNS gave for the name
  ADDR_HUB_IP,    // mDNS found nothing: the hub_ip from the settings
  ADDR_NONE,      // a ".local" name that did not answer and no hub_ip
  ADDR_CACHED,    // the address that worked last time (kept until a connect fails)
};

struct HubAddress {
  AddrSource source = ADDR_NONE;
  std::string host;     // the name the certificate is checked against
  uint16_t port = 443;
  std::string ip;       // the address to connect to (ADDR_MDNS, ADDR_HUB_IP)
};

// mDNS first, then hubIp; a base whose host is not a ".local" name is ADDR_BY_NAME and `lookup` is not called.
HubAddress chooseHubAddress(const std::string& base, const std::string& hubIp, const MdnsLookup& lookup);

// The addresses to try, in order, for a ".local" base: the cached one (no lookup then) or the mDNS answer, then hub_ip
// when it is a valid address different from the first. A base that is not a ".local" name gives one ADDR_BY_NAME entry
// and `lookup` is not called; an unusable base gives none. A failed first address is not the end: the next entry is
// tried (an mDNS answer can be a name clash's address or the wrong interface).
std::vector<HubAddress> hubAddresses(const std::string& base, const std::string& hubIp, const std::string& cachedIp,
                                     uint32_t mdnsMs, const MdnsLookup& lookup);

// Part of the budget the mDNS lookup may use: MDNS_TIMEOUT_MS, at most half the budget.
uint32_t mdnsSliceMs(uint32_t budgetMs);

// TCP connect time for the next address: what is left of the budget shared by the addresses still to try.
// 0 = the budget is used up, do not try.
uint32_t connectSliceMs(uint32_t budgetMs, uint32_t elapsedMs, size_t addressesLeft);

// Whether the mDNS responder (the reader's own name, and the queries) must be (re)started now: Wi-Fi is up and either
// it never started, or Wi-Fi got an address again since it did.
bool mdnsNeedsStart(bool wifiUp, bool mdnsUp, bool gotIpSinceStart);

}  // namespace reader
