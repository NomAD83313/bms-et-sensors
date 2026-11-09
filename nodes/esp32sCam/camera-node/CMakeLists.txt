cmake_minimum_required(VERSION 3.22)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)

# Shared components: bms_node_core (device identity provider, reused from C3 node)
set(EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/../../components")
if(NOT DEFINED ENV{ESP_MATTER_PATH})
    message(FATAL_ERROR "ESP_MATTER_PATH must point to local esp-matter release-v1.5")
endif()
set(ESP_MATTER_PATH $ENV{ESP_MATTER_PATH})
string(FIND "${ESP_MATTER_PATH}" "release-v1.5" ESP_MATTER_RELEASE_V1_5_POS)
if(ESP_MATTER_RELEASE_V1_5_POS EQUAL -1)
    message(FATAL_ERROR "ESP_MATTER_PATH must point to local esp-matter release-v1.5: ${ESP_MATTER_PATH}")
endif()
set(MATTER_SDK_PATH "${ESP_MATTER_PATH}/connectedhomeip/connectedhomeip")
if(NOT DEFINED ENV{ESP_MATTER_DEVICE_PATH})
    set(ENV{ESP_MATTER_DEVICE_PATH} "${ESP_MATTER_PATH}/device_hal/device/esp32_devkit_c")
endif()
list(APPEND EXTRA_COMPONENT_DIRS
    "${ESP_MATTER_PATH}/examples/common"
    "${MATTER_SDK_PATH}/config/esp32/components"
    "${ESP_MATTER_PATH}/components"
    "${ESP_MATTER_PATH}/device_hal/button_driver/button"
    "${ESP_MATTER_PATH}/device_hal/button_driver/iot_button"
)

execute_process(
    COMMAND git rev-parse --short=12 HEAD
    WORKING_DIRECTORY "${CMAKE_SOURCE_DIR}"
    OUTPUT_VARIABLE ESP32SCAM_NODE_GIT_COMMIT
    OUTPUT_STRIP_TRAILING_WHITESPACE
    ERROR_QUIET
)
if(NOT ESP32SCAM_NODE_GIT_COMMIT)
    set(ESP32SCAM_NODE_GIT_COMMIT "unknown")
endif()
if(DEFINED CAMERA_NODE_FIRMWARE_VERSION)
    set(ESP32SCAM_NODE_GIT_COMMIT "${CAMERA_NODE_FIRMWARE_VERSION}")
endif()

# GCC 14 compatibility patch for esp-matter nullable.h
include("${CMAKE_SOURCE_DIR}/../../cmake/patch_esp_matter_nullable.cmake")

project(esp32s_cam_matter_node)

if(DEFINED ENV{ESP_MATTER_PATH})
    idf_build_set_property(CXX_COMPILE_OPTIONS "-std=gnu++17;-Os;-DCHIP_HAVE_CONFIG_H;-Wno-overloaded-virtual" APPEND)
    idf_build_set_property(C_COMPILE_OPTIONS "-Os" APPEND)
    idf_build_set_property(COMPILE_OPTIONS "-Wno-format-nonliteral;-Wno-format-security" APPEND)
endif()
