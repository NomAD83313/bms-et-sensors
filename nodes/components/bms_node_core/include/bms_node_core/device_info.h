#pragma once

#include "bms_node_core/board_identity.h"

#include "esp_err.h"

#include <cstddef>
#include <cstdint>

#include <platform/DeviceInstanceInfoProvider.h>

namespace bms_node_core {

class DeviceInfoProvider : public chip::DeviceLayer::DeviceInstanceInfoProvider {
public:
    explicit DeviceInfoProvider(const BoardIdentity &identity);

    CHIP_ERROR GetVendorName(char *buf, size_t bufSize) override;
    CHIP_ERROR GetVendorId(uint16_t &vendorId) override;
    CHIP_ERROR GetProductName(char *buf, size_t bufSize) override;
    CHIP_ERROR GetProductId(uint16_t &productId) override;
    CHIP_ERROR GetPartNumber(char *buf, size_t bufSize) override;
    CHIP_ERROR GetProductLabel(char *buf, size_t bufSize) override;
    CHIP_ERROR GetProductURL(char *buf, size_t bufSize) override;
    CHIP_ERROR GetManufacturingDate(uint16_t &year, uint8_t &month, uint8_t &day) override;
    CHIP_ERROR GetHardwareVersion(uint16_t &hardwareVersion) override;
    CHIP_ERROR GetHardwareVersionString(char *buf, size_t bufSize) override;
    CHIP_ERROR GetSerialNumber(char *buf, size_t bufSize) override;
    CHIP_ERROR GetSoftwareVersionString(char *buf, size_t bufSize);
    CHIP_ERROR GetRotatingDeviceIdUniqueId(chip::MutableByteSpan &uniqueIdSpan) override;

    const BoardIdentity &identity() const { return identity_; }
    const char *cached_serial_number() const { return serial_number_; }

private:
    const BoardIdentity &identity_;
    char serial_number_[24] = {};
    uint8_t rotating_id_[6] = {};
    bool rotating_id_valid_ = false;
};

esp_err_t install_device_identity(DeviceInfoProvider &provider);

}  // namespace bms_node_core
