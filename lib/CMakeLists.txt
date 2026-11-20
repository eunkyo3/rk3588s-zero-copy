# librkzc: variant C's path as a library (docs/design/packaging.md, steps 2 and 3), with its C interface,
# the Python module that uses it (python/), and the program rkzc-detect with its service file (step 4).
#
# This is a build of its own, not a subdirectory of the root one. The root CMakeLists.txt, src/, tools/,
# scripts/ and tests/ are the registered code: scripts/reproduce_m1.sh refuses to run unless they equal
# freeze-m3's. So the library compiles the measured sources from ../src unchanged and adds its own files
# here.
#   cmake -S lib -B build-lib && cmake --build build-lib -j4
cmake_minimum_required(VERSION 3.18)
project(rkzc VERSION 0.1.0 LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()
# The measured build's options (root CMakeLists.txt), so the shared sources are compiled the same way
# apart from -fPIC.
add_compile_options(-Wall -Wextra -fno-omit-frame-pointer -fno-optimize-sibling-calls)
set(CMAKE_C_STANDARD 99)
set(CMAKE_C_EXTENSIONS OFF)

# Not "FFMPEG_PREFIX": pkg_check_modules(FFMPEG ...) writes a cache entry of that name itself.
set(RKZC_FFMPEG_DIR /opt/ffmpeg-rockchip CACHE PATH "ffmpeg-rockchip install prefix")
set(RKNN_INCLUDE_DIR /opt/rknpu2/include CACHE PATH "RKNN C API headers")
set(RKZC_TEST_MODEL "" CACHE FILEPATH "a .rknn model for the board tests")
set(RKZC_TEST_VIDEO "" CACHE STRING "a short H.264 file for the board tests (a few seconds)")
set(RKZC_TEST_OBJECTS_VIDEO "" CACHE FILEPATH "an H.264 file of at least 10 s with objects the model detects")
set(RKZC_DEMO_DETECT "" CACHE FILEPATH "the root build's demo_detect, for the equivalence test")
set(ENV{PKG_CONFIG_PATH} "${RKZC_FFMPEG_DIR}/lib/pkgconfig:$ENV{PKG_CONFIG_PATH}")

# Installed files find librkzc next to them and the FFmpeg libraries where they were built against; the
# package of step 5 sets RKZC_FFMPEG_DIR to where it puts them.
include(GNUInstallDirs)
set(CMAKE_INSTALL_RPATH "$ORIGIN/../${CMAKE_INSTALL_LIBDIR};${RKZC_FFMPEG_DIR}/lib")

find_package(PkgConfig REQUIRED)
find_package(Python3 COMPONENTS Interpreter)
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
    src/c_api.cpp
    src/check.cpp
    src/detector.cpp
    src/engine.cpp)
# shim/ must come before ${MEASURED}: it replaces core/util.h's die() and preprocess/fd_cache.h (see the
# two headers in shim/).
# tests/no_opencv.sh fails if the exiting die() got in after all.
target_include_directories(rkzc
    PUBLIC include
    PRIVATE ${MEASURED} src ${RKNN_INCLUDE_DIR})
target_include_directories(rkzc BEFORE PRIVATE shim)
target_compile_definitions(rkzc PRIVATE RKZC_VERSION="${PROJECT_VERSION}")
target_link_libraries(rkzc PRIVATE PkgConfig::FFMPEG PkgConfig::RGA PkgConfig::DRM ${RKNNRT} pthread)
set_target_properties(rkzc PROPERTIES
    CXX_VISIBILITY_PRESET hidden
    VISIBILITY_INLINES_HIDDEN ON
    VERSION ${PROJECT_VERSION}
    SOVERSION 0
    BUILD_RPATH ${RKZC_FFMPEG_DIR}/lib)

add_executable(rkzc-check tools/rkzc_check.cpp)
target_link_libraries(rkzc-check PRIVATE rkzc)

add_executable(rkzc-jsonl examples/detect_jsonl.cpp)
target_link_libraries(rkzc-jsonl PRIVATE rkzc)

# The program: a configuration file in, JSON lines out. It uses the public header only.
add_executable(rkzc-detect tools/rkzc_detect.cpp tools/detect_config.cpp tools/line_output.cpp)
target_link_libraries(rkzc-detect PRIVATE rkzc pthread)
configure_file(dist/rkzc-detect.service.in rkzc-detect.service @ONLY)

# What step 5's package takes. The Python module, the model and /etc/rkzc/detect.toml are not installed
# from here. The unit's ExecStart is fixed when configuring: configure with the prefix the files will
# run from (CMAKE_INSTALL_PREFIX), not with `cmake --install --prefix`.
install(TARGETS rkzc rkzc-detect rkzc-check
    LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
    RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
install(FILES include/rkzc/rkzc.h include/rkzc/rkzc_c.h DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}/rkzc)
install(FILES ${CMAKE_CURRENT_BINARY_DIR}/rkzc-detect.service DESTINATION lib/systemd/system)
install(FILES dist/detect.toml DESTINATION ${CMAKE_INSTALL_DATAROOTDIR}/doc/rkzc/examples)

# The tests see the public header only: they get no FFmpeg, RGA or RKNN include directory.
add_executable(test_checks tests/test_checks.cpp)
target_link_libraries(test_checks PRIVATE rkzc)
add_executable(test_detector tests/test_detector.cpp)
target_link_libraries(test_detector PRIVATE rkzc)
# The header's Config only: it links no board library.
add_executable(test_detect_config tests/test_detect_config.cpp tools/detect_config.cpp tools/line_output.cpp)
target_include_directories(test_detect_config PRIVATE tools include)
target_link_libraries(test_detect_config PRIVATE pthread)
# C, not C++: the C header must compile as C.
add_executable(test_c_api tests/test_c_api.c)
target_link_libraries(test_c_api PRIVATE rkzc)

enable_testing()
add_test(NAME no_opencv
    COMMAND sh ${CMAKE_CURRENT_SOURCE_DIR}/tests/no_opencv.sh $<TARGET_FILE:rkzc> $<TARGET_FILE:rkzc-check>)
add_test(NAME detect_config COMMAND test_detect_config ${CMAKE_CURRENT_SOURCE_DIR}/dist/detect.toml)
if(RKZC_TEST_MODEL AND RKZC_TEST_VIDEO)
    add_test(NAME checks COMMAND test_checks ${RKZC_TEST_MODEL} ${RKZC_TEST_VIDEO})
    add_test(NAME detector COMMAND test_detector ${RKZC_TEST_MODEL} ${RKZC_TEST_VIDEO})
    add_test(NAME c_api COMMAND test_c_api ${RKZC_TEST_MODEL} ${RKZC_TEST_VIDEO} ${RKZC_TEST_OBJECTS_VIDEO})
    # With the objects video, the Python example's lines are compared with the C++ example's too.
    set(PYTHON_TEST_EXTRA "")
    if(RKZC_TEST_OBJECTS_VIDEO)
        set(PYTHON_TEST_EXTRA ${RKZC_TEST_OBJECTS_VIDEO} $<TARGET_FILE:rkzc-jsonl>)
    endif()
    if(Python3_FOUND)
        add_test(NAME python
            COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/test_python.py ${RKZC_TEST_MODEL}
                    ${RKZC_TEST_VIDEO} ${PYTHON_TEST_EXTRA})
        set_tests_properties(python PROPERTIES
            ENVIRONMENT "RKZC_LIBRARY=$<TARGET_FILE:rkzc>;PYTHONPATH=${CMAKE_CURRENT_SOURCE_DIR}/python"
            TIMEOUT 300)
        add_test(NAME rkzc_detect
            COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/test_rkzc_detect.py
                    $<TARGET_FILE:rkzc-detect> ${RKZC_TEST_MODEL} ${RKZC_TEST_VIDEO} ${RKZC_TEST_OBJECTS_VIDEO})
        set_tests_properties(rkzc_detect PROPERTIES TIMEOUT 300)
    endif()
    # A test that hangs is a failure within minutes, not after ctest's 25.
    set_tests_properties(checks detector c_api PROPERTIES TIMEOUT 300)
endif()
if(RKZC_TEST_MODEL AND RKZC_TEST_OBJECTS_VIDEO AND RKZC_DEMO_DETECT)
    add_test(NAME equivalence
        COMMAND sh ${CMAKE_CURRENT_SOURCE_DIR}/tests/equivalence.sh ${RKZC_DEMO_DETECT} $<TARGET_FILE:rkzc-jsonl>
                ${RKZC_TEST_MODEL} ${RKZC_TEST_OBJECTS_VIDEO} ${RKZC_FFMPEG_DIR}/bin/ffmpeg)
endif()
