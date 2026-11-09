cmake_minimum_required(VERSION 3.22)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
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
idf_build_set_property(MINIMAL_BUILD ON)

execute_process(
    COMMAND git rev-parse --short=12 HEAD
    WORKING_DIRECTORY "${CMAKE_SOURCE_DIR}"
    OUTPUT_VARIABLE MATTER_NODE_GIT_COMMIT
    OUTPUT_STRIP_TRAILING_WHITESPACE
    ERROR_QUIET
)
if(NOT MATTER_NODE_GIT_COMMIT)
    set(MATTER_NODE_GIT_COMMIT "unknown")
endif()
execute_process(
    COMMAND git diff --quiet --ignore-submodules --
    WORKING_DIRECTORY "${CMAKE_SOURCE_DIR}"
    RESULT_VARIABLE MATTER_NODE_GIT_DIRTY
    ERROR_QUIET
)
if(DEFINED MATTER_NODE_FIRMWARE_VERSION)
    set(MATTER_NODE_GIT_COMMIT "${MATTER_NODE_FIRMWARE_VERSION}")
elseif(MATTER_NODE_GIT_DIRTY)
    set(MATTER_NODE_GIT_COMMIT "${MATTER_NODE_GIT_COMMIT}-dirty")
endif()

execute_process(
    COMMAND git rev-parse --short=8 HEAD
    WORKING_DIRECTORY "${CMAKE_SOURCE_DIR}"
    OUTPUT_VARIABLE MATTER_NODE_GIT_COMMIT_U32
    OUTPUT_STRIP_TRAILING_WHITESPACE
    ERROR_QUIET
)
if(NOT MATTER_NODE_GIT_COMMIT_U32)
    set(MATTER_NODE_GIT_COMMIT_U32 "00000000")
endif()
if(DEFINED MATTER_NODE_SOFTWARE_VERSION_U32)
    set(MATTER_NODE_GIT_COMMIT_U32 "${MATTER_NODE_SOFTWARE_VERSION_U32}")
elseif(MATTER_NODE_GIT_DIRTY)
    string(TIMESTAMP MATTER_NODE_BUILD_EPOCH "%s" UTC)
    math(EXPR MATTER_NODE_BUILD_VERSION_HEX "${MATTER_NODE_BUILD_EPOCH}" OUTPUT_FORMAT HEXADECIMAL)
    string(REGEX REPLACE "^0x" "" MATTER_NODE_GIT_COMMIT_U32 "${MATTER_NODE_BUILD_VERSION_HEX}")
endif()
set(MATTER_NODE_BUILD_EPOCH "${MATTER_NODE_BUILD_EPOCH}" CACHE INTERNAL "M5StickC Plus2 Matter node build epoch")
math(EXPR PROJECT_VER_NUMBER "0x${MATTER_NODE_GIT_COMMIT_U32}" OUTPUT_FORMAT DECIMAL)
set(PROJECT_VER "${MATTER_NODE_GIT_COMMIT}")

project(m5stickc_plus2_matter_node)
include("${CMAKE_SOURCE_DIR}/../../cmake/patch_esp_matter_nullable.cmake")

if(DEFINED ENV{ESP_MATTER_PATH})
    idf_build_set_property(CXX_COMPILE_OPTIONS "-std=gnu++17;-Os;-DCHIP_HAVE_CONFIG_H;-Wno-overloaded-virtual" APPEND)
    idf_build_set_property(C_COMPILE_OPTIONS "-Os" APPEND)
    idf_build_set_property(COMPILE_OPTIONS "-Wno-format-nonliteral;-Wno-format-security" APPEND)
endif()

if(EXISTS "${CMAKE_SOURCE_DIR}/.venv/bin/python")
    set(ONBOARDING_CARD_PYTHON "${CMAKE_SOURCE_DIR}/.venv/bin/python")
else()
    find_program(SYSTEM_PYTHON3 python3 PATHS /usr/bin /bin NO_DEFAULT_PATH)
    if(SYSTEM_PYTHON3)
        set(ONBOARDING_CARD_PYTHON "${SYSTEM_PYTHON3}")
    else()
        find_package(Python3 REQUIRED COMPONENTS Interpreter)
        set(ONBOARDING_CARD_PYTHON "${Python3_EXECUTABLE}")
    endif()
endif()

set(ONBOARDING_CARD_OUTPUT "${CMAKE_BINARY_DIR}/onboarding/matter-node-card.png")
set(ONBOARDING_CARD_VISIBLE_OUTPUT "${CMAKE_SOURCE_DIR}/matter-node-card.png")
add_custom_target(onboarding_card ALL
    COMMAND "${ONBOARDING_CARD_PYTHON}"
            "${CMAKE_SOURCE_DIR}/tools/generate_onboarding_card.py"
            --output-png "${ONBOARDING_CARD_OUTPUT}"
            --passcode "20345744"
            --discriminator "3344 (0xD10)"
            --vendor-id "0xFFF1"
            --product-id "0x8002"
            --serial "BMS-M5C2-<MAC6>"
            --firmware "${MATTER_NODE_GIT_COMMIT}"
            --product "M5StickC Plus2"
            --endpoints "1 Temp, 2 Button, 3 Humidity, 4 Pressure"
            --led-patterns "display: status|button A: next screen|hold 7s: commissioning|hold 15s: reset"
    COMMAND "${CMAKE_COMMAND}" -E copy_if_different
            "${ONBOARDING_CARD_OUTPUT}"
            "${ONBOARDING_CARD_VISIBLE_OUTPUT}"
    DEPENDS "${CMAKE_SOURCE_DIR}/tools/generate_onboarding_card.py"
    COMMENT "Generating printable Matter onboarding card"
    VERBATIM
)
