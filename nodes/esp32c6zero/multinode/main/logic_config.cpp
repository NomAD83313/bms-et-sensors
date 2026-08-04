#include "logic_config.h"

namespace bms::logic {

namespace {

constexpr LogicConfig kCableFallbackLogic = {
    .adc_voltage_enabled = {
        true,
        false,
        false,
        false,
        false,
        true,
        false,
    },
};

} // namespace

const LogicConfig &GetActiveLogicConfig()
{
    return kCableFallbackLogic;
}

bool IsAdcVoltageEnabled(size_t channel_index)
{
    if (channel_index >= kAdcVoltageChannelCount) {
        return false;
    }
    return GetActiveLogicConfig().adc_voltage_enabled[channel_index];
}

const char *GetActiveLogicSource()
{
    return "cable-fallback";
}

} // namespace bms::logic
