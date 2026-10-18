/*
 * SPDX-License-Identifier: LicenseRef-CSSL-1.0
 */

/* Conditional reconfiguration (conditional handover) of the MCG, TS 38.331 5.3.5.13: storage of the
 * candidate cells in VarConditionalReconfig, evaluation of their execution conditions (condEventA3,
 * condEventT1) and selection of the cell to execute. */

#include "rrc_UE_cho.h"
#include <limits.h>
#include "rrc_proto.h"
#include "nr_ue_gnss.h"
#include "oai_asn1.h"
#include "NR_RRCReconfiguration.h"
#include "NR_CellGroupConfig.h"

/** @return PCI of the reconfigurationWithSync of the masterCellGroup of an RRCReconfiguration, -1 if none */
static int get_applicable_pci(const byte_array_t *reconfig)
{
  int pci = -1;
  NR_RRCReconfiguration_t *rec = NULL;
  asn_dec_rval_t dec = uper_decode_complete(NULL, &asn_DEF_NR_RRCReconfiguration, (void **)&rec, reconfig->buf, reconfig->len);
  if (dec.code == RC_OK && rec->criticalExtensions.present == NR_RRCReconfiguration__criticalExtensions_PR_rrcReconfiguration) {
    const NR_RRCReconfiguration_IEs_t *ies = rec->criticalExtensions.choice.rrcReconfiguration;
    const OCTET_STRING_t *mcg = ies->nonCriticalExtension ? ies->nonCriticalExtension->masterCellGroup : NULL;
    NR_CellGroupConfig_t *cg = NULL;
    if (mcg && uper_decode_complete(NULL, &asn_DEF_NR_CellGroupConfig, (void **)&cg, mcg->buf, mcg->size).code == RC_OK) {
      const NR_SpCellConfig_t *sp = cg->spCellConfig;
      if (sp && sp->reconfigurationWithSync && sp->reconfigurationWithSync->spCellConfigCommon
          && sp->reconfigurationWithSync->spCellConfigCommon->physCellId)
        pci = *sp->reconfigurationWithSync->spCellConfigCommon->physCellId;
    }
    ASN_STRUCT_FREE(asn_DEF_NR_CellGroupConfig, cg);
  }
  ASN_STRUCT_FREE(asn_DEF_NR_RRCReconfiguration, rec);
  return pci;
}

static nr_cond_reconfig_t *find_cand(nr_var_cond_reconfig_t *var, long id)
{
  for (int i = 0; i < NR_maxNrofCondCells_r16; i++) {
    if (var->cand[i].id == id)
      return &var->cand[i];
  }
  return NULL;
}

static void reset_evaluation(nr_cond_reconfig_t *c)
{
  for (int k = 0; k < 2; k++) {
    c->fulfilled[k] = false;
    c->enter_ms[k] = 0;
    c->leave_ms[k] = 0;
  }
}

static void free_cand(nr_cond_reconfig_t *c)
{
  free_byte_array(c->rrc_reconfig);
  memset(c, 0, sizeof(*c));
}

void nr_rrc_cho_configure(NR_UE_RRC_INST_t *rrc, const NR_ConditionalReconfiguration_r16_t *cr)
{
  nr_var_cond_reconfig_t *var = &rrc->cond_reconfig;
  var->attempt_cond_reconfig = cr->attemptCondReconfig_r16 != NULL;

  // 5.3.5.13.2 conditional reconfiguration removal
  if (cr->condReconfigToRemoveList_r16) {
    for (int i = 0; i < cr->condReconfigToRemoveList_r16->list.count; i++) {
      nr_cond_reconfig_t *c = find_cand(var, *cr->condReconfigToRemoveList_r16->list.array[i]);
      if (c) {
        RRCLOG_I("CHO: remove candidate %ld (PCI %d)\n", c->id, c->pci);
        free_cand(c);
      }
    }
  }

  // 5.3.5.13.3 conditional reconfiguration addition/modification
  if (cr->condReconfigToAddModList_r16) {
    for (int i = 0; i < cr->condReconfigToAddModList_r16->list.count; i++) {
      const NR_CondReconfigToAddMod_r16_t *in = cr->condReconfigToAddModList_r16->list.array[i];
      nr_cond_reconfig_t *c = find_cand(var, in->condReconfigId_r16);
      if (!c) {
        c = find_cand(var, 0);
        if (!c || !in->condRRCReconfig_r16) {
          RRCLOG_E("CHO: cannot add candidate %ld\n", in->condReconfigId_r16);
          continue;
        }
        c->id = in->condReconfigId_r16;
      }
      if (in->condExecutionCond_r16) {
        c->num_meas_id = min(in->condExecutionCond_r16->list.count, 2);
        for (int k = 0; k < c->num_meas_id; k++)
          c->meas_id[k] = *in->condExecutionCond_r16->list.array[k];
        reset_evaluation(c);
      }
      if (in->condRRCReconfig_r16) {
        free_byte_array(c->rrc_reconfig);
        c->rrc_reconfig = create_byte_array(in->condRRCReconfig_r16->size, in->condRRCReconfig_r16->buf);
        c->pci = get_applicable_pci(&c->rrc_reconfig);
        c->rx_hfn = rrc->current_hfn;
        c->rx_frame = rrc->current_frame;
      }
      RRCLOG_I("CHO: candidate %ld, applicable cell PCI %d, execution condition measId %ld%s%ld\n",
               c->id,
               c->pci,
               c->meas_id[0],
               c->num_meas_id > 1 ? " and " : "",
               c->num_meas_id > 1 ? c->meas_id[1] : 0);
    }
  }
}

void nr_rrc_cho_meas_id_modified(NR_UE_RRC_INST_t *rrc, long meas_id)
{
  for (int i = 0; i < NR_maxNrofCondCells_r16; i++) {
    nr_cond_reconfig_t *c = &rrc->cond_reconfig.cand[i];
    for (int k = 0; c->id && k < c->num_meas_id; k++) {
      if (c->meas_id[k] == meas_id) {
        c->fulfilled[k] = false;
        c->enter_ms[k] = 0;
        c->leave_ms[k] = 0;
      }
    }
  }
}

bool nr_rrc_cho_configured(const NR_UE_RRC_INST_t *rrc)
{
  for (int i = 0; i < NR_maxNrofCondCells_r16; i++) {
    if (rrc->cond_reconfig.cand[i].id)
      return true;
  }
  return false;
}

void nr_rrc_cho_release(NR_UE_RRC_INST_t *rrc)
{
  for (int i = 0; i < NR_maxNrofCondCells_r16; i++) {
    if (rrc->cond_reconfig.cand[i].id)
      free_cand(&rrc->cond_reconfig.cand[i]);
  }
  rrc->cond_reconfig.attempt_cond_reconfig = false;
}

/* cell individual offset (dB) of a cell in a measurement object, 0 if not configured */
static int get_cell_offset_db(const NR_MeasObjectToAddMod_t *mo, int pci)
{
  static const int q_offset_db[] = {-24, -22, -20, -18, -16, -14, -12, -10, -8, -6, -5, -4, -3, -2, -1, 0,
                                    1,   2,   3,   4,   5,   6,   8,   10,  12, 14, 16, 18, 20, 22, 24};
  if (!mo || mo->measObject.present != NR_MeasObjectToAddMod__measObject_PR_measObjectNR)
    return 0;
  const NR_CellsToAddModList_t *cells = mo->measObject.choice.measObjectNR->cellsToAddModList;
  for (int i = 0; cells && i < cells->list.count; i++) {
    const NR_CellsToAddMod_t *cell = cells->list.array[i];
    if (cell->physCellId == pci && cell->cellIndividualOffset.rsrpOffsetSSB)
      return q_offset_db[*cell->cellIndividualOffset.rsrpOffsetSSB];
  }
  return 0;
}

/* trigger condition with time to trigger (ms): fulfilled once the entering condition has been met for
 * time_to_trigger, no longer fulfilled once the leaving condition has been met for time_to_trigger */
static void update_condition(nr_cond_reconfig_t *c, int k, bool entering, bool leaving, int time_to_trigger, int step_ms)
{
  if (!c->fulfilled[k]) {
    c->enter_ms[k] = entering ? c->enter_ms[k] + step_ms : 0;
    if (entering && c->enter_ms[k] >= time_to_trigger) {
      c->fulfilled[k] = true;
      c->leave_ms[k] = 0;
    }
  } else {
    c->leave_ms[k] = leaving ? c->leave_ms[k] + step_ms : 0;
    if (leaving && c->leave_ms[k] >= time_to_trigger) {
      c->fulfilled[k] = false;
      c->enter_ms[k] = 0;
    }
  }
}

static const meas_t *get_neighbour_meas(const l3_measurements_t *l3, int pci)
{
  for (int i = 0; i < NUMBER_OF_NEIGHBORING_CELLS_MAX; i++) {
    if (l3->neighboring_cell[i].Nid_cell == pci)
      return &l3->neighboring_cell[i];
  }
  return NULL;
}

/* 5.5.4.4 (condEventA3): neighbour (applicable cell) becomes offset better than the SpCell.
 * Offsets and hysteresis in 0.5 dB, Ofn = Ofp (same measurement object), Ocn/Ocp cell individual offsets */
static void evaluate_cond_a3(const rrcPerNB_t *nb,
                             nr_cond_reconfig_t *c,
                             int k,
                             const NR_MeasObjectToAddMod_t *mo,
                             const struct NR_CondTriggerConfig_r16__condEventId__condEventA3 *a3,
                             int pci_sp,
                             int step_ms)
{
  if (a3->a3_Offset.present != NR_MeasTriggerQuantityOffset_PR_rsrp)
    return;
  const l3_measurements_t *l3 = &nb->l3_measurements;
  const meas_t *neighbour = get_neighbour_meas(l3, c->pci);
  const int mp = nr_rrc_get_l3_rsrp(&l3->serving_cell);
  const int mn = neighbour ? nr_rrc_get_l3_rsrp(neighbour) : INT_MAX;
  const int off = a3->a3_Offset.choice.rsrp;
  const int hys = a3->hysteresis;
  bool entering = false;
  bool leaving = true; // the leaving condition holds while the applicable cell is not measured
  if (mp != INT_MAX && mn != INT_MAX) {
    const int lhs = 2 * (mn + get_cell_offset_db(mo, c->pci));
    const int rhs = 2 * (mp + get_cell_offset_db(mo, pci_sp)) + off;
    entering = lhs - hys > rhs; // A3-1
    leaving = lhs + hys < rhs; // A3-2
  }
  const bool was = c->fulfilled[k];
  update_condition(c, k, entering, leaving, get_event_time_to_trigger(a3->timeToTrigger), step_ms);
  if (was != c->fulfilled[k])
    LOG_I(NR_RRC,
          "CHO: candidate %ld PCI %d condEventA3 (measId %ld) %s: Mn %d dBm, Mp %d dBm, offset %.1f dB, hysteresis %.1f dB\n",
          c->id,
          c->pci,
          c->meas_id[k],
          c->fulfilled[k] ? "fulfilled" : "no longer fulfilled",
          mn,
          mp,
          off / 2.0,
          hys / 2.0);
}

/* 5.5.4.16 (condEventT1): the UTC time Mt measured at the UE (ms) is in (Thresh1, Thresh1 + Duration],
 * t1-Threshold in 10 ms since 1900, duration in 100 ms; no time to trigger */
static void evaluate_cond_t1(nr_cond_reconfig_t *c, int k, const struct NR_CondTriggerConfig_r16__condEventId__condEventT1_r17 *t1)
{
  uint64_t thresh = 0;
  if (asn_INTEGER2uint64(&t1->t1_Threshold_r17, &thresh) != 0)
    return;
  const uint64_t mt = nr_ue_gnss_utc_ms();
  const uint64_t thresh_ms = thresh * 10;
  const uint64_t end_ms = thresh_ms + t1->duration_r17 * 100;
  const bool was = c->fulfilled[k];
  c->fulfilled[k] = mt > thresh_ms && !(mt > end_ms); // T1-1 and not T1-2
  if (was != c->fulfilled[k])
    LOG_I(NR_RRC,
          "CHO: candidate %ld PCI %d condEventT1 (measId %ld) %s: Mt %lu ms, window %lu-%lu ms (UTC since 1900)\n",
          c->id,
          c->pci,
          c->meas_id[k],
          c->fulfilled[k] ? "fulfilled" : "no longer fulfilled",
          mt,
          thresh_ms,
          end_ms);
}

int nr_rrc_cho_evaluate(NR_UE_RRC_INST_t *rrc, int gnb_index, int step_ms)
{
  const rrcPerNB_t *nb = &rrc->perNB[gnb_index];
  int selected = -1;
  int best_rsrp = INT_MIN;
  // 5.3.5.13.4 conditional reconfiguration evaluation
  for (int i = 0; i < NR_maxNrofCondCells_r16; i++) {
    nr_cond_reconfig_t *c = &rrc->cond_reconfig.cand[i];
    if (!c->id || c->pci < 0 || c->num_meas_id == 0)
      continue;
    bool triggered = true;
    for (int k = 0; k < c->num_meas_id; k++) {
      const NR_MeasIdToAddMod_t *meas_id = c->meas_id[k] < NR_MAX_MEAS_ID ? nb->MeasId[c->meas_id[k]] : NULL;
      const NR_ReportConfigToAddMod_t *rc = meas_id ? nb->ReportConfig[meas_id->reportConfigId] : NULL;
      if (!rc || rc->reportConfig.present != NR_ReportConfigToAddMod__reportConfig_PR_reportConfigNR
          || rc->reportConfig.choice.reportConfigNR->reportType.present != NR_ReportConfigNR__reportType_PR_condTriggerConfig_r16) {
        triggered = false;
        continue;
      }
      const NR_CondTriggerConfig_r16_t *cond = rc->reportConfig.choice.reportConfigNR->reportType.choice.condTriggerConfig_r16;
      switch (cond->condEventId.present) {
        case NR_CondTriggerConfig_r16__condEventId_PR_condEventA3:
          evaluate_cond_a3(nb,
                           c,
                           k,
                           nb->MeasObj[meas_id->measObjectId],
                           cond->condEventId.choice.condEventA3,
                           rrc->phyCellID,
                           step_ms);
          break;
        case NR_CondTriggerConfig_r16__condEventId_PR_condEventT1_r17:
          evaluate_cond_t1(c, k, cond->condEventId.choice.condEventT1_r17);
          break;
        default:
          // condEventA4, condEventA5, condEventD1: not supported, never fulfilled
          break;
      }
      triggered &= c->fulfilled[k];
    }
    if (!triggered)
      continue;
    // 5.3.5.13.5: among several triggered cells, the UE selects one, here the strongest
    const meas_t *m = get_neighbour_meas(&nb->l3_measurements, c->pci);
    const int rsrp = m ? nr_rrc_get_l3_rsrp(m) : INT_MAX;
    const int rank = rsrp == INT_MAX ? INT_MIN + 1 : rsrp;
    if (selected < 0 || rank > best_rsrp) {
      selected = i;
      best_rsrp = rank;
    }
  }
  return selected;
}
