cmake_minimum_required(VERSION 3.18)
project(rk3588s_zero_copy CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()
add_compile_options(-Wall -Wextra)

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
    src/core/report.cpp
    src/core/slot_pool.cpp
    src/core/video_source.cpp
    src/core/yolov8_post.cpp
    src/preprocess/zero_copy_rga.cpp)
target_include_directories(core PUBLIC src ${RKNN_INCLUDE_DIR})
target_link_libraries(core PUBLIC PkgConfig::FFMPEG PkgConfig::RGA PkgConfig::DRM ${RKNNRT} pthread)

add_executable(bench_main src/app/bench_main.cpp)
target_link_libraries(bench_main PRIVATE core)
target_compile_definitions(bench_main PRIVATE
    CODE_COMMIT="${CODE_COMMIT}" LATEST_AMENDMENT="${LATEST_AMENDMENT}")

add_executable(spike_fd_chain tools/spike_fd_chain.cpp)
target_link_libraries(spike_fd_chain PRIVATE core ${OpenCV_LIBS})

add_executable(test_core tests/test_core.cpp)
target_link_libraries(test_core PRIVATE core)

set_target_properties(bench_main spike_fd_chain test_core PROPERTIES BUILD_RPATH ${FFMPEG_PREFIX}/lib)
