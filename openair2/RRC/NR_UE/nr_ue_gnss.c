/*
 * SPDX-License-Identifier: LicenseRef-CSSL-1.0
 */

#include "nr_ue_gnss.h"
#include <stdatomic.h>
#include <time.h>
#include "common/utils/LOG/log.h"

#define UNIX_TO_1900_MS (2208988800ULL * 1000)

static uint64_t gnss_utc_start;
static _Atomic uint64_t gnss_rx_time_ms;

void nr_ue_gnss_init(uint64_t utc_start)
{
  gnss_utc_start = utc_start;
  if (utc_start)
    LOG_I(NR_RRC, "GNSS time: UTC %lu ms (since 1970) at rfsim time 0\n", utc_start);
}

void nr_ue_gnss_set_rx_time(uint64_t timestamp, double sample_rate)
{
  atomic_store_explicit(&gnss_rx_time_ms, (uint64_t)(timestamp * 1000.0 / sample_rate), memory_order_relaxed);
}

uint64_t nr_ue_gnss_utc_ms(void)
{
  if (gnss_utc_start)
    return UNIX_TO_1900_MS + gnss_utc_start + atomic_load_explicit(&gnss_rx_time_ms, memory_order_relaxed);
  struct timespec t;
  clock_gettime(CLOCK_REALTIME, &t);
  return UNIX_TO_1900_MS + (uint64_t)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}
