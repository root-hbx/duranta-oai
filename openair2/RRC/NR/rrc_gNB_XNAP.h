/*
 * SPDX-License-Identifier: LicenseRef-CSSL-1.0
 */

#ifndef RRC_GNB_XNAP_H_
#define RRC_GNB_XNAP_H_

#include "nr_rrc_defs.h"
#include "intertask_interface.h"

void nr_rrc_trigger_xn_ho(gNB_RRC_INST *rrc, gNB_RRC_UE_t *ue, const nr_neighbour_cell_t *neighbour, sctp_assoc_t assoc_id);
void nr_rrc_trigger_xn_cho(gNB_RRC_INST *rrc, gNB_RRC_UE_t *ue, const nr_neighbour_cell_t **cells, int num_cells);
void nr_HO_Xn_CHO_trigger_telnet(gNB_RRC_INST *rrc, const uint32_t *neighbour_pci, int num_pci, uint32_t rrc_ue_id);
void nr_rrc_replace_xn_cho(gNB_RRC_INST *rrc, gNB_RRC_UE_t *ue, uint64_t t1_threshold, long t1_duration);
void nr_HO_Xn_CHO_replace_telnet(gNB_RRC_INST *rrc, uint32_t rrc_ue_id, uint64_t t1_threshold, long t1_duration);
void rrc_gNB_process_xnap_message(gNB_RRC_INST *rrc, MessageDef *msg);
void rrc_gNB_process_path_switch_req_ack(gNB_RRC_INST *rrc, const ngap_path_switch_req_ack_t *ack);
void nr_HO_Xn_trigger_telnet(gNB_RRC_INST *rrc, uint32_t neighbour_pci, uint32_t rrc_ue_id);
/** @return SCTP association of the Xn interface to gNB gnb_id, 0 if none */
sctp_assoc_t rrc_get_xn_assoc_id(const gNB_RRC_INST *rrc, uint32_t gnb_id);

#endif /* RRC_GNB_XNAP_H_ */
