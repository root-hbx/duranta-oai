/*
 * SPDX-License-Identifier: LicenseRef-CSSL-1.0
 */

/* Other SI (SIB2...) must be scheduled with the CORESET0 of the transmitted SSB
 * index, also when SSB index 0 is not transmitted (ssb_PositionsInBurst != 1). */

#include <signal.h>
#include <stdio.h>
#include <stdlib.h>

#include "common/config/config_load_configmodule.h"
#include "common/ran_context.h"
#include "common/utils/assertions.h"
#include "common/utils/LOG/log.h"
#include "common/utils/oai_asn1.h"
#include "e1ap_messages_types.h"
#include "executables/softmodem-common.h"
#include "LAYER2/NR_MAC_gNB/mac_proto.h"
#include "LAYER2/NR_MAC_gNB/nr_mac_gNB.h"
#include "LAYER2/NR_MAC_gNB/nr_radio_config.h"
#include "NR_PHY_INTERFACE/NR_IF_Module.h"
#include "NR_SIB2.h"
#include "PHY/defs_common.h"
#include "openair1/SIMULATION/NR_PHY/nr_unitary_defs.h"

RAN_CONTEXT_t RC;
int64_t uplink_frequency_offset[MAX_NUM_CCs][4];
uint64_t downlink_frequency[MAX_NUM_CCs][4];
instance_t DUuniqInstance = 0;
instance_t CUuniqInstance = 0;
unsigned int NTN_UE_Koffset = 0;
configmodule_interface_t *uniqCfg = NULL;
char *uecap_file;
THREAD_STRUCT thread_struct;

void e1_bearer_context_setup(const e1ap_bearer_setup_req_t *req) { (void)req; abort(); }
void e1_bearer_context_modif(const e1ap_bearer_mod_req_t *req) { (void)req; abort(); }
struct e1ap_bearer_mod_confirm_s;
void e1_bearer_context_mod_confirm(const struct e1ap_bearer_mod_confirm_s *conf) { (void)conf; abort(); }
struct e1ap_bearer_release_cmd_s;
void e1_bearer_release_cmd(const struct e1ap_bearer_release_cmd_s *cmd) { (void)cmd; abort(); }

static void phy_config_req_stub(NR_PHY_Config_t *cfg)
{
  (void)cfg;
}

// common search spaces as configured by the gNB (gnb_config.c), other SI in search space 3
static void add_common_search_spaces(NR_ServingCellConfigCommon_t *scc)
{
  NR_PDCCH_ConfigCommon_t *pcc = scc->downlinkConfigCommon->initialDownlinkBWP->pdcch_ConfigCommon->choice.setup;
  pcc->commonSearchSpaceList = calloc_or_fail(1, sizeof(*pcc->commonSearchSpaceList));
  int candidates[NUM_PDCCH_AGG_LEVELS] = {[PDCCH_AGG_LEVEL4] = NR_SearchSpace__nrofCandidates__aggregationLevel4_n2};
  for (int id = 1; id <= 3; id++)
    asn1cSeqAdd(&pcc->commonSearchSpaceList->list, rrc_searchspace_config(true, id, 0, candidates));
  asn1cCallocOne(pcc->searchSpaceSIB1, 0);
  asn1cCallocOne(pcc->ra_SearchSpace, 1);
  asn1cCallocOne(pcc->pagingSearchSpace, 2);
  asn1cCallocOne(pcc->searchSpaceOtherSystemInformation, 3);
}

static f1ap_sib_msg_t encode_sib2(void)
{
  NR_SIB2_t sib2 = {.intraFreqCellReselectionInfo.q_RxLevMin = -70};
  uint8_t *buf = NULL;
  ssize_t len = uper_encode_to_new_buffer(&asn_DEF_NR_SIB2, NULL, &sib2, (void **)&buf);
  AssertFatal(len > 0, "could not encode SIB2\n");
  return (f1ap_sib_msg_t){.SI_container = {.buf = buf, .len = len}, .SI_type = 2};
}

/* returns the number of other SI DCIs checked, or -1 on a wrong CORESET0 */
static int run_cell(uint64_t ssb_bitmap)
{
  NR_ServingCellConfigCommon_t *scc = calloc_or_fail(1, sizeof(*scc));
  prepare_scc(scc);
  uint64_t sim_bitmap;
  fill_scc_sim(scc, &sim_bitmap, 106, 106, 1, 1);
  fix_scc(scc, ssb_bitmap);
  add_common_search_spaces(scc);

  const nr_mac_config_t conf = {
      .pdsch_AntennaPorts = {.N1 = 1, .N2 = 1, .XP = 1},
      .pusch_AntennaPorts = 1,
      .minRXTXTIME = 6,
      .num_dlharq = 16,
      .num_ulharq = 16,
      .maxMIMO_layers = 1,
      .timer_config = {.sr_TransMax = 64, .t300 = 400, .t301 = 400, .t310 = 2000, .n310 = 10, .t311 = 3000, .n311 = 1, .t319 = 400},
      .num_agg_level_candidates = {0, 4, 4, 2, 0},
  };
  const nr_rlc_configuration_t rlc_config = {0};
  RC.nb_nr_macrlc_inst = 1;
  nr_cell_sched_t *cell;
  mac_top_init_gNB(ngran_gNB, scc, &conf, &rlc_config, &cell);
  RC.nrmac[0]->if_inst->NR_PHY_config_req = phy_config_req_stub;
  cell->beam_info = (NR_beam_info_t){.beam_mode = NO_BEAM_MODE, .beams_per_period = 1};
  nr_mac_config_scc(RC.nrmac[0], cell, scc, &conf);

  const plmn_id_t plmn = {.mcc = 1, .mnc = 1, .mnc_digit_length = 2};
  nr_mac_configure_sib1(cell, &plmn, 12345678, 1);
  f1ap_sib_msg_t sib2 = encode_sib2();
  AssertFatal(nr_mac_configure_other_sib(cell, 1, &sib2), "could not configure SIB2\n");
  free(sib2.SI_container.buf);

  NR_Sched_Rsp_t *rsp = calloc_or_fail(1, sizeof(*rsp));
  nfapi_nr_dl_tti_request_body_t *dl = &rsp->DL_req.dl_tti_request_body;
  const int slots_per_frame = cell->frame_structure.numb_slots_frame;
  int checked = 0;
  // other SI periodicity is 16 frames: run two periods
  for (int frame = 0; frame < 32; frame++) {
    for (int slot = 0; slot < slots_per_frame; slot++) {
      reset_sched_response(rsp, frame, slot, 0, 0);
      memset(cell->common_channels.vrb_map[0], 0, sizeof(uint16_t) * MAX_BWP_SIZE);
      clear_nr_nfapi_information(cell, frame, slot);
      schedule_nr_mib(cell, frame, slot, &rsp->DL_req);
      schedule_nr_sib1(cell, frame, slot, &rsp->DL_req, &rsp->TX_req);
      const int first_pdu = dl->nPDUs;
      schedule_nr_other_sib(cell, frame, slot, &rsp->DL_req, &rsp->TX_req);
      for (int i = first_pdu; i < dl->nPDUs; i++) {
        if (dl->dl_tti_pdu_list[i].PDUType != NFAPI_NR_DL_TTI_PDCCH_PDU_TYPE)
          continue;
        const nfapi_nr_dl_tti_pdcch_pdu_rel15_t *pdcch = &dl->dl_tti_pdu_list[i].pdcch_pdu.pdcch_pdu_rel15;
        printf("bitmap 0x%lx %d.%d other SI PDCCH BWPStart %d BWPSize %d (CORESET0 start %d size %d)\n",
               ssb_bitmap, frame, slot, pdcch->BWPStart, pdcch->BWPSize, cell->cset0_bwp_start, cell->cset0_bwp_size);
        if (pdcch->BWPSize != cell->cset0_bwp_size || pdcch->BWPStart != cell->cset0_bwp_start)
          return -1;
        checked++;
      }
    }
  }
  free(rsp);
  return checked;
}

// argument: ssb_PositionsInBurst bitmap, bit i = SSB index i (e.g. 0x2: only SSB index 1)
int main(int argc, char **argv)
{
  AssertFatal(argc == 2, "usage: %s <ssb bitmap>\n", argv[0]);
  const uint64_t ssb_bitmap = strtoull(argv[1], NULL, 0);
  logInit();
  set_glog(OAILOG_ERR);
  get_softmodem_params()->phy_test = 0;
  get_softmodem_params()->do_ra = 0;
  get_softmodem_params()->nsa = 0;

  int checked = run_cell(ssb_bitmap);
  if (checked <= 0) {
    printf("FAIL bitmap 0x%lx: %s\n", ssb_bitmap, checked < 0 ? "other SI DCI not in CORESET0" : "no other SI scheduled");
    return 1;
  }
  printf("OK bitmap 0x%lx: %d other SI DCIs in CORESET0\n", ssb_bitmap, checked);
  return 0;
}
