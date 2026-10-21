/*
 * SPDX-License-Identifier: LicenseRef-CSSL-1.0
 */

/* gNB application layer -> XNAP messages */
MESSAGE_DEF(XNAP_REGISTER_GNB_REQ, MESSAGE_PRIORITY_MED, xnap_register_gnb_req_t, xnap_register_gnb_req)

/* XNAP -> RRC messages */
MESSAGE_DEF(XNAP_SETUP_IND,          MESSAGE_PRIORITY_MED, xnap_setup_ind_t,          xnap_setup_ind)
MESSAGE_DEF(XNAP_PEER_SHUTDOWN_IND,  MESSAGE_PRIORITY_MED, xnap_peer_shutdown_ind_t,  xnap_peer_shutdown_ind)

/* XnAP mobility messages, RRC <-> XNAP, see xnap_*_itti_t */
MESSAGE_DEF(XNAP_HANDOVER_REQ,          MESSAGE_PRIORITY_MED, xnap_handover_req_itti_t,          xnap_handover_req)
MESSAGE_DEF(XNAP_HANDOVER_REQ_ACK,      MESSAGE_PRIORITY_MED, xnap_handover_req_ack_itti_t,      xnap_handover_req_ack)
MESSAGE_DEF(XNAP_HANDOVER_PREP_FAILURE, MESSAGE_PRIORITY_MED, xnap_handover_prep_failure_itti_t, xnap_handover_prep_failure)
MESSAGE_DEF(XNAP_SN_STATUS_TRANSFER,    MESSAGE_PRIORITY_MED, xnap_sn_status_transfer_itti_t,    xnap_sn_status_transfer)
MESSAGE_DEF(XNAP_UE_CONTEXT_RELEASE,    MESSAGE_PRIORITY_MED, xnap_ue_context_release_itti_t,    xnap_ue_context_release)
MESSAGE_DEF(XNAP_HANDOVER_CANCEL,       MESSAGE_PRIORITY_MED, xnap_handover_cancel_itti_t,       xnap_handover_cancel)
MESSAGE_DEF(XNAP_HANDOVER_SUCCESS,      MESSAGE_PRIORITY_MED, xnap_handover_success_itti_t,      xnap_handover_success)
