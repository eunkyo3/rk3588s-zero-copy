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
set(ENV{PKG_CONFIG_PATH} "${FFMPEG_PREFIX}/lib/pkgconfig:$ENV{PKG_CONFIG_PATH}")

find_package(PkgConfig REQUIRED)
pkg_check_modules(FFMPEG REQUIRED IMPORTED_TARGET libavformat libavcodec libavutil)
pkg_check_modules(RGA REQUIRED IMPORTED_TARGET librga)
pkg_check_modules(DRM REQUIRED IMPORTED_TARGET libdrm)
find_package(OpenCV REQUIRED COMPONENTS core imgproc imgcodecs)
find_library(RKNNRT rknnrt REQUIRED)

add_library(core STATIC src/core/yolov8_post.cpp)
target_include_directories(core PUBLIC src ${RKNN_INCLUDE_DIR})

add_executable(spike_fd_chain tools/spike_fd_chain.cpp)
target_link_libraries(spike_fd_chain PRIVATE
    core PkgConfig::FFMPEG PkgConfig::RGA PkgConfig::DRM ${OpenCV_LIBS} ${RKNNRT} pthread)
set_target_properties(spike_fd_chain PROPERTIES BUILD_RPATH ${FFMPEG_PREFIX}/lib)
