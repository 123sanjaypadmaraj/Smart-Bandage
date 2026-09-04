/*
 * Phase 7 wire packet protocol -- C mirror of common/protocol/packet.py.
 *
 * NOT COMPILED OR FLASHED. There is no Arduino/ESP-IDF toolchain or ESP32
 * device available in the environment this was written in -- this header
 * is a first draft against docs/hardware/packet_protocol.md, ready to
 * compile-check and bench-test once Phase 7 hardware exists. The Python
 * side (common/protocol/packet.py) is the tested, canonical spec; if this
 * file and that one ever disagree, the Python side is right and this file
 * is the bug.
 *
 * Byte layout (little-endian, 41 bytes, packed):
 *   see docs/hardware/packet_protocol.md for the full field table.
 */
#ifndef SMART_BANDAGE_PACKET_H
#define SMART_BANDAGE_PACKET_H

#include <stdint.h>
#include <string.h>

#define SB_PACKET_MAGIC 0xA5
#define SB_PACKET_VERSION 1
#define SB_ID_FIELD_LEN 12
#define SB_BATTERY_ABSENT 0xFF

#define SB_FLAG_HAS_TEMPERATURE (1 << 0)
#define SB_FLAG_HAS_BATTERY (1 << 1)

#pragma pack(push, 1)
typedef struct {
    uint8_t magic;
    uint8_t version;
    char device_id[SB_ID_FIELD_LEN];
    char channel_id[SB_ID_FIELD_LEN];
    uint32_t timestamp;   // unix seconds, UTC
    float raw_signal;
    float temperature;    // meaningless unless flags & SB_FLAG_HAS_TEMPERATURE
    uint8_t battery;      // 0-100, or SB_BATTERY_ABSENT
    uint8_t flags;
    uint8_t checksum;
} sb_packet_t;
#pragma pack(pop)

#define SB_PACKET_SIZE (sizeof(sb_packet_t))  // must equal 41; see packet_protocol_selftest()

/* Sum of every byte except the checksum field itself, mod 256 -- matches
 * common/protocol/packet.py's _checksum() exactly. */
static inline uint8_t sb_packet_checksum(const sb_packet_t *pkt) {
    const uint8_t *bytes = (const uint8_t *)pkt;
    uint32_t sum = 0;
    for (size_t i = 0; i < SB_PACKET_SIZE - 1; i++) {
        sum += bytes[i];
    }
    return (uint8_t)(sum & 0xFF);
}

/* Fills a packet from one reading. device_id/channel_id are copied and
 * NUL-padded; the caller is responsible for keeping them <= SB_ID_FIELD_LEN
 * bytes (the real device only ever has one device_id and a handful of
 * fixed channel_ids, so this isn't checked at runtime the way the Python
 * encoder checks it -- get the id strings right at compile time). */
static inline void sb_packet_encode(
    sb_packet_t *pkt,
    const char *device_id,
    const char *channel_id,
    uint32_t timestamp,
    float raw_signal,
    const float *temperature,  // NULL => absent
    const int16_t *battery     // NULL => absent
) {
    memset(pkt, 0, sizeof(*pkt));
    pkt->magic = SB_PACKET_MAGIC;
    pkt->version = SB_PACKET_VERSION;
    strncpy(pkt->device_id, device_id, SB_ID_FIELD_LEN);
    strncpy(pkt->channel_id, channel_id, SB_ID_FIELD_LEN);
    pkt->timestamp = timestamp;
    pkt->raw_signal = raw_signal;

    pkt->flags = 0;
    if (temperature != NULL) {
        pkt->temperature = *temperature;
        pkt->flags |= SB_FLAG_HAS_TEMPERATURE;
    }
    if (battery != NULL) {
        pkt->battery = (uint8_t)(*battery);
        pkt->flags |= SB_FLAG_HAS_BATTERY;
    } else {
        pkt->battery = SB_BATTERY_ABSENT;
    }

    pkt->checksum = sb_packet_checksum(pkt);
}

/* Returns 1 if `pkt` passes the magic/version/checksum checks, 0 otherwise
 * -- mirrors common/protocol/packet.py's decode_packet() validation, run
 * on the *receiving* end (backend gateway), not on the ESP32 itself. */
static inline int sb_packet_is_valid(const sb_packet_t *pkt) {
    if (pkt->magic != SB_PACKET_MAGIC) return 0;
    if (pkt->version != SB_PACKET_VERSION) return 0;
    if (pkt->checksum != sb_packet_checksum(pkt)) return 0;
    return 1;
}

/* Sanity check the struct actually packed to the size the protocol
 * requires -- call this once at startup; a mismatch means the compiler's
 * struct packing doesn't match docs/hardware/packet_protocol.md and every
 * packet this firmware sends will be silently wrong. */
static inline int sb_packet_selftest(void) {
    return SB_PACKET_SIZE == 41 ? 1 : 0;
}

#endif  // SMART_BANDAGE_PACKET_H
