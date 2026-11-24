cmake_minimum_required(VERSION 3.18)
project(rk3588s_zero_copy CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()
# Frame pointers, and no tail calls, let perf attribute samples to prepare, bind and rknn_run
# (PROTOCOL §11.4). A tail call to memcpy would leave its caller off the stack.
add_compile_options(-Wall -Wextra -fno-omit-frame-pointer -fno-optimize-sibling-calls)

set(FFMPEG_PREFIX /opt/ffmpeg-rockchip CACHE PATH "ffmpeg-rockchip install prefix")
set(RKNN_INCLUDE_DIR /opt/rknpu2/include CACHE PATH "RKNN C API headers")
set(CODE_COMMIT unknown CACHE STRING "commit of the code being built (tools/deploy_board.sh sets it)")
set(ENV{PKG_CONFIG_PATH} "${FFMPEG_PREFIX}/lib/pkgconfig:$ENV{PKG_CONFIG_PATH}")

# The latest amendment in force goes into every result header (PROTOCOL §1).
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS prereg/AMENDMENTS.md)
file(STRINGS prereg/AMENDMENTS.md amendment_lines REGEX "^## A[0-9]+ ")
set(LATEST_AMENDMENT none)
foreach(line ${amendment_lines})
    string(REGEX REPLACE "^## (A[0-9]+) .*" "\\1" LATEST_AMENDMENT "${line}")
endforeach()

find_package(PkgConfig REQUIRED)
pkg_check_modules(FFMPEG REQUIRED IMPORTED_TARGET libavformat libavcodec libavutil)
pkg_check_modules(RGA REQUIRED IMPORTED_TARGET librga)
pkg_check_modules(DRM REQUIRED IMPORTED_TARGET libdrm)
find_package(OpenCV REQUIRED COMPONENTS core imgproc imgcodecs)
find_library(RKNNRT rknnrt REQUIRED)

add_library(core STATIC
    src/core/metrics.cpp
    src/core/npu.cpp
    src/core/pipeline.cpp
    src/core/pipeline_post.cpp
    src/core/report.cpp
    src/core/run_rules.cpp
    src/core/slot_pool.cpp
    src/core/video_source.cpp
    src/core/yolov8_post.cpp
    src/core/yolov8_post_neon.cpp
    src/core/yolov10_post.cpp
    src/preprocess/cpu_common.cpp
    src/preprocess/cpu_copy.cpp
    src/preprocess/cpu_direct.cpp
    src/preprocess/cpu_sw.cpp
    src/preprocess/rga_copy.cpp
    src/preprocess/zero_copy_rga.cpp)
target_include_directories(core PUBLIC src ${RKNN_INCLUDE_DIR})
target_link_libraries(core PUBLIC PkgConfig::FFMPEG PkgConfig::RGA PkgConfig::DRM ${OpenCV_LIBS} ${RKNNRT} pthread)

# M6's arm By (prereg/M6.md §1, §4): libyuv at one commit, built from its source in third_party/libyuv and
# linked statically. tools/setup/fetch_libyuv.sh puts it there; the configure step runs it when the
# directory is missing. libyuv's own options stay at their defaults, and this file's build type and
# compile options apply to it as to every target. By's backend is a library of its own, core_by, and only
# it sees libyuv's headers: the flags, include paths and defines of core and bench_main stay what
# docs/notes/inventory_m1.txt records (M3 §4).
set(LIBYUV_PINNED_COMMIT 0ec9f68d82634f6f3414aaaf780e03f9edf002b3)
set(LIBYUV_DIR ${CMAKE_SOURCE_DIR}/third_party/libyuv)
if(NOT EXISTS ${LIBYUV_DIR}/CMakeLists.txt)
    execute_process(COMMAND sh ${CMAKE_SOURCE_DIR}/tools/setup/fetch_libyuv.sh ${LIBYUV_DIR}
                    RESULT_VARIABLE libyuv_fetch)
    if(NOT libyuv_fetch EQUAL 0)
        message(FATAL_ERROR "no libyuv source in ${LIBYUV_DIR}: run tools/setup/fetch_libyuv.sh")
    endif()
endif()
execute_process(COMMAND git -C ${LIBYUV_DIR} rev-parse HEAD
                OUTPUT_VARIABLE LIBYUV_COMMIT OUTPUT_STRIP_TRAILING_WHITESPACE RESULT_VARIABLE libyuv_rev)
execute_process(COMMAND git -C ${LIBYUV_DIR} status --porcelain
                OUTPUT_VARIABLE libyuv_changes OUTPUT_STRIP_TRAILING_WHITESPACE RESULT_VARIABLE libyuv_status)
if(NOT libyuv_rev EQUAL 0 OR NOT libyuv_status EQUAL 0 OR NOT LIBYUV_COMMIT STREQUAL LIBYUV_PINNED_COMMIT
   OR NOT libyuv_changes STREQUAL "")
    message(FATAL_ERROR "${LIBYUV_DIR} is not a clean checkout of libyuv ${LIBYUV_PINNED_COMMIT} (M6 §1): "
                        "it is at '${LIBYUV_COMMIT}', changes: '${libyuv_changes}'")
endif()
add_subdirectory(${LIBYUV_DIR} third_party/libyuv EXCLUDE_FROM_ALL)

add_library(core_by STATIC src/preprocess/cpu_yuv.cpp)
target_include_directories(core_by PRIVATE ${LIBYUV_DIR}/include)
target_compile_definitions(core_by PRIVATE LIBYUV_COMMIT="${LIBYUV_COMMIT}")
target_link_libraries(core_by PUBLIC core PRIVATE yuv)

add_executable(bench_main src/app/bench_main.cpp)
target_link_libraries(bench_main PRIVATE core_by core)
target_compile_definitions(bench_main PRIVATE
    CODE_COMMIT="${CODE_COMMIT}" LATEST_AMENDMENT="${LATEST_AMENDMENT}")

add_executable(spike_fd_chain tools/spike_fd_chain.cpp)
target_link_libraries(spike_fd_chain PRIVATE core ${OpenCV_LIBS})

add_executable(compare_prepare tools/compare_prepare.cpp)
target_link_libraries(compare_prepare PRIVATE core)

add_executable(rga_probe tools/rga_probe.cpp)
target_link_libraries(rga_probe PRIVATE core)

add_executable(rga_kernel tools/rga_kernel.cpp)
target_link_libraries(rga_kernel PRIVATE core)

add_executable(inputs_set_probe tools/inputs_set_probe.cpp)
target_link_libraries(inputs_set_probe PRIVATE core)

add_executable(equivalence_dump tools/equivalence_dump.cpp)
target_link_libraries(equivalence_dump PRIVATE core_by core)
target_compile_definitions(equivalence_dump PRIVATE
    CODE_COMMIT="${CODE_COMMIT}" LATEST_AMENDMENT="${LATEST_AMENDMENT}")

add_executable(dump_tensor tools/dump_tensor.cpp)
target_link_libraries(dump_tensor PRIVATE core)
target_compile_definitions(dump_tensor PRIVATE
    CODE_COMMIT="${CODE_COMMIT}" LATEST_AMENDMENT="${LATEST_AMENDMENT}")

add_executable(calib_npu tools/calib_npu.cpp)
target_link_libraries(calib_npu PRIVATE core)
target_compile_definitions(calib_npu PRIVATE
    CODE_COMMIT="${CODE_COMMIT}" LATEST_AMENDMENT="${LATEST_AMENDMENT}")

add_executable(demo_detect tools/demo_detect.cpp)
target_link_libraries(demo_detect PRIVATE core)
target_compile_definitions(demo_detect PRIVATE CODE_COMMIT="${CODE_COMMIT}")

add_executable(bdirect_check tools/bdirect_check.cpp)
target_link_libraries(bdirect_check PRIVATE core)
target_compile_definitions(bdirect_check PRIVATE
    CODE_COMMIT="${CODE_COMMIT}" LATEST_AMENDMENT="${LATEST_AMENDMENT}")

add_executable(output_probe tools/output_probe.cpp)
target_link_libraries(output_probe PRIVATE core)
target_compile_definitions(output_probe PRIVATE
    CODE_COMMIT="${CODE_COMMIT}" LATEST_AMENDMENT="${LATEST_AMENDMENT}")

add_executable(first_runs_probe tools/first_runs_probe.cpp)
target_link_libraries(first_runs_probe PRIVATE core)
target_compile_definitions(first_runs_probe PRIVATE
    CODE_COMMIT="${CODE_COMMIT}" LATEST_AMENDMENT="${LATEST_AMENDMENT}")

add_executable(post_probe tools/post_probe.cpp)
target_link_libraries(post_probe PRIVATE core)
target_compile_definitions(post_probe PRIVATE
    CODE_COMMIT="${CODE_COMMIT}" LATEST_AMENDMENT="${LATEST_AMENDMENT}")

add_executable(m2_equivalence tools/m2_equivalence.cpp)
target_link_libraries(m2_equivalence PRIVATE core)
target_compile_definitions(m2_equivalence PRIVATE
    CODE_COMMIT="${CODE_COMMIT}" LATEST_AMENDMENT="${LATEST_AMENDMENT}")

add_executable(m4_probe tools/m4_probe.cpp)
target_link_libraries(m4_probe PRIVATE core)
target_compile_definitions(m4_probe PRIVATE
    CODE_COMMIT="${CODE_COMMIT}" LATEST_AMENDMENT="${LATEST_AMENDMENT}")

# M4's accuracy tool (prereg/M4.md §2.2 to §2.5): its mAP rules and the SHA-256 of its inputs. The rules
# are compiled without floating-point contraction, so the IoU is computed as written (src/m4/map_rules.h).
add_library(m4map STATIC src/m4/map_rules.cpp src/m4/sha256.cpp)
target_include_directories(m4map PUBLIC src)
target_link_libraries(m4map PUBLIC core)
set_source_files_properties(src/m4/map_rules.cpp PROPERTIES COMPILE_OPTIONS -ffp-contract=off)

add_executable(m4_accuracy tools/m4_accuracy.cpp)
target_link_libraries(m4_accuracy PRIVATE m4map core)
target_compile_definitions(m4_accuracy PRIVATE
    CODE_COMMIT="${CODE_COMMIT}" LATEST_AMENDMENT="${LATEST_AMENDMENT}")

# M6's checks (prereg/M6.md §8 step 3). m6_arms_dump also builds against freeze-m3's code, without the new
# arms: there CMake has no core_by, and M6_OLD_ARMS_ONLY leaves Rc out.
add_executable(m6_arms_dump tools/m6_arms_dump.cpp)
target_link_libraries(m6_arms_dump PRIVATE core)
target_compile_definitions(m6_arms_dump PRIVATE
    CODE_COMMIT="${CODE_COMMIT}" LATEST_AMENDMENT="${LATEST_AMENDMENT}")

add_executable(m6_by_check tools/m6_by_check.cpp)
target_link_libraries(m6_by_check PRIVATE core_by core)
target_compile_definitions(m6_by_check PRIVATE
    CODE_COMMIT="${CODE_COMMIT}" LATEST_AMENDMENT="${LATEST_AMENDMENT}")

add_executable(test_core tests/test_core.cpp)
target_link_libraries(test_core PRIVATE m4map core)

set_target_properties(bench_main spike_fd_chain compare_prepare rga_probe rga_kernel inputs_set_probe
    equivalence_dump dump_tensor calib_npu demo_detect bdirect_check output_probe first_runs_probe post_probe m2_equivalence m4_probe m4_accuracy m6_arms_dump m6_by_check test_core PROPERTIES BUILD_RPATH ${FFMPEG_PREFIX}/lib)
