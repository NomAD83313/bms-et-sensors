set(QRCODEGEN_DIR "$ENV{ESP_MATTER_PATH}/connectedhomeip/connectedhomeip/examples/common/QRCode/repo/c")
if(NOT EXISTS "${QRCODEGEN_DIR}/qrcodegen.c")
    message(FATAL_ERROR "qrcodegen.c not found under ESP_MATTER_PATH: ${QRCODEGEN_DIR}")
endif()

idf_component_register(
    SRCS "main.cpp" "${QRCODEGEN_DIR}/qrcodegen.c"
    INCLUDE_DIRS "." "${QRCODEGEN_DIR}"
    REQUIRES
        esp_matter
        nvs_flash
        esp_driver_gpio
        esp_driver_i2c
        esp_driver_ledc
        esp_driver_spi
        esp_driver_tsens
        esp_event
        esp_lcd
        esp_netif
        esp_wifi
        bms_node_core
)

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
    set(MATTER_NODE_GIT_COMMIT_U32 "0")
endif()
if(DEFINED MATTER_NODE_SOFTWARE_VERSION_U32)
    set(MATTER_NODE_GIT_COMMIT_U32 "${MATTER_NODE_SOFTWARE_VERSION_U32}")
elseif(MATTER_NODE_GIT_DIRTY)
    if(DEFINED MATTER_NODE_BUILD_EPOCH)
        set(_MATTER_NODE_BUILD_EPOCH "${MATTER_NODE_BUILD_EPOCH}")
    else()
        string(TIMESTAMP _MATTER_NODE_BUILD_EPOCH "%s" UTC)
    endif()
    math(EXPR MATTER_NODE_BUILD_VERSION_HEX "${_MATTER_NODE_BUILD_EPOCH}" OUTPUT_FORMAT HEXADECIMAL)
    string(REGEX REPLACE "^0x" "" MATTER_NODE_GIT_COMMIT_U32 "${MATTER_NODE_BUILD_VERSION_HEX}")
endif()

target_compile_definitions(${COMPONENT_LIB} PRIVATE
    MATTER_NODE_GIT_COMMIT="${MATTER_NODE_GIT_COMMIT}"
    MATTER_NODE_GIT_COMMIT_U32=0x${MATTER_NODE_GIT_COMMIT_U32}
)
