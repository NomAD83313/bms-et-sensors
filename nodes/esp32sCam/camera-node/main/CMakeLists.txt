idf_component_register(
    SRCS "main.cpp"
    INCLUDE_DIRS "."
    REQUIRES
        esp_matter
        nvs_flash
        esp_driver_gpio
        esp_event
        esp_netif
        esp_wifi
        bms_node_core
)

target_compile_definitions(${COMPONENT_LIB} PRIVATE
    ESP32SCAM_NODE_GIT_COMMIT="${ESP32SCAM_NODE_GIT_COMMIT}"
)
