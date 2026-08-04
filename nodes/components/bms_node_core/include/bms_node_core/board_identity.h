#pragma once

#include <cstdint>

namespace bms_node_core {

struct BoardIdentity {
    uint16_t vendor_id;
    uint16_t product_id;
    const char *vendor_name;
    const char *product_name;
    const char *serial_prefix;
    uint16_t hw_version;
    const char *hw_version_str;
    const char *software_version_str;
    bool provide_rotating_id;
};

}  // namespace bms_node_core
