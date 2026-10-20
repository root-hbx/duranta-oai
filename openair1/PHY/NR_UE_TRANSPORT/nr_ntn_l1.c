/*
 * SPDX-License-Identifier: LicenseRef-CSSL-1.0
 */

#include "PHY/NR_UE_TRANSPORT/nr_transport_proto_ue.h"

/* target cell: the epoch SFN is the one nearest to the frame in which the message with the epoch time was
 * received (TS 38.331 NTN-Config epochTime), e.g., much earlier for a conditional handover */
static int get_target_epoch_hfn(const fapi_nr_ntn_config_t *cfg)
{
  const int ref_frame = cfg->epoch_ref_frame;
  const int diff1 = (ref_frame - cfg->epoch_sfn + 1024) % 1024;
  const int diff2 = (cfg->epoch_sfn - ref_frame + 1024) % 1024;
  if (diff1 < diff2) // epoch before the reception
    return cfg->epoch_sfn <= ref_frame ? cfg->epoch_ref_hfn : cfg->epoch_ref_hfn - 1;
  return cfg->epoch_sfn >= ref_frame ? cfg->epoch_ref_hfn : cfg->epoch_ref_hfn + 1;
}

void fix_ntn_epoch_hfn(PHY_VARS_NR_UE *UE, int hfn, int frame)
{
  fapi_nr_ntn_config_t *cfg = &UE->nrUE_config.ntn_config;
  if (!cfg->is_targetcell)
    return;
  cfg->is_targetcell = false;
  cfg->epoch_hfn = get_target_epoch_hfn(cfg);
  const int frames_since_epoch = (hfn - cfg->epoch_hfn) * 1024 + frame - cfg->epoch_sfn;
  LOG_I(PHY,
        "epoch %d.%d (HFN.SFN) received at %d.%d, %d frames in the %s\n",
        cfg->epoch_hfn,
        cfg->epoch_sfn,
        cfg->epoch_ref_hfn,
        cfg->epoch_ref_frame,
        abs(frames_since_epoch),
        frames_since_epoch >= 0 ? "past" : "future");
}

/* DL Doppler of the target cell at its synchronization, at abs_subframe (the epoch can be long ago, e.g.,
 * conditional handover) */
void apply_ntn_doppler_target_cell(PHY_VARS_NR_UE *UE, const NR_DL_FRAME_PARMS *fp, int abs_subframe)
{
  UE->nrUE_config.ntn_config.epoch_hfn = get_target_epoch_hfn(&UE->nrUE_config.ntn_config);
  const int timing_advance_ntn = UE->timing_advance_ntn;
  apply_ntn_timing_advance_and_doppler(UE, fp, abs_subframe);
  UE->timing_advance_ntn = timing_advance_ntn;
}

// projected velocity of the satellite towards the UE ms_since_epoch after the epoch, and their distance
static double get_sat_velocity_towards_ue(const PHY_VARS_NR_UE *UE,
                                          const fapi_nr_ntn_config_t *ntn_config_params,
                                          int ms_since_epoch,
                                          double *distance)
{
  const position_t pos_sat_0 = ntn_config_params->pos_sat_0;
  const position_t vel_sat_0 = ntn_config_params->vel_sat_0;

  position_t pos_sat;
  position_t vel_sat;

  const double omega = ntn_config_params->omega;
  if (omega) {
    const double cos_wt = cos(omega * ms_since_epoch);
    const double sin_wt = sin(omega * ms_since_epoch);

    const position_t pos_sat_90 = ntn_config_params->pos_sat_90;
    pos_sat = (position_t){pos_sat_0.X * cos_wt + pos_sat_90.X * sin_wt,
                           pos_sat_0.Y * cos_wt + pos_sat_90.Y * sin_wt,
                           pos_sat_0.Z * cos_wt + pos_sat_90.Z * sin_wt};

    const position_t vel_sat_90 = ntn_config_params->vel_sat_90;
    vel_sat = (position_t){vel_sat_0.X * cos_wt + vel_sat_90.X * sin_wt,
                           vel_sat_0.Y * cos_wt + vel_sat_90.Y * sin_wt,
                           vel_sat_0.Z * cos_wt + vel_sat_90.Z * sin_wt};
  } else {
    pos_sat = (position_t){pos_sat_0.X + vel_sat_0.X * ms_since_epoch / 1000,
                           pos_sat_0.Y + vel_sat_0.Y * ms_since_epoch / 1000,
                           pos_sat_0.Z + vel_sat_0.Z * ms_since_epoch / 1000};
    vel_sat = vel_sat_0;
  }

  position_t pos_ue = {0};
  get_position_coordinates(UE->Mod_id, &pos_ue);

  // calculate directional vector from SAT to UE
  const position_t dir_sat_ue = {pos_ue.X - pos_sat.X, pos_ue.Y - pos_sat.Y, pos_ue.Z - pos_sat.Z};

  // calculate distance between SAT and UE
  *distance = sqrt(dir_sat_ue.X * dir_sat_ue.X + dir_sat_ue.Y * dir_sat_ue.Y + dir_sat_ue.Z * dir_sat_ue.Z);

  // calculate projected velocity from SAT towards UE
  const double vel_sat_ue = (vel_sat.X * dir_sat_ue.X + vel_sat.Y * dir_sat_ue.Y + vel_sat.Z * dir_sat_ue.Z) / *distance;

  return vel_sat_ue;
}

// calculate TA and Doppler based on the NTN-Config, if abs_subframe_tx < 0 then apply only Doppler
void apply_ntn_timing_advance_and_doppler(PHY_VARS_NR_UE *UE, const NR_DL_FRAME_PARMS *fp, int abs_subframe_tx)
{
  const fapi_nr_ntn_config_t *ntn_config_params = &UE->nrUE_config.ntn_config;

  // Handle terrestrial networks
  if (ntn_config_params->cell_specific_k_offset == 0) {
    if (abs_subframe_tx >= 0)
      UE->timing_advance_ntn = 0;
    UE->dl_Doppler_shift = 0;
    UE->ul_Doppler_shift = 0;
    return;
  }

  const int abs_subframe_epoch = ntn_config_params->epoch_subframe
                               + ntn_config_params->epoch_sfn * 10
                               + ntn_config_params->epoch_hfn * 10240;
  const int ms_since_epoch = abs_subframe_tx < 0 ? 0 : abs_subframe_tx - abs_subframe_epoch;

  double distance;
  const double vel_sat_ue = get_sat_velocity_towards_ue(UE, ntn_config_params, ms_since_epoch, &distance);

  // calculate DL Doppler shift (moving source)
  const double dl_Doppler_shift = (vel_sat_ue / (SPEED_OF_LIGHT - vel_sat_ue)) * fp->dl_CarrierFreq;

  // calculate UL Doppler shift (moving target)
  const double ul_Doppler_shift = (vel_sat_ue / SPEED_OF_LIGHT) * fp->ul_CarrierFreq;

  // calculate round-trip-time (factor 2) between SAT and UE in ms (factor 1000)
  const double N_UE_TA_adj = 2000 * distance / SPEED_OF_LIGHT;

  const double N_common_ta_adj = ntn_config_params->N_common_ta_adj;
  const double N_common_ta_drift = ntn_config_params->N_common_ta_drift;
  const double N_common_ta_drift_variant = ntn_config_params->N_common_ta_drift_variant;

  if (abs_subframe_tx >= 0) {
    UE->timing_advance_ntn = (N_UE_TA_adj + N_common_ta_adj + N_common_ta_drift * ms_since_epoch / 1e6
                              + N_common_ta_drift_variant * ((int64_t)ms_since_epoch * ms_since_epoch) / 1e9)
                             * fp->samples_per_subframe;
  }

  UE->freq_offset -= dl_Doppler_shift - UE->dl_Doppler_shift;
  UE->dl_Doppler_shift = dl_Doppler_shift;
  UE->ul_Doppler_shift = ul_Doppler_shift;

  if (abs_subframe_tx < 0) {
    LOG_I(PHY,
          "satellite velocity towards UE = %f m/s, DL Doppler shift = %f kHz, UL Doppler shift = %f kHz\n",
          vel_sat_ue,
          dl_Doppler_shift / 1000,
          ul_Doppler_shift / 1000);
  }
}

/* DL Doppler shift of a neighbour cell at abs_subframe, from its own ntn-Config (e.g., a conditional handover
 * candidate) */
double get_ntn_neighbour_dl_doppler(const PHY_VARS_NR_UE *UE,
                                    const fapi_nr_ntn_config_t *cfg,
                                    const NR_DL_FRAME_PARMS *fp,
                                    int abs_subframe)
{
  const int abs_subframe_epoch = cfg->epoch_subframe + cfg->epoch_sfn * 10 + get_target_epoch_hfn(cfg) * 10240;
  double distance;
  const double vel_sat_ue = get_sat_velocity_towards_ue(UE, cfg, abs_subframe - abs_subframe_epoch, &distance);
  return (vel_sat_ue / (SPEED_OF_LIGHT - vel_sat_ue)) * fp->dl_CarrierFreq;
}

// apply values based on the NTN-Config
void apply_ntn_config(PHY_VARS_NR_UE *UE,
                      const NR_DL_FRAME_PARMS *fp,
                      int hfn_rx,
                      int frame_rx,
                      int slot_rx,
                      int *duration_rx_to_tx,
                      int *timing_advance,
                      int *ntn_koffset,
                      bool *ntn_targetcell)
{
  // return if there is no pending update
  if (!UE->nrUE_config.ntn_config.params_changed)
    return;

  *ntn_targetcell = UE->nrUE_config.ntn_config.is_targetcell;

  // skip update if it is for the target cell
  if (UE->nrUE_config.ntn_config.is_targetcell)
    return;

  UE->nrUE_config.ntn_config.params_changed = false;

  const fapi_nr_ntn_config_t *ntn_config_params = &UE->nrUE_config.ntn_config;

  const int mu = fp->numerology_index;
  const int koffset = ntn_config_params->cell_specific_k_offset;

  *duration_rx_to_tx = NR_UE_CAPABILITY_SLOT_RX_TO_TX + (koffset << mu);
  if (koffset > *ntn_koffset)
    *timing_advance += get_samples_slot_duration(fp, slot_rx, (koffset - *ntn_koffset) << mu);
  else if (koffset < *ntn_koffset)
    *timing_advance -= get_samples_slot_duration(fp, slot_rx, (*ntn_koffset - koffset) << mu);
  *ntn_koffset = koffset;

  const int abs_subframe_tx = 10240 * hfn_rx + 10 * frame_rx + ((slot_rx + *duration_rx_to_tx) >> mu);
  apply_ntn_timing_advance_and_doppler(UE, fp, abs_subframe_tx);

  LOG_I(PHY,
        "k_offset: %dms, N_Common_Ta: %fms, drift: %fµs/s, variant: %fµs/s², "
        "timing_advance_ntn: %d samples, DL Doppler shift: %fkHz, UL Doppler shift: %fkHz\n",
        koffset << mu,
        ntn_config_params->N_common_ta_adj,
        ntn_config_params->N_common_ta_drift,
        ntn_config_params->N_common_ta_drift_variant,
        UE->timing_advance_ntn,
        UE->dl_Doppler_shift / 1000,
        UE->ul_Doppler_shift / 1000);
}
