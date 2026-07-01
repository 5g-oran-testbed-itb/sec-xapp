if(XAPP_DB STREQUAL "MYSQL_XAPP")
	include_directories(${LIBMYSQLCLIENT_INCLUDE_DIRS})
endif()

# xapp_cust_moni
add_executable(xapp_cust_moni
		xapp_cust_moni.c
								${CMAKE_SOURCE_DIR}/src/util/alg_ds/alg/defer.c
								)

target_link_libraries(xapp_cust_moni
											PUBLIC
											e42_xapp
											$<TARGET_OBJECTS:e2_time_obj>
											$<$<STREQUAL:${XAPP_DB},MYSQL_XAPP>:${LIBMYSQLCLIENT_LIBRARIES}>
											-pthread
											-lsctp
											-ldl
											)

# xapp_oran_moni
add_executable(xapp_oran_moni
		xapp_oran_moni.c
								${CMAKE_SOURCE_DIR}/src/util/alg_ds/alg/defer.c
								)

target_link_libraries(xapp_oran_moni
											PUBLIC
											e42_xapp
											$<TARGET_OBJECTS:e2_time_obj>
											$<$<STREQUAL:${XAPP_DB},MYSQL_XAPP>:${LIBMYSQLCLIENT_LIBRARIES}>
											-pthread
											-lsctp
											-ldl
											)
# enable below directive if you need to debug asn code
# target_compile_options(xapp_kpm_moni PUBLIC -DASN_EMIT_DEBUG=1)

# xapp_all_moni
add_executable(xapp_all_moni
		xapp_all_moni.c
								${CMAKE_SOURCE_DIR}/src/util/alg_ds/alg/defer.c
								${CMAKE_SOURCE_DIR}/src/util/e2ap_ngran_type.c
								)

target_link_libraries(xapp_all_moni
											PUBLIC
											e42_xapp
											$<TARGET_OBJECTS:e2_time_obj>
											$<$<STREQUAL:${XAPP_DB},MYSQL_XAPP>:${LIBMYSQLCLIENT_LIBRARIES}>
											-pthread
											-lsctp
											-ldl
											)

# xapp_sec_moni
add_executable(xapp_sec_moni
		xapp_sec_moni.c
		sec_ids.c
		sec_ids_ue.c
		ue_tracker.c
		vendor/cJSON.c
								${CMAKE_SOURCE_DIR}/src/util/alg_ds/alg/defer.c
								${CMAKE_SOURCE_DIR}/src/util/e2ap_ngran_type.c
								)

target_link_libraries(xapp_sec_moni
											PUBLIC
											e42_xapp
											$<TARGET_OBJECTS:e2_time_obj>
											$<$<STREQUAL:${XAPP_DB},MYSQL_XAPP>:${LIBMYSQLCLIENT_LIBRARIES}>
											-pthread
											-lsctp
											-ldl
											-lm
											onnxruntime
											)

# xapp_mac_ue_probe — MAC SM per-UE probe (1s polling, CSV output)
add_executable(xapp_mac_ue_probe
    xapp_mac_ue_probe.c
)

target_link_libraries(xapp_mac_ue_probe
    PUBLIC
    e42_xapp
    $<TARGET_OBJECTS:e2_time_obj>
    $<$<STREQUAL:${XAPP_DB},MYSQL_XAPP>:${LIBMYSQLCLIENT_LIBRARIES}>
    -pthread
    -lsctp
    -ldl
)
target_link_options(xapp_mac_ue_probe PRIVATE -Wl,-z,execstack)

# xapp_sec_mitigate
add_executable(xapp_sec_mitigate
    xapp_sec_mitigate.c
    vendor/cJSON.c
    ${CMAKE_SOURCE_DIR}/src/util/alg_ds/alg/defer.c
)

target_link_libraries(xapp_sec_mitigate
    PUBLIC
    e42_xapp
    $<TARGET_OBJECTS:e2_time_obj>
    $<$<STREQUAL:${XAPP_DB},MYSQL_XAPP>:${LIBMYSQLCLIENT_LIBRARIES}>
    -pthread
    -lsctp
    -ldl
    -lm
)