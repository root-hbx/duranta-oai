/*
 * SPDX-License-Identifier: LicenseRef-CSSL-1.0
 */

#ifndef RRC_UE_CHO_H_
#define RRC_UE_CHO_H_

#include "rrc_defs.h"
#include "NR_ConditionalReconfiguration-r16.h"

/** @brief Conditional reconfiguration removal and addition/modification (TS 38.331 5.3.5.13.2/3) */
void nr_rrc_cho_configure(NR_UE_RRC_INST_t *rrc, const NR_ConditionalReconfiguration_r16_t *cr);
/** @brief A measId of an execution condition has been modified: its event is no longer fulfilled */
void nr_rrc_cho_meas_id_modified(NR_UE_RRC_INST_t *rrc, long meas_id);
bool nr_rrc_cho_configured(const NR_UE_RRC_INST_t *rrc);
/** @brief Remove all the entries within VarConditionalReconfig */
void nr_rrc_cho_release(NR_UE_RRC_INST_t *rrc);
/** @brief Conditional reconfiguration evaluation (5.3.5.13.4), every step_ms
 *  @return index in VarConditionalReconfig of the cell selected for execution (5.3.5.13.5), -1 if none */
int nr_rrc_cho_evaluate(NR_UE_RRC_INST_t *rrc, int gnb_index, int step_ms);
/** @return index in VarConditionalReconfig of the candidate of a cell selected after a failure for which the stored
 *  condRRCReconfig applies (5.3.7.3: applicable cell, condEventT1 leaving condition not fulfilled), -1 if none */
int nr_rrc_cho_recovery_candidate(NR_UE_RRC_INST_t *rrc, int gnb_index, int pci);
/** @return a copy of the ntn-Config of the reconfigurationWithSync of a candidate, NULL if none */
NR_NTN_Config_r17_t *nr_rrc_cho_get_ntn_config(const nr_cond_reconfig_t *c);

#endif /* RRC_UE_CHO_H_ */
