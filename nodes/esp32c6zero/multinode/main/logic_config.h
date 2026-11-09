#pragma once

#include <cstddef>

namespace bms::logic {

constexpr size_t kAdcVoltageChannelCount = 7;

struct LogicConfig {
    bool adc_voltage_enabled[kAdcVoltageChannelCount];
};

const LogicConfig &GetActiveLogicConfig();
bool IsAdcVoltageEnabled(size_t channel_index);
const char *GetActiveLogicSource();

} // namespace bms::logic
