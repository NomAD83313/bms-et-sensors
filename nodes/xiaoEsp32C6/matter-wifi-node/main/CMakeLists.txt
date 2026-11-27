idf_component_register(
    SRCS "main.cpp"
    INCLUDE_DIRS "."
    REQUIRES esp_matter nvs_flash esp_driver_i2c esp_adc esp_wifi bms_node_core
)
target_compile_definitions(${COMPONENT_LIB} PRIVATE MATTER_NODE_VERSION="${PROJECT_VER}")
