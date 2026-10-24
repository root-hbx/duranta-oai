# Open5GS core the experiments run against, sourced by lte.sh and ntn.sh:
#   CORE=shared   (default) SaTrinity open5gs-docker on docker_open5gs_default, 172.22.0.0/16
#   CORE=private  orbitango-open5gs on orbitango_core, 172.23.0.0/16, containers o5g-<nf>
# The RAN containers keep their host part (eNB1 .60, UE .61, gNB1 .70 ...) on either network.
if [ "${CORE:-shared}" = private ]; then
  export CORE_NET=172.23 CORE_NETWORK=orbitango_core CORE_IMAGE=orbitango-open5gs CORE_CT=o5g-
else
  export CORE_NET=172.22 CORE_NETWORK=docker_open5gs_default CORE_IMAGE=docker_open5gs CORE_CT=
fi
