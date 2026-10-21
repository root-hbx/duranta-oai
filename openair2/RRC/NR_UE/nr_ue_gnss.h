/*
 * SPDX-License-Identifier: LicenseRef-CSSL-1.0
 */

#ifndef NR_UE_GNSS_H_
#define NR_UE_GNSS_H_

#include <stdint.h>

/* GNSS time of the UE: an NTN UE is GNSS-capable (TS 38.300 16.14) and measures the UTC time Mt of
 * the conditional handover event T1 (TS 38.331 5.5.4.16). With the rfsimulator, the satellite trace
 * time is the rfsim time (sat_trace_common_clock), so the UTC is the one of the trace at rfsim time 0
 * plus the rfsim time of the received samples. */

/** @param utc_start UTC at rfsim time 0, in ms since 1970-01-01 (utc_ms of the trace start);
 *  0: the system clock is the GNSS clock */
void nr_ue_gnss_init(uint64_t utc_start);
/** @brief Time of the last samples received from the radio */
void nr_ue_gnss_set_rx_time(uint64_t timestamp, double sample_rate);
/** @return UTC in ms since 1900-01-01, the reference of t1-Threshold */
uint64_t nr_ue_gnss_utc_ms(void);

#endif /* NR_UE_GNSS_H_ */
