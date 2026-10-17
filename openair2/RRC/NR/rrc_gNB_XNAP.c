/*
 * SPDX-License-Identifier: LicenseRef-CSSL-1.0
 */

/* Xn handover (3GPP TS 38.300 9.2.3.2.1, TS 38.423 8.2), RRC part of the source and the target gNB:
 * source: HANDOVER REQUEST -> (ACK) RRCReconfiguration to the UE -> SN STATUS TRANSFER -> (UE CONTEXT RELEASE)
 * target: (HANDOVER REQUEST) target DU context -> ACK -> (UE access) PATH SWITCH -> UE CONTEXT RELEASE */

#include "rrc_gNB_XNAP.h"
#include <stdlib.h>
#include <string.h>
#include "assertions.h"
#include "common/utils/LOG/log.h"
#include "intertask_interface.h"
#include "nr_rrc_proto.h"
#include "rrc_gNB_du.h"
#include "rrc_cell_management.h"
#include "rrc_gNB_radio_bearers.h"
#include "rrc_gNB_UE_context.h"
#include "openair2/F1AP/f1ap_ids.h"
#include "MESSAGES/asn1_msg.h"
#include "openair3/SECU/key_nas_deriver.h"
#include "openair3/NGAP/ngap_gNB_mobility_management.h"
#include "openair2/RRC/NR/rrc_gNB_NGAP.h"
#include "openair2/RRC/NR/rrc_gNB_mobility.h"
#include "openair2/XNAP/lib/xnap_gNB_mobility_management.h"
#include "NR_HandoverPreparationInformation.h"
#include "NGAP_CellSize.h"
#include "XNAP_LastVisitedCell-Item.h"

static rrc_gNB_ue_context_t *xn_target_ue_by_source_id(gNB_RRC_INST *rrc, sctp_assoc_t assoc_id, uint32_t source_ue_id)
{
  rrc_gNB_ue_context_t *ue_context_p;
  RB_FOREACH (ue_context_p, rrc_nr_ue_tree_s, &rrc->rrc_ue_head) {
    const nr_handover_context_t *ho = ue_context_p->ue_context.ho_context;
    if (ho && ho->target && ho->target->xn_assoc_id == assoc_id && ho->target->xn_source_ue_id == source_ue_id)
      return ue_context_p;
  }
  return NULL;
}

/** @brief Release a UE without the core network (Xn handover source after the handover, or
 * target after a cancellation): CU-UP bearers, NGAP context, DU context, then the RRC context */
static void rrc_gNB_xn_release_ue(gNB_RRC_INST *rrc, rrc_gNB_ue_context_t *ue_context_p)
{
  gNB_RRC_UE_t *UE = &ue_context_p->ue_context;
  if (ue_associated_to_cuup(UE)) {
    e1ap_bearer_release_cmd_t cmd = {
        .gNB_cu_cp_ue_id = UE->rrc_ue_id,
        .gNB_cu_up_ue_id = UE->rrc_ue_id,
        .cause = {.type = E1AP_CAUSE_RADIO_NETWORK, .value = E1AP_RADIO_CAUSE_NORMAL_RELEASE},
    };
    rrc->cucp_cuup.bearer_context_release(get_existing_cuup_for_ue(UE), &cmd);
  }

  MessageDef *msg = itti_alloc_new_message(TASK_RRC_GNB, rrc->module_id, NGAP_UE_CONTEXT_LOCAL_RELEASE);
  NGAP_UE_CONTEXT_LOCAL_RELEASE(msg).gNB_ue_ngap_id = UE->rrc_ue_id;
  itti_send_msg_to_task(TASK_NGAP, rrc->module_id, msg);

  // without a handover context, the source DU already released the UE after the handover command
  const bool du_ue = UE->ho_context && cu_exists_f1_ue_data(UE->rrc_ue_id) && cu_get_f1_ue_data(UE->rrc_ue_id).secondary_ue != -1;
  if (UE->ho_context)
    nr_rrc_finalize_ho(UE);
  if (du_ue) {
    f1_ue_data_t ue_data = cu_get_f1_ue_data(UE->rrc_ue_id);
    f1ap_ue_context_rel_cmd_t cmd = {
        .gNB_CU_ue_id = UE->rrc_ue_id,
        .gNB_DU_ue_id = ue_data.secondary_ue,
        .cause = F1AP_CAUSE_RADIO_NETWORK,
        .cause_value = 5, // 5 = F1AP_CauseRadioNetwork_interaction_with_other_procedure
    };
    UE->xn_release = true; // the UE is removed at the F1 UE Context Release Complete
    rrc->mac_rrc.ue_context_release_command(ue_data.du_assoc_id, &cmd);
  } else {
    rrc_remove_ue(rrc, ue_context_p);
  }
}

/* ----------------------------------------- source ----------------------------------------- */

/** @brief SN STATUS TRANSFER (38.423 8.2.2) to the target, after the PDCP status from the CU-UP */
static int rrc_gNB_send_xn_sn_status_transfer(gNB_RRC_INST *rrc,
                                              gNB_RRC_UE_t *UE,
                                              const int n_to_mod,
                                              const int *drb_ids,
                                              const e1_pdcp_status_info_t *pdcp_status)
{
  const nr_ho_source_cu_t *source = UE->ho_context->source;
  MessageDef *msg = itti_alloc_new_message(TASK_RRC_GNB, rrc->module_id, XNAP_SN_STATUS_TRANSFER);
  xnap_sn_status_transfer_itti_t *m = &XNAP_SN_STATUS_TRANSFER(msg);
  m->assoc_id = source->xn_assoc_id;
  m->msg.s_ng_node_ue_xnap_id = UE->rrc_ue_id;
  m->msg.t_ng_node_ue_xnap_id = source->xn_target_ue_id;
  DevAssert(n_to_mod <= MAX_DRBS_PER_UE);
  for (int i = 0; i < n_to_mod; i++) {
    const drb_t *drb = get_drb(&UE->drbs, drb_ids[i]);
    if (!drb) {
      LOG_E(NR_RRC, "UE %u: SN Status Transfer: DRB %d not found\n", UE->rrc_ue_id, drb_ids[i]);
      continue;
    }
    const xnap_sn_length_t sn_len = drb->pdcp_config.drb.sn_size == 18 ? XNAP_SN_LENGTH_18 : XNAP_SN_LENGTH_12;
    xnap_drb_status_t *s = &m->msg.ran_status.drb_status_list[m->msg.ran_status.nb_drb++];
    s->drb_id = drb_ids[i];
    s->ul_count =
        (xnap_drb_count_value_t){.pdcp_sn = pdcp_status[i].ul_count.sn, .hfn = pdcp_status[i].ul_count.hfn, .sn_len = sn_len};
    s->dl_count =
        (xnap_drb_count_value_t){.pdcp_sn = pdcp_status[i].dl_count.sn, .hfn = pdcp_status[i].dl_count.hfn, .sn_len = sn_len};
  }
  itti_send_msg_to_task(TASK_XNAP, rrc->module_id, msg);
  return 0;
}

/** @brief HANDOVER CANCEL (38.423 8.2.3) to the target, e.g., when the UE re-establishes at the source */
static void nr_rrc_xn_ho_cancel(gNB_RRC_INST *rrc, gNB_RRC_UE_t *UE)
{
  const nr_ho_source_cu_t *source = UE->ho_context->source;
  MessageDef *msg = itti_alloc_new_message(TASK_RRC_GNB, rrc->module_id, XNAP_HANDOVER_CANCEL);
  XNAP_HANDOVER_CANCEL(msg).assoc_id = source->xn_assoc_id;
  XNAP_HANDOVER_CANCEL(msg).msg = (xnap_handover_cancel_t){
      .s_ng_node_ue_xnap_id = UE->rrc_ue_id,
      .cause = {.type = XNAP_CAUSE_RADIO_NETWORK, .value = XNAP_CAUSE_RADIO_NETWORK_LAYER_PROCEDURE_CANCELLED},
  };
  itti_send_msg_to_task(TASK_XNAP, rrc->module_id, msg);
}

static void fill_xn_pdusession(xnap_pdusession_resources_tobe_setup_item_t *out, const pdusession_t *in)
{
  out->pdusession_id = in->pdusession_id;
  out->nssai = malloc_or_fail(sizeof(*out->nssai));
  *out->nssai = in->nssai;
  out->n3_incoming = in->n3_incoming;
  out->pdu_session_type = in->pdu_session_type;
  out->qos_list = calloc_or_fail(seq_arr_size(&in->qos), sizeof(*out->qos_list));
  FOR_EACH_SEQ_ARR (nr_rrc_qos_t *, q, &in->qos) {
    xnap_qos_flow_tobe_setup_item_t *qos = &out->qos_list[out->num_qos++];
    const pdusession_level_qos_parameter_t *p = &q->qos;
    qos->qfi = p->qfi;
    qos->qos_params.qos_type = p->fiveQI_type;
    if (p->fiveQI_type == NON_DYNAMIC) {
      qos->qos_params.nondyn.fiveQI = p->qos_characteristics.non_dynamic.fiveQI;
    } else {
      const dynamic_5qi_t *dyn = &p->qos_characteristics.dynamic;
      qos->qos_params.dyn = (xnap_dynamic_5qi_t){.prio = dyn->qos_priority,
                                                 .pdb = dyn->packet_delay_budget,
                                                 .per = {.scalar = dyn->per.scalar, .exponent = dyn->per.exponent}};
    }
    qos->qos_params.arp = p->arp;
  }
}

/** @brief Trigger an Xn handover at the source gNB: HANDOVER REQUEST (38.423 8.2.1) to the
 * target gNB of the neighbour cell, over the Xn association assoc_id */
void nr_rrc_trigger_xn_ho(gNB_RRC_INST *rrc, gNB_RRC_UE_t *ue, const nr_neighbour_cell_t *neighbour, sctp_assoc_t assoc_id)
{
  int nb_pdu = 0;
  FOR_EACH_SEQ_ARR (rrc_pdu_session_param_t *, session, &ue->pduSessions) {
    if (session->status == PDU_SESSION_STATUS_ESTABLISHED)
      nb_pdu++;
  }
  // the PDU Session Resources To Be Setup List of the UE Context Information is mandatory
  if (nb_pdu == 0) {
    LOG_W(NR_RRC, "Xn HO not triggered for UE %u: no active PDU sessions\n", ue->rrc_ue_id);
    return;
  }
  if (ue->ho_context != NULL) {
    LOG_E(NR_RRC, "Ongoing handover for UE %d, cannot trigger new\n", ue->rrc_ue_id);
    return;
  }
  nr_rrc_cell_container_t *source_cell = rrc_get_pcell_for_ue(rrc, ue);
  if (source_cell == NULL) {
    LOG_E(NR_RRC, "No source cell found for UE %d\n", ue->rrc_ue_id);
    return;
  }
  byte_array_t hoPrepInfo = rrc_gNB_generate_HandoverPreparationInformation(rrc, ue);
  if (!hoPrepInfo.buf || (ssize_t)hoPrepInfo.len <= 0) {
    LOG_E(NR_RRC, "Failed to trigger Xn handover for UE %u\n", ue->rrc_ue_id);
    return;
  }

  ue->ho_context = alloc_ho_ctx(HO_CTX_SOURCE);
  nr_ho_source_cu_t *source = ue->ho_context->source;
  source->cell = source_cell;
  source->target_nci = neighbour->nrcell_id;
  source->ho_status_transfer = rrc_gNB_send_xn_sn_status_transfer;
  source->ho_cancel = nr_rrc_xn_ho_cancel;
  source->xn_assoc_id = assoc_id;

  MessageDef *msg = itti_alloc_new_message(TASK_RRC_GNB, rrc->module_id, XNAP_HANDOVER_REQ);
  XNAP_HANDOVER_REQ(msg).assoc_id = assoc_id;
  xnap_handover_req_t *req = &XNAP_HANDOVER_REQ(msg).msg;
  req->s_ng_node_ue_xnap_id = ue->rrc_ue_id;
  req->cause = (xnap_cause_t){.type = XNAP_CAUSE_RADIO_NETWORK,
                              .value = XNAP_CAUSE_RADIO_NETWORK_LAYER_HANDOVER_DESIRABLE_FOR_RADIO_REASONS};
  req->target_cgi = (xnap_ngran_cgi_t){.plmn_id = neighbour->plmn, .nrcell_id = neighbour->nrcell_id};
  req->guami = ue->ue_guami;

  xnap_ue_context_info_t *ctx = &req->ue_context;
  ctx->ngc_ue_sig_ref = ue->amf_ue_ngap_id;
  // source NG-C TNL address: not known to the RRC, not used by the target
  ctx->cp_tnl_ip_source.length = 4;
  ctx->security_capabilities = (xnap_security_capabilities_t){
      .nRencryption_algorithms = ue->security_capabilities.nRencryption_algorithms,
      .nRintegrity_algorithms = ue->security_capabilities.nRintegrity_algorithms,
      .eUTRAencryption_algorithms = ue->security_capabilities.eUTRAencryption_algorithms,
      .eUTRAintegrity_algorithms = ue->security_capabilities.eUTRAintegrity_algorithms,
  };
  /* K_NG-RAN* for the target cell (TS 33.501 6.9.2.3.2): vertical derivation from an unused NH,
   * if the AMF provided one, otherwise horizontal derivation from the current K_gNB */
  const uint8_t *key = ue->kgnb;
  ctx->as_security_ncc = ue->nh_ncc;
  if (ue->nh_next_valid) {
    key = ue->nh_next;
    ctx->as_security_ncc = ue->ncc_next;
  }
  nr_derive_key_ng_ran_star(neighbour->physicalCellId, neighbour->absoluteFrequencySSB, key, ctx->as_security_key_ranstar);
  ctx->ue_ambr = (xnap_ambr_t){.br_ul = ue->ambr.ul_br, .br_dl = ue->ambr.dl_br};
  ctx->rrc_context = hoPrepInfo;
  ctx->pdusession_resources_tobe_setup_list = calloc_or_fail(nb_pdu, sizeof(*ctx->pdusession_resources_tobe_setup_list));
  FOR_EACH_SEQ_ARR (rrc_pdu_session_param_t *, session, &ue->pduSessions) {
    if (session->status == PDU_SESSION_STATUS_ESTABLISHED)
      fill_xn_pdusession(&ctx->pdusession_resources_tobe_setup_list[ctx->num_pdu++], &session->param);
  }

  // UE History Information: the source cell
  ngap_cause_t cause = {.type = NGAP_CAUSE_RADIO_NETWORK, .value = NGAP_CAUSE_RADIO_NETWORK_HANDOVER_DESIRABLE_FOR_RADIO_REASON};
  const last_visited_ngran_cell_info_t cell = {
      .id = {.nrCellIdentity = source_cell->info.cell_id, .plmn_identity = source_cell->info.plmn},
      .type = NGAP_CellSize_small,
      .time_in_cell = min(time(NULL) - ue->last_seen, 4095),
      .cause = &cause,
  };
  req->num_last_visited_cells = 1;
  req->ue_history_info = calloc_or_fail(1, sizeof(*req->ue_history_info));
  req->ue_history_info[0].xnap_cell_type = XNAP_LastVisitedCell_Item_PR_nG_RAN_Cell;
  req->ue_history_info[0].last_visited_cell_info = encode_ng_last_visited_ngran_cell(&cell);

  LOG_I(NR_RRC,
        "UE %u: Xn handover preparation, send Handover Request (target gNB ID=%d, PCI=%d, NCC %ld)\n",
        ue->rrc_ue_id,
        neighbour->gNB_ID,
        neighbour->physicalCellId,
        ctx->as_security_ncc);
  itti_send_msg_to_task(TASK_XNAP, rrc->module_id, msg);
}

/** @brief Source: HANDOVER REQUEST ACKNOWLEDGE, send the Handover Command to the UE */
static void rrc_gNB_process_xn_handover_req_ack(gNB_RRC_INST *rrc, const xnap_handover_req_ack_itti_t *m)
{
  rrc_gNB_ue_context_t *ue_context_p = rrc_gNB_get_ue_context(rrc, m->msg.s_ng_node_ue_xnap_id);
  nr_ho_source_cu_t *source = NULL;
  if (ue_context_p && ue_context_p->ue_context.ho_context)
    source = ue_context_p->ue_context.ho_context->source;
  if (!source || source->xn_assoc_id != m->assoc_id) {
    LOG_E(NR_RRC, "Xn Handover Request Acknowledge for unknown handover of UE %u\n", m->msg.s_ng_node_ue_xnap_id);
    return;
  }
  gNB_RRC_UE_t *UE = &ue_context_p->ue_context;
  source->xn_target_ue_id = m->msg.t_ng_node_ue_xnap_id;

  byte_array_t buffer = doRRCReconfiguration_from_HandoverCommand(m->msg.target2source);
  if (!buffer.buf || buffer.len == 0) {
    LOG_E(NR_RRC, "UE %u: failed to decode/encode RRCReconfiguration from HandoverCommand\n", UE->rrc_ue_id);
    source->ho_cancel(rrc, UE);
    nr_rrc_finalize_ho(UE);
    return;
  }
  rrc_gNB_trigger_reconfiguration_for_handover(rrc, UE, buffer.buf, buffer.len);
  LOG_A(NR_RRC, "Send reconfiguration (HO Command) to UE %u/RNTI %04x\n", UE->rrc_ue_id, UE->rnti);
  free_byte_array(buffer);
}

/** @brief Source: HANDOVER PREPARATION FAILURE, the UE stays */
static void rrc_gNB_process_xn_handover_prep_failure(gNB_RRC_INST *rrc, const xnap_handover_prep_failure_itti_t *m)
{
  rrc_gNB_ue_context_t *ue_context_p = rrc_gNB_get_ue_context(rrc, m->msg.s_ng_node_ue_xnap_id);
  if (!ue_context_p || !ue_context_p->ue_context.ho_context || !ue_context_p->ue_context.ho_context->source) {
    LOG_E(NR_RRC, "Xn Handover Preparation Failure for unknown handover of UE %u\n", m->msg.s_ng_node_ue_xnap_id);
    return;
  }
  LOG_W(NR_RRC,
        "UE %u: Xn handover preparation failed (cause %d/%d)\n",
        m->msg.s_ng_node_ue_xnap_id,
        m->msg.cause.type,
        m->msg.cause.value);
  nr_rrc_finalize_ho(&ue_context_p->ue_context);
}

/** @brief Source: UE CONTEXT RELEASE (38.423 8.2.7) from the target after the path switch */
static void rrc_gNB_process_xn_ue_context_release(gNB_RRC_INST *rrc, const xnap_ue_context_release_itti_t *m)
{
  rrc_gNB_ue_context_t *ue_context_p = rrc_gNB_get_ue_context(rrc, m->msg.s_ng_node_ue_xnap_id);
  if (!ue_context_p) {
    LOG_W(NR_RRC, "Xn UE Context Release for unknown UE %u\n", m->msg.s_ng_node_ue_xnap_id);
    return;
  }
  gNB_RRC_UE_t *UE = &ue_context_p->ue_context;
  LOG_I(NR_RRC, "UE %u: Xn UE Context Release from the handover target, release the UE\n", UE->rrc_ue_id);
  if (UE->ho_context && UE->ho_context->source)
    nr_rrc_mark_ho_completed(rrc, UE->ho_context->source->cell->info.cell_id, UE->ho_context->source->target_nci);
  rrc_gNB_xn_release_ue(rrc, ue_context_p);
}

/* ----------------------------------------- target ----------------------------------------- */

static void send_xn_handover_prep_failure(gNB_RRC_INST *rrc,
                                          sctp_assoc_t assoc_id,
                                          uint32_t source_ue_id,
                                          xnap_cause_radio_network_t value)
{
  MessageDef *msg = itti_alloc_new_message(TASK_RRC_GNB, rrc->module_id, XNAP_HANDOVER_PREP_FAILURE);
  XNAP_HANDOVER_PREP_FAILURE(msg).assoc_id = assoc_id;
  XNAP_HANDOVER_PREP_FAILURE(msg).msg = (xnap_handover_preparation_failure_t){
      .s_ng_node_ue_xnap_id = source_ue_id,
      .cause = {.type = XNAP_CAUSE_RADIO_NETWORK, .value = value},
  };
  itti_send_msg_to_task(TASK_XNAP, rrc->module_id, msg);
}

/** @brief Target: the handover cannot be prepared, inform the source and release the UE */
static void nr_rrc_xn_ho_failure(gNB_RRC_INST *rrc, uint32_t gnb_ue_id, ngap_handover_failure_t *fail)
{
  UNUSED(fail);
  rrc_gNB_ue_context_t *ue_context_p = rrc_gNB_get_ue_context(rrc, gnb_ue_id);
  DevAssert(ue_context_p && ue_context_p->ue_context.ho_context && ue_context_p->ue_context.ho_context->target);
  const nr_ho_target_cu_t *target = ue_context_p->ue_context.ho_context->target;
  LOG_W(NR_RRC, "UE %u: Xn handover preparation failed in the target\n", gnb_ue_id);
  send_xn_handover_prep_failure(rrc,
                                target->xn_assoc_id,
                                target->xn_source_ue_id,
                                XNAP_CAUSE_RADIO_NETWORK_LAYER_NO_RADIO_RESOURCES_AVAILABLE_IN_TARGET_CELL);
  rrc_gNB_xn_release_ue(rrc, ue_context_p);
}

/** @brief Target: the target DU accepted the UE, HANDOVER REQUEST ACKNOWLEDGE with the Handover Command */
static void nr_rrc_xn_ho_acknowledge(gNB_RRC_INST *rrc, gNB_RRC_UE_t *UE)
{
  const nr_ho_target_cu_t *target = UE->ho_context->target;
  byte_array_t hoCommand = nr_rrc_ho_target_prepare(rrc, UE);
  if (hoCommand.len == 0) {
    target->ho_failure(rrc, UE->rrc_ue_id, NULL);
    return;
  }

  MessageDef *msg = itti_alloc_new_message(TASK_RRC_GNB, rrc->module_id, XNAP_HANDOVER_REQ_ACK);
  XNAP_HANDOVER_REQ_ACK(msg).assoc_id = target->xn_assoc_id;
  xnap_handover_req_ack_t *ack = &XNAP_HANDOVER_REQ_ACK(msg).msg;
  ack->s_ng_node_ue_xnap_id = target->xn_source_ue_id;
  ack->t_ng_node_ue_xnap_id = UE->rrc_ue_id;
  ack->pdusession_admitted_list = calloc_or_fail(seq_arr_size(&UE->pduSessions), sizeof(*ack->pdusession_admitted_list));
  FOR_EACH_SEQ_ARR (rrc_pdu_session_param_t *, session, &UE->pduSessions) {
    session->status = PDU_SESSION_STATUS_ESTABLISHED;
    xnap_pdusession_admitted_item_t *pdu = &ack->pdusession_admitted_list[ack->num_pdu_admitted++];
    pdu->pdusession_id = session->param.pdusession_id;
    pdu->qos_list = calloc_or_fail(seq_arr_size(&session->param.qos), sizeof(*pdu->qos_list));
    FOR_EACH_SEQ_ARR (nr_rrc_qos_t *, qos, &session->param.qos)
      pdu->qos_list[pdu->num_qos++].qfi = qos->qos.qfi;
  }
  ack->target2source = hoCommand;
  LOG_I(NR_RRC, "UE %u: Xn handover admitted, send Handover Request Acknowledge\n", UE->rrc_ue_id);
  itti_send_msg_to_task(TASK_XNAP, rrc->module_id, msg);
}

/** @brief Target: the UE accessed the target cell (RRCReconfigurationComplete), PATH SWITCH REQUEST
 * (38.413 8.4.4) to the AMF. The source is released once the AMF acknowledges. */
static void nr_rrc_xn_ho_complete(gNB_RRC_INST *rrc, gNB_RRC_UE_t *UE)
{
  const nr_ho_target_cu_t *target = UE->ho_context->target;
  UE->xn_source_assoc_id = target->xn_assoc_id;
  UE->xn_source_ue_id = target->xn_source_ue_id;

  nr_rrc_cell_container_t *cell = rrc_get_pcell_for_ue(rrc, UE);
  DevAssert(cell != NULL);
  MessageDef *msg = itti_alloc_new_message(TASK_RRC_GNB, rrc->module_id, NGAP_PATH_SWITCH_REQ);
  ngap_path_switch_req_t *req = &NGAP_PATH_SWITCH_REQ(msg);
  req->gNB_ue_ngap_id = UE->rrc_ue_id;
  req->amf_ue_ngap_id = UE->amf_ue_ngap_id;
  req->user_info.nrCellIdentity = cell->info.cell_id;
  req->user_info.target_ng_ran =
      (target_ran_node_id_t){.tac = cell->info.tac, .targetgNBId = rrc->node_id, .plmn_identity = cell->info.plmn};
  req->security_capabilities = UE->security_capabilities;
  FOR_EACH_SEQ_ARR (rrc_pdu_session_param_t *, session, &UE->pduSessions) {
    DevAssert(req->nb_of_pdusessions < NR_MAX_NB_PDU_SESSIONS);
    pdusession_setup_t *pdu = &req->pdusessions_tobeswitched[req->nb_of_pdusessions++];
    pdu->pdusession_id = session->param.pdusession_id;
    pdu->pdu_session_type = session->param.pdu_session_type;
    pdu->n3_outgoing = session->param.n3_outgoing;
    FOR_EACH_SEQ_ARR (nr_rrc_qos_t *, qos, &session->param.qos) {
      DevAssert(pdu->nb_of_qos_flow < MAX_QOS_FLOWS);
      pdu->associated_qos_flows[pdu->nb_of_qos_flow++].qfi = qos->qos.qfi;
    }
  }
  LOG_I(NR_RRC, "UE %u: Xn handover complete, send Path Switch Request\n", UE->rrc_ue_id);
  itti_send_msg_to_task(TASK_NGAP, rrc->module_id, msg);
}

static void nr_rrc_trigger_xn_ho_target(gNB_RRC_INST *rrc, gNB_RRC_UE_t *ue)
{
  nr_initiate_handover(rrc,
                       ue,
                       NULL,
                       &ue->ho_context->target->ue_ho_prep_info,
                       nr_rrc_xn_ho_acknowledge,
                       nr_rrc_xn_ho_complete,
                       NULL,
                       nr_rrc_xn_ho_failure);
  FREE_AND_ZERO_BYTE_ARRAY(ue->ho_context->target->ue_ho_prep_info);
  ue->UE_Capability_nr = get_ue_nr_capability(ue->rnti, ue->ue_cap_buffer.buf, ue->ue_cap_buffer.len);
  ue->UE_Capability_size = ue->ue_cap_buffer.len;
}

/** @brief UE-CapabilityRAT-ContainerList of a HandoverPreparationInformation */
static byte_array_t get_ue_cap_from_ho_prep_info(byte_array_t hpi)
{
  byte_array_t cap = {0};
  NR_HandoverPreparationInformation_t *info = NULL;
  asn_dec_rval_t dec = uper_decode_complete(NULL, &asn_DEF_NR_HandoverPreparationInformation, (void **)&info, hpi.buf, hpi.len);
  if (dec.code == RC_OK && info->criticalExtensions.present == NR_HandoverPreparationInformation__criticalExtensions_PR_c1
      && info->criticalExtensions.choice.c1->present
             == NR_HandoverPreparationInformation__criticalExtensions__c1_PR_handoverPreparationInformation) {
    const NR_HandoverPreparationInformation_IEs_t *ies = info->criticalExtensions.choice.c1->choice.handoverPreparationInformation;
    ssize_t len =
        uper_encode_to_new_buffer(&asn_DEF_NR_UE_CapabilityRAT_ContainerList, NULL, &ies->ue_CapabilityRAT_List, (void **)&cap.buf);
    if (len > 0)
      cap.len = len;
  }
  ASN_STRUCT_FREE(asn_DEF_NR_HandoverPreparationInformation, info);
  return cap;
}

/** @brief Target: HANDOVER REQUEST (38.423 8.2.1.2), admission and preparation of the UE context */
static void rrc_gNB_process_xn_handover_request(gNB_RRC_INST *rrc, const xnap_handover_req_itti_t *m)
{
  const xnap_handover_req_t *req = &m->msg;
  const xnap_ue_context_info_t *ctx = &req->ue_context;
  const sctp_assoc_t assoc_id = m->assoc_id;

  nr_rrc_cell_container_t *cell = get_cell_by_cell_id(&rrc->cells, req->target_cgi.nrcell_id);
  if (cell == NULL) {
    LOG_E(NR_RRC, "Xn Handover Request: no cell with NR Cell ID=%lu\n", req->target_cgi.nrcell_id);
    send_xn_handover_prep_failure(rrc, assoc_id, req->s_ng_node_ue_xnap_id, XNAP_CAUSE_RADIO_NETWORK_LAYER_CELL_NOT_AVAILABLE);
    return;
  }
  nr_rrc_du_container_t *du = get_du_by_assoc_id(rrc, cell->assoc_id);
  const plmn_id_t *serving_plmn = get_serving_plmn(rrc, &req->guami.plmn);
  if (du == NULL || serving_plmn == NULL || !is_cuup_associated(rrc)) {
    LOG_E(NR_RRC, "Xn Handover Request: no DU, CU-UP or PLMN for the UE\n");
    send_xn_handover_prep_failure(rrc,
                                  assoc_id,
                                  req->s_ng_node_ue_xnap_id,
                                  XNAP_CAUSE_RADIO_NETWORK_LAYER_NO_RADIO_RESOURCES_AVAILABLE_IN_TARGET_CELL);
    return;
  }
  byte_array_t ue_cap = get_ue_cap_from_ho_prep_info(ctx->rrc_context);
  if (ue_cap.len == 0) {
    LOG_E(NR_RRC, "Xn Handover Request: cannot decode the HandoverPreparationInformation\n");
    send_xn_handover_prep_failure(rrc, assoc_id, req->s_ng_node_ue_xnap_id, XNAP_CAUSE_RADIO_NETWORK_LAYER_UNSPECIFIED);
    return;
  }
  rrc_gNB_ue_context_t *ue_context_p = rrc_gNB_create_ue_context(du->assoc_id, UINT16_MAX, rrc, UINT64_MAX, UINT32_MAX);
  if (!ue_context_p) {
    free_byte_array(ue_cap);
    send_xn_handover_prep_failure(rrc,
                                  assoc_id,
                                  req->s_ng_node_ue_xnap_id,
                                  XNAP_CAUSE_RADIO_NETWORK_LAYER_NO_RADIO_RESOURCES_AVAILABLE_IN_TARGET_CELL);
    return;
  }
  gNB_RRC_UE_t *UE = &ue_context_p->ue_context;
  LOG_I(NR_RRC,
        "UE %u: received Xn Handover Request (NR Cell ID=%lu, PCI=%u, source UE XnAP ID %u)\n",
        UE->rrc_ue_id,
        req->target_cgi.nrcell_id,
        cell->info.pci,
        req->s_ng_node_ue_xnap_id);

  UE->ho_context = alloc_ho_ctx(HO_CTX_TARGET);
  nr_ho_target_cu_t *target = UE->ho_context->target;
  target->cell = cell;
  target->ho_trigger = nr_rrc_trigger_xn_ho_target;
  target->xn_assoc_id = assoc_id;
  target->xn_source_ue_id = req->s_ng_node_ue_xnap_id;
  target->ue_ho_prep_info = copy_byte_array(ctx->rrc_context);

  UE->amf_ue_ngap_id = ctx->ngc_ue_sig_ref;
  UE->ue_guami = req->guami;
  UE->serving_plmn = *serving_plmn;
  UE->ue_cap_buffer = ue_cap;
  UE->ambr.dl_br = ctx->ue_ambr.br_dl;
  UE->ambr.ul_br = ctx->ue_ambr.br_ul;

  /* AS security (TS 33.501 6.9.2.3.2): the source derived K_NG-RAN* for this cell, it is the new
   * K_gNB, and the UE gets its NCC in the masterKeyUpdate of the Handover Command */
  const ngap_security_capabilities_t cap = {
      .nRencryption_algorithms = ctx->security_capabilities.nRencryption_algorithms,
      .nRintegrity_algorithms = ctx->security_capabilities.nRintegrity_algorithms,
      .eUTRAencryption_algorithms = ctx->security_capabilities.eUTRAencryption_algorithms,
      .eUTRAintegrity_algorithms = ctx->security_capabilities.eUTRAintegrity_algorithms,
  };
  set_UE_security_algos(rrc, UE, &cap);
  memcpy(UE->kgnb, ctx->as_security_key_ranstar, SECURITY_KEY_LENGTH);
  memset(UE->nh, 0, SECURITY_KEY_LENGTH);
  UE->nh_ncc = ctx->as_security_ncc;
  UE->as_security_active = true;
  activate_srb(UE, SRB1);
  activate_srb(UE, SRB2);
  // as for N2: the first PDUs after the random access are still ciphered with the source keys
  nr_rrc_pdcp_config_security(UE, true);

  DevAssert(ctx->num_pdu <= NR_MAX_NB_PDU_SESSIONS);
  pdusession_t to_setup[NR_MAX_NB_PDU_SESSIONS] = {0};
  for (int i = 0; i < ctx->num_pdu; i++) {
    const xnap_pdusession_resources_tobe_setup_item_t *in = &ctx->pdusession_resources_tobe_setup_list[i];
    pdusession_t *pdu = &to_setup[i];
    pdu->pdusession_id = in->pdusession_id;
    pdu->nssai = *in->nssai;
    pdu->pdu_session_type = in->pdu_session_type;
    pdu->n3_incoming = in->n3_incoming;
    seq_arr_init(&pdu->qos, sizeof(nr_rrc_qos_t));
    for (int q = 0; q < in->num_qos; q++) {
      const xnap_qos_flow_tobe_setup_item_t *qin = &in->qos_list[q];
      pdusession_level_qos_parameter_t qos = {.qfi = qin->qfi, .fiveQI_type = qin->qos_params.qos_type, .arp = qin->qos_params.arp};
      if (qos.fiveQI_type == NON_DYNAMIC) {
        qos.qos_characteristics.non_dynamic.fiveQI = qin->qos_params.nondyn.fiveQI;
      } else {
        qos.qos_characteristics.dynamic.qos_priority = qin->qos_params.dyn.prio;
        qos.qos_characteristics.dynamic.packet_delay_budget = qin->qos_params.dyn.pdb;
        qos.qos_characteristics.dynamic.per.scalar = qin->qos_params.dyn.per.scalar;
        qos.qos_characteristics.dynamic.per.exponent = qin->qos_params.dyn.per.exponent;
      }
      if (!add_qos(&pdu->qos, &qos))
        LOG_E(NR_RRC, "UE %u: failed to add QoS flow %d of PDU session %d\n", UE->rrc_ue_id, qos.qfi, pdu->pdusession_id);
    }
  }
  nr_rrc_add_bearers(rrc, UE, ctx->num_pdu, to_setup);
  trigger_bearer_setup(rrc, UE, UE->ambr.dl_br);
}

/** @brief Target: SN STATUS TRANSFER, PDCP COUNTs of the source for the CU-UP */
static void rrc_gNB_process_xn_sn_status_transfer(gNB_RRC_INST *rrc, const xnap_sn_status_transfer_itti_t *m)
{
  rrc_gNB_ue_context_t *ue_context_p = rrc_gNB_get_ue_context(rrc, m->msg.t_ng_node_ue_xnap_id);
  if (!ue_context_p) {
    LOG_E(NR_RRC, "Xn SN Status Transfer for unknown UE %u\n", m->msg.t_ng_node_ue_xnap_id);
    return;
  }
  for (int i = 0; i < m->msg.ran_status.nb_drb; i++) {
    const xnap_drb_status_t *s = &m->msg.ran_status.drb_status_list[i];
    const ngap_drb_status_t status = {
        .drb_id = s->drb_id,
        .ul_count = {.pdcp_sn = s->ul_count.pdcp_sn, .hfn = s->ul_count.hfn, .sn_len = (ngap_sn_length_t)s->ul_count.sn_len},
        .dl_count = {.pdcp_sn = s->dl_count.pdcp_sn, .hfn = s->dl_count.hfn, .sn_len = (ngap_sn_length_t)s->dl_count.sn_len},
    };
    LOG_I(NR_RRC,
          "UE %u: Xn SN Status Transfer DRB %d: UL COUNT %u/%u, DL COUNT %u/%u (HFN/SN)\n",
          m->msg.t_ng_node_ue_xnap_id,
          s->drb_id,
          s->ul_count.hfn,
          s->ul_count.pdcp_sn,
          s->dl_count.hfn,
          s->dl_count.pdcp_sn);
    e1_notify_pdcp_status(rrc, &ue_context_p->ue_context, &status);
  }
}

/** @brief Target: HANDOVER CANCEL from the source, release the prepared UE */
static void rrc_gNB_process_xn_handover_cancel(gNB_RRC_INST *rrc, const xnap_handover_cancel_itti_t *m)
{
  rrc_gNB_ue_context_t *ue_context_p = xn_target_ue_by_source_id(rrc, m->assoc_id, m->msg.s_ng_node_ue_xnap_id);
  if (!ue_context_p) {
    LOG_W(NR_RRC, "Xn Handover Cancel for unknown UE (source UE XnAP ID %u)\n", m->msg.s_ng_node_ue_xnap_id);
    return;
  }
  LOG_I(NR_RRC, "UE %u: Xn handover cancelled by the source\n", ue_context_p->ue_context.rrc_ue_id);
  rrc_gNB_xn_release_ue(rrc, ue_context_p);
}

/** @brief Target: PATH SWITCH REQUEST ACKNOWLEDGE, keep the new {NH, NCC} and release the source */
void rrc_gNB_process_path_switch_req_ack(gNB_RRC_INST *rrc, const ngap_path_switch_req_ack_t *ack)
{
  rrc_gNB_ue_context_t *ue_context_p = rrc_gNB_get_ue_context(rrc, ack->gNB_ue_ngap_id);
  if (!ue_context_p) {
    LOG_E(NR_RRC, "Path Switch Request Acknowledge for unknown UE %u\n", ack->gNB_ue_ngap_id);
    return;
  }
  gNB_RRC_UE_t *UE = &ue_context_p->ue_context;
  UE->amf_ue_ngap_id = ack->amf_ue_ngap_id;
  UE->nh_next_valid = true;
  memcpy(UE->nh_next, ack->next_security_key, SECURITY_KEY_LENGTH);
  UE->ncc_next = ack->nh_ncc;
  for (int i = 0; i < ack->nb_of_pdusessions; i++) {
    if (ack->pdusessions_switched[i].pathSwitchReqAckTransfer.n3_incoming)
      LOG_W(NR_RRC,
            "UE %u: new UL NG-U tunnel of PDU session %d ignored\n",
            UE->rrc_ue_id,
            ack->pdusessions_switched[i].pdusession_id);
  }
  LOG_I(NR_RRC, "UE %u: Path Switch Request Acknowledge (NCC %d), release the source\n", UE->rrc_ue_id, UE->ncc_next);

  if (UE->xn_source_assoc_id == 0)
    return;
  MessageDef *msg = itti_alloc_new_message(TASK_RRC_GNB, rrc->module_id, XNAP_UE_CONTEXT_RELEASE);
  XNAP_UE_CONTEXT_RELEASE(msg).assoc_id = UE->xn_source_assoc_id;
  XNAP_UE_CONTEXT_RELEASE(msg).msg = (xnap_ue_context_release_t){
      .s_ng_node_ue_xnap_id = UE->xn_source_ue_id,
      .t_ng_node_ue_xnap_id = UE->rrc_ue_id,
  };
  itti_send_msg_to_task(TASK_XNAP, rrc->module_id, msg);
  UE->xn_source_assoc_id = 0;
}

/* -------------------------------------- dispatching --------------------------------------- */

void rrc_gNB_process_xnap_message(gNB_RRC_INST *rrc, MessageDef *msg)
{
  switch (ITTI_MSG_ID(msg)) {
    case XNAP_HANDOVER_REQ:
      rrc_gNB_process_xn_handover_request(rrc, &XNAP_HANDOVER_REQ(msg));
      free_xnap_handover_request(&XNAP_HANDOVER_REQ(msg).msg);
      break;
    case XNAP_HANDOVER_REQ_ACK:
      rrc_gNB_process_xn_handover_req_ack(rrc, &XNAP_HANDOVER_REQ_ACK(msg));
      free_xnap_handover_request_acknowledge(&XNAP_HANDOVER_REQ_ACK(msg).msg);
      break;
    case XNAP_HANDOVER_PREP_FAILURE:
      rrc_gNB_process_xn_handover_prep_failure(rrc, &XNAP_HANDOVER_PREP_FAILURE(msg));
      break;
    case XNAP_SN_STATUS_TRANSFER:
      rrc_gNB_process_xn_sn_status_transfer(rrc, &XNAP_SN_STATUS_TRANSFER(msg));
      break;
    case XNAP_UE_CONTEXT_RELEASE:
      rrc_gNB_process_xn_ue_context_release(rrc, &XNAP_UE_CONTEXT_RELEASE(msg));
      break;
    case XNAP_HANDOVER_CANCEL:
      rrc_gNB_process_xn_handover_cancel(rrc, &XNAP_HANDOVER_CANCEL(msg));
      break;
    default:
      AssertFatal(false, "unexpected XnAP message %s\n", ITTI_MSG_NAME(msg));
  }
}

void nr_HO_Xn_trigger_telnet(gNB_RRC_INST *rrc, uint32_t neighbour_pci, uint32_t rrc_ue_id)
{
  rrc_gNB_ue_context_t *ue_context_p = rrc_gNB_get_ue_context(rrc, rrc_ue_id);
  nr_rrc_cell_container_t *pcell = ue_context_p ? rrc_get_pcell_for_ue(rrc, &ue_context_p->ue_context) : NULL;
  if (pcell == NULL) {
    LOG_E(NR_RRC, "Xn HO trigger failed for UE %d: unknown UE or cell\n", rrc_ue_id);
    return;
  }
  const neighbour_cell_configuration_t *cell = get_neighbour_cell_config(rrc, pcell->info.cell_id);
  const nr_neighbour_cell_t *neighbour = cell ? get_neighbour_cell_by_pci(cell, neighbour_pci) : NULL;
  if (neighbour == NULL) {
    LOG_E(NR_RRC, "Xn HO trigger failed for UE %d: no neighbour cell with PCI=%d\n", rrc_ue_id, neighbour_pci);
    return;
  }
  const sctp_assoc_t assoc_id = rrc_get_xn_assoc_id(rrc, neighbour->gNB_ID);
  if (assoc_id == 0) {
    LOG_E(NR_RRC, "Xn HO trigger failed for UE %d: no Xn interface to gNB %u\n", rrc_ue_id, neighbour->gNB_ID);
    return;
  }
  LOG_I(NR_RRC, "UE %d: triggered Xn HO, source PCI=%d to target PCI=%d\n", rrc_ue_id, pcell->info.pci, neighbour_pci);
  nr_rrc_trigger_xn_ho(rrc, &ue_context_p->ue_context, neighbour, assoc_id);
}

sctp_assoc_t rrc_get_xn_assoc_id(const gNB_RRC_INST *rrc, uint32_t gnb_id)
{
  rrc_xn_candidate_t key = {.gnb_id = gnb_id};
  const rrc_xn_candidate_t *cand = RB_FIND(rrc_xn_cand_tree, (struct rrc_xn_cand_tree *)&rrc->xn_candidates, &key);
  return cand ? cand->assoc_id : 0;
}
