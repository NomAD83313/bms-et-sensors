idf_component_register(
    SRCS "src/device_info.cpp"
    INCLUDE_DIRS "include"
    REQUIRES esp_matter
    PRIV_REQUIRES log
)
