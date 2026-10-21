/*
 * SPDX-License-Identifier: LicenseRef-CSSL-1.0
 */

#ifndef __LAYER2_NR_MAC_CONFIG_H__
#define __LAYER2_NR_MAC_CONFIG_H__

#include <stdint.h>

typedef struct vector_s {
  int X;
  int Y;
  int Z;
} vector_t;

// Format similar to values sent in SIB19
typedef struct gnb_sat_position_update_s {
  int sfn;
  int subframe;
  uint32_t delay;
  int drift;
  uint32_t accel;
  vector_t position;
  vector_t velocity;
} gnb_sat_position_update_t;

bool nr_update_sib19(const gnb_sat_position_update_t *sat_position);

/// Common TA prediction for a dedicated ntn-Config: the SIB19 values of the current epoch, with ta-Common, drift and
/// drift variant fitted over [utc_start_ms, utc_end_ms] (UTC, ms since 1970) instead of around the epoch.
/// epoch_utc_ms: UTC of the epoch. max_err_ns: largest error of the predicted (SIB19-quantised) Common TA over the window.
/// Returns false if not known.
typedef bool (*nr_ntn_ta_predictor_t)(uint64_t utc_start_ms,
                                      uint64_t utc_end_ms,
                                      gnb_sat_position_update_t *out,
                                      uint64_t *epoch_utc_ms,
                                      int *max_err_ns);
void nr_register_ntn_ta_predictor(nr_ntn_ta_predictor_t predictor);

bool nr_trigger_bwp_switch(uint16_t rnti, int bwp_id);

#endif /*__LAYER2_NR_MAC_CONFIG_H__*/
