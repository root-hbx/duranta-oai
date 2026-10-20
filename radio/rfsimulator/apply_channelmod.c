/*
 * SPDX-License-Identifier: LicenseRef-CSSL-1.0
 */

#include <complex.h>
#include <pthread.h>
#include <common/utils/LOG/log.h>
#include <openair1/SIMULATION/TOOLS/sim.h>
#include "openair2/LAYER2/NR_MAC_gNB/mac_config.h"
#include "rfsimulator.h"

static double vec_dist(const double a[3], const double b[3])
{
  const double dx = a[0] - b[0], dy = a[1] - b[1], dz = a[2] - b[2];
  return sqrt(dx * dx + dy * dy + dz * dz);
}

// Radial velocity of the satellite (pos_sat, vel_sat) w.r.t. a static ground point, >0 when moving away.
static double range_rate(const double pos_gnd[3], const double pos_sat[3], const double vel_sat[3])
{
  const double dist = vec_dist(pos_sat, pos_gnd);
  double v = 0;
  for (int k = 0; k < 3; k++)
    v += vel_sat[k] * (pos_sat[k] - pos_gnd[k]);
  return v / dist;
}

// Satellite position and velocity at time t (s), interpolated from the orbit trace.
// Clamped to the first/last sample outside of the trace.
static void interp_sat_trace(channel_desc_t *cd, double t, double pos[3], double vel[3])
{
  const sat_trace_sample_t *tr = cd->sat_trace;
  const int n = cd->sat_trace_len;
  if (n == 1 || t <= tr[0].t || t >= tr[n - 1].t) {
    const sat_trace_sample_t *smp = (n == 1 || t <= tr[0].t) ? &tr[0] : &tr[n - 1];
    memcpy(pos, smp->pos, sizeof(smp->pos));
    memcpy(vel, smp->vel, sizeof(smp->vel));
    return;
  }

  // time is mostly monotonic: start the search from the last interval used
  int idx = cd->sat_trace_last_idx;
  if (idx >= n - 1)
    idx = 0;
  while (idx < n - 2 && tr[idx + 1].t < t)
    idx++;
  while (idx > 0 && tr[idx].t > t)
    idx--;
  cd->sat_trace_last_idx = idx;

  const sat_trace_sample_t *a = &tr[idx];
  const sat_trace_sample_t *b = &tr[idx + 1];
  const double frac = cd->sat_interp_zoh ? 0.0 : (t - a->t) / (b->t - a->t);
  for (int k = 0; k < 3; k++) {
    pos[k] = a->pos[k] + frac * (b->pos[k] - a->pos[k]);
    vel[k] = a->vel[k] + frac * (b->vel[k] - a->vel[k]);
  }
}

// Distance, velocity and acceleration of a ground point to satellite link, such that the distance is exact at
// t, t + 5 s and t + 10 s (same approximation as for the built-in orbit, used for SIB19 ta-Common/Drift/DriftVariant):
//   d(t +  5) = d(t) +  5 * vel +  25 * acc
//   d(t + 10) = d(t) + 10 * vel + 100 * acc
static void trace_link_dynamics(channel_desc_t *cd, double t, const double pos_gnd[3], double *dist, double *vel, double *acc)
{
  const int last_idx = cd->sat_trace_last_idx;
  double pos_sat[3], vel_sat[3];
  interp_sat_trace(cd, t, pos_sat, vel_sat);
  const double d0 = vec_dist(pos_gnd, pos_sat);
  interp_sat_trace(cd, t + 5, pos_sat, vel_sat);
  const double d5 = vec_dist(pos_gnd, pos_sat);
  interp_sat_trace(cd, t + 10, pos_sat, vel_sat);
  const double d10 = vec_dist(pos_gnd, pos_sat);
  // keep the lookup position for the next call at time ~t
  cd->sat_trace_last_idx = last_idx;

  *dist = d0;
  *vel = 2 * (d5 - d0) / 5 - (d10 - d0) / 10;
  // when the satellite disappears behind the horizon acc might become negative, what is invalid
  *acc = fmax((d10 - d0) / 50 - (d5 - d0) / 25, 0);
}

// Common TA prediction for the gNB MAC (dedicated ntn-Config of time-based CHO candidates): the trace of the uplink
// link that updates SIB19, and the epoch of its last SIB19 update
static struct {
  pthread_mutex_t mutex;
  sat_trace_sample_t *trace;
  int len;
  double utc_ms;
  double pos_gnb[3];
  bool transparent;
  gnb_sat_position_update_t epoch;
  double t_epoch;
} ta_pred = {.mutex = PTHREAD_MUTEX_INITIALIZER};

// Satellite position at time t (s) of the trace, linear interpolation, false outside of the trace
static bool trace_position(const sat_trace_sample_t *tr, int n, double t, double pos[3])
{
  if (n < 2 || t < tr[0].t || t > tr[n - 1].t)
    return false;
  int lo = 0, hi = n - 1;
  while (hi - lo > 1) {
    const int mid = (lo + hi) / 2;
    if (tr[mid].t <= t)
      lo = mid;
    else
      hi = mid;
  }
  const double frac = (t - tr[lo].t) / (tr[hi].t - tr[lo].t);
  for (int k = 0; k < 3; k++)
    pos[k] = tr[lo].pos[k] + frac * (tr[hi].pos[k] - tr[lo].pos[k]);
  return true;
}

// nr_ntn_ta_predictor_t: ta-Common (feeder link round trip) y(x) = d0 + drift * x + variant * x^2, x the time since the
// epoch, exact at the Chebyshev nodes of the window (smallest maximum error). Within the TA-Info ranges of 38.331:
// variant >= 0, else a line; drift clamped, then exact at the outer nodes.
static bool predict_ta_common(uint64_t utc_start_ms, uint64_t utc_end_ms, gnb_sat_position_update_t *out, uint64_t *epoch_utc_ms)
{
  pthread_mutex_lock(&ta_pred.mutex);
  *out = ta_pred.epoch;
  const double t_epoch = ta_pred.t_epoch;
  pthread_mutex_unlock(&ta_pred.mutex);
  *epoch_utc_ms = ta_pred.utc_ms + t_epoch * 1000;
  if (!ta_pred.transparent)
    return true;

  const double t_start = (utc_start_ms - ta_pred.utc_ms) / 1000;
  const double t_end = (utc_end_ms - ta_pred.utc_ms) / 1000;
  const double t_mid = (t_start + t_end) / 2;
  const double half = (t_end - t_start) / 2 * cos(M_PI / 6);
  const double t_node[3] = {t_mid - half, t_mid, t_mid + half};
  double x[3], y[3];
  for (int i = 0; i < 3; i++) {
    double pos_sat[3];
    if (!trace_position(ta_pred.trace, ta_pred.len, t_node[i], pos_sat))
      return false;
    x[i] = t_node[i] - t_epoch;
    y[i] = 2 * vec_dist(ta_pred.pos_gnb, pos_sat) / SPEED_OF_LIGHT;
  }
  const double drift_max = 257303 * 0.2e-9;
  const double slope01 = (y[1] - y[0]) / (x[1] - x[0]);
  const double slope12 = (y[2] - y[1]) / (x[2] - x[1]);
  double variant = (slope12 - slope01) / (x[2] - x[0]);
  double drift = slope01 - variant * (x[0] + x[1]);
  if (variant < 0) {
    variant = 0;
    drift = (y[2] - y[0]) / (x[2] - x[0]);
  }
  if (fabs(drift) > drift_max) {
    drift = copysign(drift_max, drift);
    variant = (y[2] - y[0] - drift * (x[2] - x[0])) / (x[2] * x[2] - x[0] * x[0]);
  }
  const double d0 = y[0] - drift * x[0] - variant * x[0] * x[0];

  // SIB19 units and ranges (38.331 TA-Info)
  const long ta_common = lround(d0 / 4.072e-9);
  const long ta_drift = lround(drift / 0.2e-9);
  const long ta_variant = lround(variant / 0.2e-10);
  if (ta_common < 0 || ta_common > 66485757 || labs(ta_drift) > 257303 || ta_variant < 0 || ta_variant > 28949)
    return false;
  out->delay = ta_common;
  out->drift = ta_drift;
  out->accel = ta_variant;

  double max_err = 0;
  for (double t = t_start; t <= t_end; t += 0.5) {
    double pos_sat[3];
    trace_position(ta_pred.trace, ta_pred.len, t, pos_sat);
    const double xt = t - t_epoch;
    const double err = d0 + drift * xt + variant * xt * xt - 2 * vec_dist(ta_pred.pos_gnb, pos_sat) / SPEED_OF_LIGHT;
    max_err = fmax(max_err, fabs(err));
  }
  LOG_I(HW, "Common TA prediction for %.1f-%.1f s (trace time): max error %.3f us\n", t_start, t_end, max_err * 1e6);
  return true;
}

static void update_ta_prediction(const channel_desc_t *cd, bool transparent, const gnb_sat_position_update_t *epoch, double t_epoch)
{
  if (cd->sat_trace_utc_ms == 0)
    return;
  pthread_mutex_lock(&ta_pred.mutex);
  const bool first = ta_pred.trace == NULL;
  if (first) {
    ta_pred.trace = malloc_or_fail(cd->sat_trace_len * sizeof(*ta_pred.trace));
    memcpy(ta_pred.trace, cd->sat_trace, cd->sat_trace_len * sizeof(*ta_pred.trace));
    ta_pred.len = cd->sat_trace_len;
    ta_pred.utc_ms = cd->sat_trace_utc_ms;
    memcpy(ta_pred.pos_gnb, cd->pos_gnb, sizeof(ta_pred.pos_gnb));
    ta_pred.transparent = transparent;
  }
  ta_pred.epoch = *epoch;
  ta_pred.t_epoch = t_epoch;
  pthread_mutex_unlock(&ta_pred.mutex);
  if (first)
    nr_register_ntn_ta_predictor(predict_ta_common);
}

// LEO satellite channel driven by an orbit trace (satellite ECEF position/velocity over time).
// Service link: UE <-> SAT. Feeder link: SAT <-> gNB at pos_gnb (SAT_LEO_TRANS only).
// - Delay, which the UE compensates in its TA (38.300 16.14.2.2):
//   - SAT_LEO_TRANS: service link + feeder link, the feeder link part is signalled as Common TA in SIB19.
//   - SAT_LEO_REGEN: service link only, the gNB is on the satellite.
// - Doppler, which the UE pre-compensates (38.300 16.14.2.2):
//   - both modes: service link only. The feeder link Doppler is left to the network, which compensates it,
//     as in the built-in circular orbit model.
static void update_sat_trace_channel_model(channel_desc_t *channelDesc, int nbSamples, uint64_t TS)
{
  uint64_t start_TS = channelDesc->start_TS;
  if (channelDesc->sat_trace_common_clock)
    start_TS = 0;
  const double t = (TS > start_TS ? TS - start_TS : 0) / channelDesc->sampling_rate;
  double pos_sat[3], vel_sat[3];
  interp_sat_trace(channelDesc, t, pos_sat, vel_sat);

  const bool transparent = channelDesc->modelid == SAT_LEO_TRANS;
  const double *pos_ue = channelDesc->pos_ue;
  const double *pos_gnb = channelDesc->pos_gnb;
  const double c = (double)SPEED_OF_LIGHT;
  const double f_c = channelDesc->center_freq;

  const double dist_service = vec_dist(pos_ue, pos_sat);
  const double vel_service = range_rate(pos_ue, pos_sat, vel_sat);

  // [NOBUG] Delay/TA: only service link for SAT_LEO_REGEN; plus feeder link for SAT_LEO_TRANS
  double dist_feeder = 0;
  if (transparent)
    dist_feeder = vec_dist(pos_gnb, pos_sat);
  const double prop_delay = (dist_service + dist_feeder) / c;
  if (channelDesc->enable_dynamic_delay)
    channelDesc->channel_offset = prop_delay * channelDesc->sampling_rate;

  // [NOBUG] Doppler: service link only in both modes
  double f_doppler;
  if (channelDesc->is_uplink) // UE -> SAT: moving receiver
    f_doppler = -vel_service / c * f_c;
  else // SAT -> UE: moving transmitter
    f_doppler = -vel_service / (c + vel_service) * f_c;
  if (channelDesc->enable_dynamic_Doppler)
    channelDesc->Doppler_phase_inc = 2 * M_PI * f_doppler / channelDesc->sampling_rate;

  if (TS / (unsigned int)channelDesc->sampling_rate != (TS + nbSamples) / (unsigned int)channelDesc->sampling_rate) {
    LOG_I(HW,
          "Satellite orbit (trace): time %f s, Position = (%f, %f, %f), Velocity = (%f, %f, %f)\n",
          t,
          pos_sat[0],
          pos_sat[1],
          pos_sat[2],
          vel_sat[0],
          vel_sat[1],
          vel_sat[2]);
    LOG_I(HW,
          "%s delay %f ms, Doppler service link %f kHz\n",
          channelDesc->is_uplink ? "Uplink" : "Downlink",
          prop_delay * 1000,
          f_doppler / 1000);
  }

  if (!channelDesc->is_uplink)
    return;

  const int samples_per_subframe = channelDesc->sampling_rate / 1000;
  const int abs_subframe = TS / samples_per_subframe;
  if (abs_subframe % 10 == 0) { // update SIB19 information for the next frame, its epoch
    const double t_epoch = t + ((abs_subframe / 10 + 1) * 10.0 * samples_per_subframe - TS) / channelDesc->sampling_rate;
    interp_sat_trace(channelDesc, t_epoch, pos_sat, vel_sat);
    // ta-Common covers the feeder link only; for SAT_LEO_REGEN there is none, the UE computes the
    // service link delay itself from the ephemeris
    double dist_sib19 = 0, vel_sib19 = 0, acc_sib19 = 0;
    if (transparent)
      trace_link_dynamics(channelDesc, t_epoch, pos_gnb, &dist_sib19, &vel_sib19, &acc_sib19);
    gnb_sat_position_update_t sat_position = {
        .sfn = (abs_subframe / 10 + 1) % 1024,
        .subframe = 0,
        .delay = 2 * dist_sib19 / (c * 4.072e-9),
        .drift = 2 * vel_sib19 / (c * 0.2e-9),
        .accel = 2 * acc_sib19 / (c * 0.2e-10),
        .position.X = pos_sat[0] / 1.3,
        .position.Y = pos_sat[1] / 1.3,
        .position.Z = pos_sat[2] / 1.3,
        .velocity.X = vel_sat[0] / 0.06,
        .velocity.Y = vel_sat[1] / 0.06,
        .velocity.Z = vel_sat[2] / 0.06,
    };
    nr_update_sib19(&sat_position);
    update_ta_prediction(channelDesc, transparent, &sat_position, t_epoch);
  }
}

void update_channel_model(channel_desc_t *channelDesc, int nbSamples, uint64_t TS)
{
  if (channelDesc->sat_trace != NULL && (channelDesc->enable_dynamic_delay || channelDesc->enable_dynamic_Doppler)) {
    update_sat_trace_channel_model(channelDesc, nbSamples, TS);
    return;
  }
  if ((channelDesc->sat_height > 0)
      && (channelDesc->enable_dynamic_delay
          || channelDesc->enable_dynamic_Doppler)) { // model for transparent satellite on circular orbit
    /* assumptions:
       - The Earth is spherical, the ground station is static, and that the Earth does not rotate.
       - An access or link is possible from the satellite to the ground station at all times.
       - The ground station is located at the North Pole (positive Zaxis), and the satellite starts from the initial elevation angle
       0° in the second quadrant of the YZplane.
       - Satellite moves in the clockwise direction in its circular orbit.
    */
    const double radius_earth = 6377900; // m
    const double radius_sat = radius_earth + channelDesc->sat_height;
    const double GM_earth = 3.986e14; // m^3/s^2
    const double w_sat = sqrt(GM_earth / (radius_sat * radius_sat * radius_sat)); // rad/s

    // start_time and end_time are when the pos_sat_z == pos_gnb_z (elevation angle == 0 and 180 degree)
    const double start_phase = -acos(radius_earth / radius_sat); // SAT is just rising above the horizon
    const double end_phase = -start_phase; // SAT is just falling behind the horizon
    const double start_time = start_phase / w_sat; // in seconds
    const double end_time = end_phase / w_sat; // in seconds
    const uint64_t duration_samples = (end_time - start_time) * channelDesc->sampling_rate;
    const double t = start_time + ((TS - channelDesc->start_TS) % duration_samples) / channelDesc->sampling_rate;

    const double pos_sat_x = 0;
    const double pos_sat_y = radius_sat * sin(w_sat * t);
    const double pos_sat_z = radius_sat * cos(w_sat * t);

    const double vel_sat_x = 0;
    const double vel_sat_y = w_sat * radius_sat * cos(w_sat * t);
    const double vel_sat_z = -w_sat * radius_sat * sin(w_sat * t);

    const double pos_ue_x = 0;
    const double pos_ue_y = 0;
    const double pos_ue_z = radius_earth;

    const double c = (double) SPEED_OF_LIGHT;

    if (channelDesc->is_uplink) {
      const double dir_ue_sat_x = pos_sat_x - pos_ue_x;
      const double dir_ue_sat_y = pos_sat_y - pos_ue_y;
      const double dir_ue_sat_z = pos_sat_z - pos_ue_z;

      const double dist_ue_sat = sqrt(dir_ue_sat_x * dir_ue_sat_x + dir_ue_sat_y * dir_ue_sat_y + dir_ue_sat_z * dir_ue_sat_z);
      const double vel_ue_sat = (vel_sat_x * dir_ue_sat_x + vel_sat_y * dir_ue_sat_y + vel_sat_z * dir_ue_sat_z) / dist_ue_sat;

      double dist_sat_gnb = 0;
      double vel_sat_gnb = 0;
      double acc_sat_gnb = 0;
      if (channelDesc->modelid == SAT_LEO_TRANS) {
        const double t5 = t + 5;
        const double t10 = t + 10;

        const double pos_gnb_x = 0;
        const double pos_gnb_y = 0;
        const double pos_gnb_z = radius_earth;

        const double pos_sat_x5 = 0;
        const double pos_sat_y5 = radius_sat * sin(w_sat * t5);
        const double pos_sat_z5 = radius_sat * cos(w_sat * t5);

        const double pos_sat_x10 = 0;
        const double pos_sat_y10 = radius_sat * sin(w_sat * t10);
        const double pos_sat_z10 = radius_sat * cos(w_sat * t10);

        const double dir_sat_gnb_x = pos_gnb_x - pos_sat_x;
        const double dir_sat_gnb_y = pos_gnb_y - pos_sat_y;
        const double dir_sat_gnb_z = pos_gnb_z - pos_sat_z;

        const double dir_sat_gnb_x5 = pos_gnb_x - pos_sat_x5;
        const double dir_sat_gnb_y5 = pos_gnb_y - pos_sat_y5;
        const double dir_sat_gnb_z5 = pos_gnb_z - pos_sat_z5;

        const double dir_sat_gnb_x10 = pos_gnb_x - pos_sat_x10;
        const double dir_sat_gnb_y10 = pos_gnb_y - pos_sat_y10;
        const double dir_sat_gnb_z10 = pos_gnb_z - pos_sat_z10;

        dist_sat_gnb = sqrt(dir_sat_gnb_x * dir_sat_gnb_x + dir_sat_gnb_y * dir_sat_gnb_y + dir_sat_gnb_z * dir_sat_gnb_z);
        const double dist_sat_gnb5 = sqrt(dir_sat_gnb_x5 * dir_sat_gnb_x5 + dir_sat_gnb_y5 * dir_sat_gnb_y5 + dir_sat_gnb_z5 * dir_sat_gnb_z5);
        const double dist_sat_gnb10 = sqrt(dir_sat_gnb_x10 * dir_sat_gnb_x10 + dir_sat_gnb_y10 * dir_sat_gnb_y10 + dir_sat_gnb_z10 * dir_sat_gnb_z10);

        // calculate vel_sat_gnb and acc_sat_gnb, so the delay is correct for epoch_time, epoch_time + 5 seconds and epoch_time + 10 seconds
        //
        // dist_sat_gnb   = dist_sat_gnb +  0 * vel_sat_gnb +   0 * acc_sat_gnb;
        // dist_sat_gnb5  = dist_sat_gnb +  5 * vel_sat_gnb +  25 * acc_sat_gnb;
        // dist_sat_gnb10 = dist_sat_gnb + 10 * vel_sat_gnb + 100 * acc_sat_gnb;
        //
        // vel_sat_gnb = (dist_sat_gnb5 - dist_sat_gnb) / 5 - 5 * acc_sat_gnb;
        // acc_sat_gnb = (dist_sat_gnb10 - dist_sat_gnb) / 100 - vel_sat_gnb / 10;
        //
        // vel_sat_gnb = (dist_sat_gnb5 - dist_sat_gnb) / 5 - 5 * ((dist_sat_gnb10 - dist_sat_gnb) / 100 - vel_sat_gnb / 10);

        vel_sat_gnb = 2 * (dist_sat_gnb5 - dist_sat_gnb) / 5 - (dist_sat_gnb10 - dist_sat_gnb) / 10;
        acc_sat_gnb = (dist_sat_gnb10 - dist_sat_gnb) / 50 - (dist_sat_gnb5 - dist_sat_gnb) / 25;

        // in the moment when the satellite disappears behind the horizon acc_sat_gnb might become negative, what is invalid
        if (acc_sat_gnb < 0)
          acc_sat_gnb = 0;
      }

      const double prop_delay = (dist_ue_sat + dist_sat_gnb) / c;
      if (channelDesc->enable_dynamic_delay)
        channelDesc->channel_offset = prop_delay * channelDesc->sampling_rate;

      const double f_Doppler_shift_ue_sat = (-vel_ue_sat / c) * channelDesc->center_freq;
      if (channelDesc->enable_dynamic_Doppler)
        channelDesc->Doppler_phase_inc = 2 * M_PI * f_Doppler_shift_ue_sat / channelDesc->sampling_rate;

      if (TS / (unsigned int)channelDesc->sampling_rate != (TS + nbSamples) / (unsigned int)channelDesc->sampling_rate) {
        LOG_I(HW,
              "Satellite orbit: time %f s, Position = (%f, %f, %f), Velocity = (%f, %f, %f)\n",
              t,
              pos_sat_x,
              pos_sat_y,
              pos_sat_z,
              vel_sat_x,
              vel_sat_y,
              vel_sat_z);
        LOG_I(HW, "Uplink delay %f ms, Doppler shift UE->SAT %f kHz\n", prop_delay * 1000, f_Doppler_shift_ue_sat / 1000);
        LOG_I(HW, "Satellite velocity towards gNB: %f m/s, acceleration towards gNB: %f m/s²\n", -vel_sat_gnb, acc_sat_gnb);
      }

      const int samples_per_subframe = channelDesc->sampling_rate / 1000;
      const int abs_subframe = TS / samples_per_subframe;
      if (abs_subframe % 10 == 0) { // update SIB19 information for the next frame
        gnb_sat_position_update_t sat_position = {
            .sfn = (abs_subframe / 10 + 1) % 1024,
            .subframe = 0,
            .delay = 2 * dist_sat_gnb / (c * 4.072e-9),
            .drift = 2 * vel_sat_gnb / (c * 0.2e-9),
            .accel = 2 * acc_sat_gnb / (c * 0.2e-10),
            .position.X = pos_sat_x / 1.3,
            .position.Y = pos_sat_y / 1.3,
            .position.Z = pos_sat_z / 1.3,
            .velocity.X = vel_sat_x / 0.06,
            .velocity.Y = vel_sat_y / 0.06,
            .velocity.Z = vel_sat_z / 0.06,
        };
        // Here we update the SIB19 information directly in the gNB MAC layer.
        // Without rf-simulaor, in a real system or with an external channel emulator, the SIB19 updates would
        // be provided via an external interface (e.g. O-RAN E2 interface) to the MAC layer (in the O-DU).
        // We do it directly here, because we can, and an E2 Interface implementation (e.g using FlexRIC)
        // would pull too many dependencies into rf-simulator, just for updating SIB19.
        nr_update_sib19(&sat_position);
      }
    } else {
      const double dir_sat_ue_x = pos_ue_x - pos_sat_x;
      const double dir_sat_ue_y = pos_ue_y - pos_sat_y;
      const double dir_sat_ue_z = pos_ue_z - pos_sat_z;

      const double dist_sat_ue = sqrt(dir_sat_ue_x * dir_sat_ue_x + dir_sat_ue_y * dir_sat_ue_y + dir_sat_ue_z * dir_sat_ue_z);
      const double vel_sat_ue = (vel_sat_x * dir_sat_ue_x + vel_sat_y * dir_sat_ue_y + vel_sat_z * dir_sat_ue_z) / dist_sat_ue;

      double dist_gnb_sat = 0;
      if (channelDesc->modelid == SAT_LEO_TRANS) {
        const double pos_gnb_x = 0;
        const double pos_gnb_y = 0;
        const double pos_gnb_z = radius_earth;

        const double dir_gnb_sat_x = pos_sat_x - pos_gnb_x;
        const double dir_gnb_sat_y = pos_sat_y - pos_gnb_y;
        const double dir_gnb_sat_z = pos_sat_z - pos_gnb_z;

        dist_gnb_sat = sqrt(dir_gnb_sat_x * dir_gnb_sat_x + dir_gnb_sat_y * dir_gnb_sat_y + dir_gnb_sat_z * dir_gnb_sat_z);
      }

      const double prop_delay = (dist_gnb_sat + dist_sat_ue) / c;
      if (channelDesc->enable_dynamic_delay)
        channelDesc->channel_offset = prop_delay * channelDesc->sampling_rate;

      const double f_Doppler_shift_sat_ue = (vel_sat_ue / (c - vel_sat_ue)) * channelDesc->center_freq;
      if (channelDesc->enable_dynamic_Doppler)
        channelDesc->Doppler_phase_inc = 2 * M_PI * f_Doppler_shift_sat_ue / channelDesc->sampling_rate;

      if (TS / (unsigned int)channelDesc->sampling_rate != (TS + nbSamples) / (unsigned int)channelDesc->sampling_rate) {
        LOG_I(HW,
              "Satellite orbit: time %f s, Position = (%f, %f, %f), Velocity = (%f, %f, %f)\n",
              t,
              pos_sat_x,
              pos_sat_y,
              pos_sat_z,
              vel_sat_x,
              vel_sat_y,
              vel_sat_z);
        LOG_I(HW, "Downlink delay %f ms, Doppler shift SAT->UE %f kHz\n", prop_delay * 1000, f_Doppler_shift_sat_ue / 1000);
      }
    }
  }
}

/*
  Legacy study:
  The parameters are:
  gain&loss (decay, signal power, ...)
  either a fixed gain in dB, a target power in dBm or ACG (automatic control gain) to a target average
  => don't redo the AGC, as it was used in UE case, that must have a AGC inside the UE
  will be better to handle the "set_gain()" called by UE to apply it's gain (enable test of UE power loop)
  lin_amp = pow(10.0,.05*txpwr_dBm)/sqrt(nb_tx_antennas);
  a lot of operations in legacy, grouped in one simulation signal decay: txgain*decay*rxgain

  multi_path (auto convolution, ISI, ...)
  either we regenerate the channel (call again random_channel(desc,0)), or we keep it over subframes
  legacy: we regenerate each sub frame in UL, and each frame only in DL
*/
void rxAddInput(c16_t **input_sig, cf_t *after_channel_sig, int rxAnt, channel_desc_t *channelDesc, int nbSamples)
{
  // channelDesc->path_loss_dB should contain the total path gain
  // so, in actual RF: tx gain + path loss + rx gain (+antenna gain, ...)
  // UE and NB gain control to be added
  // Fixme: not sure when it is "volts" so dB is 20*log10(...) or "power", so dB is 10*log10(...)
  const double pathLossLinear = pow(10, channelDesc->path_loss_dB / 20.0);
  // Energy in one sample to calibrate input noise
  // the normalized OAI value seems to be 256 as average amplitude (numerical amplification = 1)
  const double noise_per_sample = pow(10, channelDesc->noise_power_dB / 10.0) * 256;
  const int nbTx = channelDesc->nb_tx;
  double Doppler_phase_cur = channelDesc->Doppler_phase_cur[rxAnt];
  Doppler_phase_cur -= 2 * M_PI * round(Doppler_phase_cur / (2 * M_PI));

  for (int i = 0; i < nbSamples; i++) {
    cf_t *out_ptr = after_channel_sig + i;
    struct complexd rx_tmp = {0};

    for (int txAnt = 0; txAnt < nbTx; txAnt++) {
      const struct complexd *channelModel = channelDesc->ch[rxAnt + (txAnt * channelDesc->nb_rx)];

      // const struct complex *channelModelEnd=channelModel+channelDesc->channel_length;
      for (int l = 0; l < (int)channelDesc->channel_length; l++) {
        const int idx = i - l + channelDesc->channel_length - 1;
        const struct complex16 tx16 = input_sig[txAnt][idx];
        rx_tmp.r += tx16.r * channelModel[l].r - tx16.i * channelModel[l].i;
        rx_tmp.i += tx16.i * channelModel[l].r + tx16.r * channelModel[l].i;
      } // l
    }

    if (channelDesc->Doppler_phase_inc != 0.0) {
#ifdef CMPLX
      double complex in = CMPLX(rx_tmp.r, rx_tmp.i);
#else
      double complex in = rx_tmp.r + rx_tmp.i * I;
#endif
      double complex out = in * cexp(Doppler_phase_cur * I);
      rx_tmp.r = creal(out);
      rx_tmp.i = cimag(out);
      Doppler_phase_cur += channelDesc->Doppler_phase_inc;
    }

    out_ptr->r += rx_tmp.r * pathLossLinear + noise_per_sample * gaussZiggurat(0.0, 1.0);
    out_ptr->i += rx_tmp.i * pathLossLinear + noise_per_sample * gaussZiggurat(0.0, 1.0);
    out_ptr++;
  }

  channelDesc->Doppler_phase_cur[rxAnt] = Doppler_phase_cur;

  // Cast to a wrong type for compatibility !
  LOG_D(HW,
        "Input power %f, output power: %f, channel path loss %f, noise coeff: %f \n",
        10 * log10((double)signal_energy((int32_t *)&input_sig[0], nbSamples)),
        10 * log10((double)signal_energy((int32_t *)after_channel_sig, nbSamples)),
        channelDesc->path_loss_dB,
        10 * log10(noise_per_sample));
}
