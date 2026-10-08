# SPDX-License-Identifier: LicenseRef-CSSL-1.0

add_executable(test_nr_other_sib_ssb_index
  test_nr_other_sib_ssb_index.c
  ${PHY_INTERFACE_DIR}/queue_t.c)
target_link_libraries(test_nr_other_sib_ssb_index PRIVATE
  -Wl,--start-group UTIL SIMU PHY_NR_COMMON PHY_NR SCHED_NR_LIB MAC_NR_COMMON CONFIG_LIB L2_NR -Wl,--end-group
  m pthread ${T_LIB} ITTI dl physim_common time_management_core softmodem_common
  asn1_nr_rrc_hdrs asn1_lte_rrc_hdrs)
add_dependencies(tests test_nr_other_sib_ssb_index)
foreach(ssb_bitmap 0x1 0x2 0x6)
  add_test(NAME test_nr_other_sib_ssb_index_${ssb_bitmap} COMMAND test_nr_other_sib_ssb_index ${ssb_bitmap})
endforeach()
