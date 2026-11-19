# librkzc: variant C's path as a library (docs/design/packaging.md, step 2).
#
# This is a build of its own, not a subdirectory of the root one. The root CMakeLists.txt, src/, tools/,
# scripts/ and tests/ are the registered code: scripts/reproduce_m1.sh refuses to run unless they equal
# freeze-m3's. So the library compiles the measured sources from ../src unchanged and adds its own files
# here.
#   cmake -S lib -B build-lib && cmake --build build-lib -j4
cmake_minimum_required(VERSION 3.18)
project(rkzc VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()
# The measured build's options (root CMakeLists.txt), so the shared sources are compiled the same way
# apart from -fPIC.
add_compile_options(-Wall -Wextra -fno-omit-frame-pointer -fno-optimize-sibling-calls)

set(FFMPEG_PREFIX /opt/ffmpeg-rockchip CACHE PATH "ffmpeg-rockchip install prefix")
set(RKNN_INCLUDE_DIR /opt/rknpu2/include CACHE PATH "RKNN C API headers")
set(RKZC_TEST_MODEL "" CACHE FILEPATH "a .rknn model for the board tests")
set(RKZC_TEST_VIDEO "" CACHE STRING "an H.264 file or rtsp:// URL for the board tests")
set(ENV{PKG_CONFIG_PATH} "${FFMPEG_PREFIX}/lib/pkgconfig:$ENV{PKG_CONFIG_PATH}")

find_package(PkgConfig REQUIRED)
pkg_check_modules(FFMPEG REQUIRED IMPORTED_TARGET libavformat libavcodec libavutil)
pkg_check_modules(RGA REQUIRED IMPORTED_TARGET librga)
pkg_check_modules(DRM REQUIRED IMPORTED_TARGET libdrm)
find_library(RKNNRT rknnrt REQUIRED)

set(MEASURED ${CMAKE_CURRENT_SOURCE_DIR}/../src)

# Variant C's part of the root build's `core`: no cpu_*.cpp (variants A and B, which need OpenCV), and
# no pipeline.cpp, metrics.cpp, report.cpp or run_rules.cpp (the benchmark's timeline and records).
add_library(rkzc SHARED
    ${MEASURED}/core/npu.cpp
    ${MEASURED}/core/pipeline_post.cpp
    ${MEASURED}/core/slot_pool.cpp
    ${MEASURED}/core/video_source.cpp
    ${MEASURED}/core/yolov8_post.cpp
    ${MEASURED}/core/yolov8_post_neon.cpp
    ${MEASURED}/core/yolov10_post.cpp
    ${MEASURED}/preprocess/zero_copy_rga.cpp
    src/check.cpp
    src/engine.cpp)
# shim/ must stay before ${MEASURED}: it replaces core/util.h's die() (see shim/core/util.h).
target_include_directories(rkzc
    PUBLIC include
    PRIVATE shim ${MEASURED} src ${RKNN_INCLUDE_DIR})
target_compile_definitions(rkzc PRIVATE RKZC_VERSION="${PROJECT_VERSION}")
target_link_libraries(rkzc PRIVATE PkgConfig::FFMPEG PkgConfig::RGA PkgConfig::DRM ${RKNNRT} pthread)
set_target_properties(rkzc PROPERTIES
    CXX_VISIBILITY_PRESET hidden
    VISIBILITY_INLINES_HIDDEN ON
    VERSION ${PROJECT_VERSION}
    SOVERSION 0
    BUILD_RPATH ${FFMPEG_PREFIX}/lib)

add_executable(rkzc-check tools/rkzc_check.cpp)
target_link_libraries(rkzc-check PRIVATE rkzc)

# The tests see the public header only: they get no FFmpeg, RGA or RKNN include directory.
add_executable(test_checks tests/test_checks.cpp)
target_link_libraries(test_checks PRIVATE rkzc)

enable_testing()
add_test(NAME no_opencv
    COMMAND sh ${CMAKE_CURRENT_SOURCE_DIR}/tests/no_opencv.sh $<TARGET_FILE:rkzc> $<TARGET_FILE:rkzc-check>)
if(RKZC_TEST_MODEL AND RKZC_TEST_VIDEO)
    add_test(NAME checks COMMAND test_checks ${RKZC_TEST_MODEL} ${RKZC_TEST_VIDEO})
endif()
