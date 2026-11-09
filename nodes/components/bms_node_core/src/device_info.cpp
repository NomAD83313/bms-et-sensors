#include "bms_node_core/device_info.h"

#include "esp_log.h"
#include "esp_mac.h"
#include "esp_matter.h"
#include "esp_matter_providers.h"

#include <cstdio>
#include <cstring>

#include <platform/CHIPDeviceLayer.h>
#include <platform/ESP32/ESP32Config.h>

namespace bms_node_core {

namespace {
constexpr const char *kTag = "bms_node_core/device_info";
}

DeviceInfoProvider::DeviceInfoProvider(const BoardIdentity &identity)
    : identity_(identity)
{
    uint8_t mac[6] = {};
    if (esp_base_mac_addr_get(mac) == ESP_OK) {
        std::memcpy(rotating_id_, mac, sizeof(mac));
        rotating_id_valid_ = true;
        std::snprintf(serial_number_, sizeof(serial_number_), "%s%02X%02X%02X",
                      identity_.serial_prefix, mac[3], mac[4], mac[5]);
    } else {
        std::snprintf(serial_number_, sizeof(serial_number_), "%sUNKNOWN",
                      identity_.serial_prefix);
    }
}

CHIP_ERROR DeviceInfoProvider::GetVendorName(char *buf, size_t bufSize) {
    chip::Platform::CopyString(buf, bufSize, identity_.vendor_name);
    return CHIP_NO_ERROR;
}

CHIP_ERROR DeviceInfoProvider::GetVendorId(uint16_t &vendorId) {
    vendorId = identity_.vendor_id;
    return CHIP_NO_ERROR;
}

CHIP_ERROR DeviceInfoProvider::GetProductName(char *buf, size_t bufSize) {
    chip::Platform::CopyString(buf, bufSize, identity_.product_name);
    return CHIP_NO_ERROR;
}

CHIP_ERROR DeviceInfoProvider::GetProductId(uint16_t &productId) {
    productId = identity_.product_id;
    return CHIP_NO_ERROR;
}

CHIP_ERROR DeviceInfoProvider::GetPartNumber(char *, size_t) {
    return CHIP_ERROR_UNSUPPORTED_CHIP_FEATURE;
}

CHIP_ERROR DeviceInfoProvider::GetProductLabel(char *, size_t) {
    return CHIP_ERROR_UNSUPPORTED_CHIP_FEATURE;
}

CHIP_ERROR DeviceInfoProvider::GetProductURL(char *, size_t) {
    return CHIP_ERROR_UNSUPPORTED_CHIP_FEATURE;
}

CHIP_ERROR DeviceInfoProvider::GetManufacturingDate(uint16_t &, uint8_t &, uint8_t &) {
    return CHIP_ERROR_UNSUPPORTED_CHIP_FEATURE;
}

CHIP_ERROR DeviceInfoProvider::GetHardwareVersion(uint16_t &hardwareVersion) {
    hardwareVersion = identity_.hw_version;
    return CHIP_NO_ERROR;
}

CHIP_ERROR DeviceInfoProvider::GetHardwareVersionString(char *buf, size_t bufSize) {
    chip::Platform::CopyString(buf, bufSize, identity_.hw_version_str);
    return CHIP_NO_ERROR;
}

CHIP_ERROR DeviceInfoProvider::GetSerialNumber(char *buf, size_t bufSize) {
    chip::Platform::CopyString(buf, bufSize, serial_number_);
    return CHIP_NO_ERROR;
}

CHIP_ERROR DeviceInfoProvider::GetSoftwareVersionString(char *buf, size_t bufSize) {
    chip::Platform::CopyString(buf, bufSize, identity_.software_version_str);
    return CHIP_NO_ERROR;
}

CHIP_ERROR DeviceInfoProvider::GetRotatingDeviceIdUniqueId(chip::MutableByteSpan &uniqueIdSpan) {
    if (!identity_.provide_rotating_id || !rotating_id_valid_) {
        return CHIP_ERROR_UNSUPPORTED_CHIP_FEATURE;
    }
    return chip::CopySpanToMutableSpan(chip::ByteSpan(rotating_id_), uniqueIdSpan);
}

esp_err_t install_device_identity(DeviceInfoProvider &provider) {
    const BoardIdentity &id = provider.identity();
    const char *serial_number = provider.cached_serial_number();

    esp_matter::set_custom_device_instance_info_provider(&provider);
    chip::DeviceLayer::SetDeviceInstanceInfoProvider(&provider);

    CHIP_ERROR write_err = chip::DeviceLayer::Internal::ESP32Config::WriteConfigValueStr(
        chip::DeviceLayer::Internal::ESP32Config::kConfigKey_SerialNum, serial_number);
    if (write_err != CHIP_NO_ERROR) {
        ESP_LOGW(kTag, "Failed to persist serial %s to chip-factory/serial-num: %s",
                 serial_number, chip::ErrorStr(write_err));
    } else {
        char stored[chip::DeviceLayer::ConfigurationManager::kMaxSerialNumberLength + 1] = {};
        size_t len = 0;
        CHIP_ERROR read_err = chip::DeviceLayer::Internal::ESP32Config::ReadConfigValueStr(
            chip::DeviceLayer::Internal::ESP32Config::kConfigKey_SerialNum,
            stored, sizeof(stored), len);
        if (read_err == CHIP_NO_ERROR) {
            ESP_LOGI(kTag, "Stored chip-factory/serial-num: %s", stored);
        } else {
            ESP_LOGW(kTag, "Serial persisted but readback failed: %s", chip::ErrorStr(read_err));
        }
    }

    ESP_LOGI(kTag,
             "Basic Information provider: VID=0x%04X PID=0x%04X Vendor=%s Product=%s Serial=%s Software=%s",
             id.vendor_id, id.product_id, id.vendor_name, id.product_name,
             serial_number, id.software_version_str);
    return ESP_OK;
}

}  // namespace bms_node_core
