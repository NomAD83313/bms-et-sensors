#include "driver/gpio.h"
#include "driver/temperature_sensor.h"
#include "esp_adc/adc_cali.h"
#include "esp_adc/adc_cali_scheme.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_check.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_matter.h"
#include "esp_matter_endpoint.h"
#include "esp_matter_providers.h"
#include "esp_matter_test_event_trigger.h"
#include "esp_openthread.h"
#include "esp_openthread_types.h"
#include "esp_pm.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "led_strip.h"
#include "nvs_flash.h"
#include "platform/ESP32/OpenthreadLauncher.h"

#include "bms_node_core/device_info.h"
#include "bms_node_core/thread_diagnostics.h"
#include "logic_config.h"

#include <app/server/Server.h>
#include <app/TestEventTriggerDelegate.h>
#include <app/clusters/electrical-power-measurement-server/electrical-power-measurement-server.h>
#include <app/data-model/List.h>
#include <app/reporting/reporting.h>
#include <credentials/DeviceAttestationCredsProvider.h>
#include <credentials/examples/DeviceAttestationCredsExample.h>
#include <platform/CHIPDeviceLayer.h>
#include <platform/DeviceInstanceInfoProvider.h>
#include <clusters/BooleanState/AttributeIds.h>
#include <clusters/BooleanState/ClusterId.h>
#include <clusters/BasicInformation/AttributeIds.h>
#include <clusters/BasicInformation/ClusterId.h>
#include <clusters/ElectricalPowerMeasurement/AttributeIds.h>
#include <clusters/ElectricalPowerMeasurement/ClusterId.h>
#include <clusters/TemperatureMeasurement/AttributeIds.h>
#include <clusters/TemperatureMeasurement/ClusterId.h>
#include <platform/ConnectivityManager.h>
#include <platform/PlatformManager.h>
#include <setup_payload/OnboardingCodesUtil.h>

#include <cmath>
#include <cstdio>
#include <cstring>

static const char *TAG = "bms_multinode";
#ifndef MATTER_NODE_GIT_COMMIT
#define MATTER_NODE_GIT_COMMIT "unknown"
#endif
#ifndef MATTER_NODE_GIT_COMMIT_U32
#define MATTER_NODE_GIT_COMMIT_U32 0
#endif

constexpr bms_node_core::BoardIdentity kBoardIdentity = {
    .vendor_id            = 0xFFF1,
    .product_id           = 0x8001,
    .vendor_name          = "BMS DOA",
    .product_name         = "ESP32-C6-Zero Multinode",
    .serial_prefix        = "BMS-C6M-",
    .hw_version           = 1,
    .hw_version_str       = "v1.0",
    .software_version_str = MATTER_NODE_GIT_COMMIT,
    .provide_rotating_id  = false,
};

static bms_node_core::DeviceInfoProvider s_device_info_provider(kBoardIdentity);

using namespace esp_matter;
using namespace esp_matter::attribute;
using namespace esp_matter::endpoint;

namespace {

constexpr gpio_num_t kRgbLedGpio = GPIO_NUM_8;
constexpr gpio_num_t kBootButtonGpio = GPIO_NUM_9;
constexpr uint32_t kTelemetryPeriodMs = 5000;
constexpr uint32_t kHeartbeatPeriodMs = 30000;
constexpr uint32_t kButtonPollPeriodMs = 50;
constexpr uint32_t kButtonDebounceMs = 30;
constexpr uint32_t kCommissioningHoldMs = 7000;
constexpr uint32_t kFactoryResetHoldMs = 15000;
constexpr uint32_t kCommissioningWindowSeconds = 180;
constexpr uint32_t kBootIndicatorMs = 5000;
constexpr uint32_t kIndicatorPeriodMs = 50;
constexpr uint32_t kAirRebootDelayMs = 500;
constexpr uint64_t kBmsAirRebootEventTrigger = 0xFFF10001ull;
constexpr uint8_t kLedBrightnessCap = 24;
constexpr adc_atten_t kAdcAttenuation = ADC_ATTEN_DB_12;
constexpr size_t kAdcDiagnosticChannelCount = 7;
constexpr adc_channel_t kAdcDiagnosticChannels[kAdcDiagnosticChannelCount] = {
    ADC_CHANNEL_0,
    ADC_CHANNEL_1,
    ADC_CHANNEL_2,
    ADC_CHANNEL_3,
    ADC_CHANNEL_4,
    ADC_CHANNEL_5,
    ADC_CHANNEL_6,
};
constexpr gpio_num_t kAdcDiagnosticGpios[kAdcDiagnosticChannelCount] = {
    GPIO_NUM_0,
    GPIO_NUM_1,
    GPIO_NUM_2,
    GPIO_NUM_3,
    GPIO_NUM_4,
    GPIO_NUM_5,
    GPIO_NUM_6,
};
static_assert(kAdcDiagnosticChannelCount == bms::logic::kAdcVoltageChannelCount,
              "ADC diagnostics and logic config must describe the same channel count");

enum class IndicatorState : uint8_t {
    Boot,
    Commissioning,
    ThreadDetached,
    Running,
    CommissioningPreview,
    FactoryResetPreview,
    FactoryResetActive,
};

class AdcVoltageDelegate : public chip::app::Clusters::ElectricalPowerMeasurement::Delegate {
public:
    chip::app::Clusters::ElectricalPowerMeasurement::PowerModeEnum GetPowerMode() override
    {
        return chip::app::Clusters::ElectricalPowerMeasurement::PowerModeEnum::kDc;
    }

    uint8_t GetNumberOfMeasurementTypes() override { return 1; }

    CHIP_ERROR StartAccuracyRead() override { return CHIP_NO_ERROR; }

    CHIP_ERROR GetAccuracyByIndex(
        uint8_t index,
        chip::app::Clusters::ElectricalPowerMeasurement::Structs::MeasurementAccuracyStruct::Type &accuracy) override
    {
        using namespace chip;
        using namespace chip::app::Clusters::ElectricalPowerMeasurement;
        using namespace chip::app::Clusters::ElectricalPowerMeasurement::Structs;

        static const MeasurementAccuracyRangeStruct::Type kVoltageAccuracyRanges[] = {
            {
                .rangeMin = 0,
                .rangeMax = 3300,
                .percentMax = MakeOptional(static_cast<Percent100ths>(500)),
                .percentMin = MakeOptional(static_cast<Percent100ths>(100)),
                .percentTypical = MakeOptional(static_cast<Percent100ths>(300)),
            },
        };

        static const MeasurementAccuracyStruct::Type kVoltageAccuracy = {
            .measurementType = MeasurementTypeEnum::kVoltage,
            .measured = true,
            .minMeasuredValue = 0,
            .maxMeasuredValue = 3300,
            .accuracyRanges = chip::app::DataModel::List<const MeasurementAccuracyRangeStruct::Type>(kVoltageAccuracyRanges),
        };

        if (index > 0) {
            return CHIP_ERROR_PROVIDER_LIST_EXHAUSTED;
        }
        accuracy = kVoltageAccuracy;
        return CHIP_NO_ERROR;
    }

    CHIP_ERROR EndAccuracyRead() override { return CHIP_NO_ERROR; }
    CHIP_ERROR StartRangesRead() override { return CHIP_NO_ERROR; }
    CHIP_ERROR GetRangeByIndex(
        uint8_t,
        chip::app::Clusters::ElectricalPowerMeasurement::Structs::MeasurementRangeStruct::Type &) override
    {
        return CHIP_ERROR_PROVIDER_LIST_EXHAUSTED;
    }
    CHIP_ERROR EndRangesRead() override { return CHIP_NO_ERROR; }
    CHIP_ERROR StartHarmonicCurrentsRead() override { return CHIP_NO_ERROR; }
    CHIP_ERROR GetHarmonicCurrentsByIndex(
        uint8_t,
        chip::app::Clusters::ElectricalPowerMeasurement::Structs::HarmonicMeasurementStruct::Type &) override
    {
        return CHIP_ERROR_PROVIDER_LIST_EXHAUSTED;
    }
    CHIP_ERROR EndHarmonicCurrentsRead() override { return CHIP_NO_ERROR; }
    CHIP_ERROR StartHarmonicPhasesRead() override { return CHIP_NO_ERROR; }
    CHIP_ERROR GetHarmonicPhasesByIndex(
        uint8_t,
        chip::app::Clusters::ElectricalPowerMeasurement::Structs::HarmonicMeasurementStruct::Type &) override
    {
        return CHIP_ERROR_PROVIDER_LIST_EXHAUSTED;
    }
    CHIP_ERROR EndHarmonicPhasesRead() override { return CHIP_NO_ERROR; }

    chip::app::DataModel::Nullable<int64_t> GetVoltage() override { return m_voltage; }
    chip::app::DataModel::Nullable<int64_t> GetActiveCurrent() override { return chip::app::DataModel::Nullable<int64_t>(); }
    chip::app::DataModel::Nullable<int64_t> GetReactiveCurrent() override { return chip::app::DataModel::Nullable<int64_t>(); }
    chip::app::DataModel::Nullable<int64_t> GetApparentCurrent() override { return chip::app::DataModel::Nullable<int64_t>(); }
    chip::app::DataModel::Nullable<int64_t> GetActivePower() override { return chip::app::DataModel::Nullable<int64_t>(); }
    chip::app::DataModel::Nullable<int64_t> GetReactivePower() override { return chip::app::DataModel::Nullable<int64_t>(); }
    chip::app::DataModel::Nullable<int64_t> GetApparentPower() override { return chip::app::DataModel::Nullable<int64_t>(); }
    chip::app::DataModel::Nullable<int64_t> GetRMSVoltage() override { return chip::app::DataModel::Nullable<int64_t>(); }
    chip::app::DataModel::Nullable<int64_t> GetRMSCurrent() override { return chip::app::DataModel::Nullable<int64_t>(); }
    chip::app::DataModel::Nullable<int64_t> GetRMSPower() override { return chip::app::DataModel::Nullable<int64_t>(); }
    chip::app::DataModel::Nullable<int64_t> GetFrequency() override { return chip::app::DataModel::Nullable<int64_t>(); }
    chip::app::DataModel::Nullable<int64_t> GetPowerFactor() override { return chip::app::DataModel::Nullable<int64_t>(); }
    chip::app::DataModel::Nullable<int64_t> GetNeutralCurrent() override { return chip::app::DataModel::Nullable<int64_t>(); }

    void SetVoltageMv(int64_t voltage_mv)
    {
        chip::app::DataModel::Nullable<int64_t> new_voltage(voltage_mv);
        if (m_voltage == new_voltage) {
            return;
        }
        m_voltage = new_voltage;
        if (mEndpointId != 0) {
            chip::DeviceLayer::PlatformMgr().LockChipStack();
            MatterReportingAttributeChangeCallback(
                mEndpointId,
                chip::app::Clusters::ElectricalPowerMeasurement::Id,
                chip::app::Clusters::ElectricalPowerMeasurement::Attributes::Voltage::Id);
            chip::DeviceLayer::PlatformMgr().UnlockChipStack();
        }
    }

private:
    chip::app::DataModel::Nullable<int64_t> m_voltage;
};

struct DeviceContext {
    uint16_t temp_ep_id = 0;
    uint16_t button_ep_id = 0;
    uint16_t adc_voltage_ep_ids[kAdcDiagnosticChannelCount] = {};
    temperature_sensor_handle_t temp_handle = nullptr;
    led_strip_handle_t led_strip = nullptr;
    adc_oneshot_unit_handle_t adc1_handle = nullptr;
    adc_cali_handle_t adc_cali_handles[kAdcDiagnosticChannelCount] = {};
    bool adc_cali_enabled[kAdcDiagnosticChannelCount] = {};
    bool button_pressed = false;
    uint8_t last_led_r = 0;
    uint8_t last_led_g = 0;
    uint8_t last_led_b = 0;
    bool led_initialized = false;
};

struct IndicatorCtx {
    volatile bool thread_attached = false;
    volatile bool commissioned = false;
    volatile bool window_open = false;
    volatile bool button_preview_commissioning = false;
    volatile bool button_preview_factory_reset = false;
    volatile bool factory_reset_active = false;
};

DeviceContext s_device;
IndicatorCtx s_indicator;
AdcVoltageDelegate s_adc_voltage_delegates[kAdcDiagnosticChannelCount];
TickType_t s_boot_tick = 0;
uint8_t s_test_event_enable_key[chip::TestEventTriggerDelegate::kEnableKeyLength] = {
    0x00, 0x11, 0x22, 0x33,
    0x44, 0x55, 0x66, 0x77,
    0x88, 0x99, 0xaa, 0xbb,
    0xcc, 0xdd, 0xee, 0xff,
};

void air_reboot_task(void *)
{
    vTaskDelay(pdMS_TO_TICKS(kAirRebootDelayMs));
    ESP_LOGW(TAG, "Rebooting after Matter air reboot request");
    esp_restart();
}

class BmsNodeTestEventTriggerHandler : public chip::TestEventTriggerHandler
{
public:
    CHIP_ERROR HandleEventTrigger(uint64_t eventTrigger) override
    {
        if (eventTrigger != kBmsAirRebootEventTrigger) {
            return CHIP_ERROR_INVALID_ARGUMENT;
        }
        ESP_LOGW(TAG, "Matter air reboot requested");
        xTaskCreate(air_reboot_task, "air_reboot_task", 2048, nullptr, 5, nullptr);
        return CHIP_NO_ERROR;
    }
};

chip::SimpleTestEventTriggerDelegate s_test_event_trigger_delegate;
BmsNodeTestEventTriggerHandler s_bms_test_event_trigger_handler;

// Matter spec: temperature in 0.01 °C units (int16_t)
int16_t to_matter_temp(float celsius) { return static_cast<int16_t>(celsius * 100.0f); }

esp_err_t ensure_serial_number_attribute()
{
    cluster_t *basic_info_cluster = cluster::get(static_cast<uint16_t>(0), chip::app::Clusters::BasicInformation::Id);
    if (!basic_info_cluster) {
        ESP_LOGE(TAG, "Basic Information cluster not found on root endpoint");
        return ESP_ERR_NOT_FOUND;
    }

    attribute_t *serial_attribute = attribute::get(
        0,
        chip::app::Clusters::BasicInformation::Id,
        chip::app::Clusters::BasicInformation::Attributes::SerialNumber::Id);
    if (serial_attribute) {
        ESP_LOGI(TAG, "Basic Information SerialNumber attribute already present");
        return ESP_OK;
    }

    serial_attribute = cluster::basic_information::attribute::create_serial_number(basic_info_cluster, nullptr, 0);
    if (!serial_attribute) {
        ESP_LOGE(TAG, "Failed to create Basic Information SerialNumber attribute");
        return ESP_FAIL;
    }

    ESP_LOGI(TAG, "Created Basic Information SerialNumber attribute (0/40/15)");
    return ESP_OK;
}

bool read_boot_button_pressed()
{
    return gpio_get_level(kBootButtonGpio) == 0;
}

const char *reset_reason_str(esp_reset_reason_t r)
{
    switch (r) {
    case ESP_RST_POWERON:   return "POWERON";
    case ESP_RST_EXT:       return "EXT";
    case ESP_RST_SW:        return "SW";
    case ESP_RST_PANIC:     return "PANIC";
    case ESP_RST_INT_WDT:   return "INT_WDT";
    case ESP_RST_TASK_WDT:  return "TASK_WDT";
    case ESP_RST_WDT:       return "OTHER_WDT";
    case ESP_RST_DEEPSLEEP: return "DEEPSLEEP";
    case ESP_RST_BROWNOUT:  return "BROWNOUT";
    case ESP_RST_SDIO:      return "SDIO";
    case ESP_RST_USB:       return "USB";
    case ESP_RST_JTAG:      return "JTAG";
    default:                return "UNKNOWN";
    }
}

const char *indicator_state_str(IndicatorState s)
{
    switch (s) {
    case IndicatorState::Boot:                  return "boot/white-pulse";
    case IndicatorState::Commissioning:         return "commissioning/blue-blink";
    case IndicatorState::ThreadDetached:        return "thread-detached/yellow";
    case IndicatorState::Running:               return "running/green";
    case IndicatorState::CommissioningPreview:  return "btn-preview-commissioning/blue-fast";
    case IndicatorState::FactoryResetPreview:   return "btn-preview-factory-reset/red-fast";
    case IndicatorState::FactoryResetActive:    return "factory-reset/red";
    }
    return "?";
}

void log_onboarding_codes()
{
    char qr_code_buffer[256] = {};
    chip::MutableCharSpan qr_code(qr_code_buffer);
    CHIP_ERROR qr_err = GetQRCode(qr_code, chip::RendezvousInformationFlag::kBLE);
    if (qr_err == CHIP_NO_ERROR) {
        ESP_LOGI(TAG, "BLE onboarding QR code: %s", qr_code.data());
    } else {
        ESP_LOGW(TAG, "Failed to generate BLE onboarding QR code: %s", chip::ErrorStr(qr_err));
    }

    char manual_code_buffer[chip::kManualSetupLongCodeCharLength + 1] = {};
    chip::MutableCharSpan manual_code(manual_code_buffer);
    CHIP_ERROR manual_err = GetManualPairingCode(manual_code, chip::RendezvousInformationFlag::kBLE);
    if (manual_err == CHIP_NO_ERROR) {
        ESP_LOGI(TAG, "BLE manual pairing code: %s", manual_code.data());
    } else {
        ESP_LOGW(TAG, "Failed to generate BLE manual pairing code: %s", chip::ErrorStr(manual_err));
    }
}

void log_commissioning_state(const char *reason)
{
    const size_t fabric_count = chip::Server::GetInstance().GetFabricTable().FabricCount();
    ESP_LOGI(TAG, "Commissioning state (%s): fabric_count=%u commissioned=%s",
             reason,
             static_cast<unsigned>(fabric_count),
             fabric_count > 0 ? "yes" : "no");
}

// NOTE: The WS2812 on the ESP32-C6-Zero has swapped Red and Green channels
// (RGB wiring instead of standard GRB). We compensate by swapping r<->g here.
esp_err_t set_led_color(uint8_t r, uint8_t g, uint8_t b, uint8_t intensity)
{
    if (!s_device.led_strip) {
        return ESP_ERR_INVALID_STATE;
    }
    const uint16_t scale = static_cast<uint16_t>(intensity) * kLedBrightnessCap / 255;
    const uint8_t rr = static_cast<uint16_t>(r) * scale / 255;
    const uint8_t gg = static_cast<uint16_t>(g) * scale / 255;
    const uint8_t bb = static_cast<uint16_t>(b) * scale / 255;
    if (s_device.led_initialized &&
        rr == s_device.last_led_r &&
        gg == s_device.last_led_g &&
        bb == s_device.last_led_b) {
        return ESP_OK;
    }

    if (rr == 0 && gg == 0 && bb == 0) {
        ESP_RETURN_ON_ERROR(led_strip_clear(s_device.led_strip), TAG, "led_strip_clear failed");
    } else {
        ESP_RETURN_ON_ERROR(led_strip_set_pixel(s_device.led_strip, 0, gg, rr, bb), TAG, "led_strip_set_pixel failed");
    }
    ESP_RETURN_ON_ERROR(led_strip_refresh(s_device.led_strip), TAG, "led_strip_refresh failed");
    s_device.last_led_r = rr;
    s_device.last_led_g = gg;
    s_device.last_led_b = bb;
    s_device.led_initialized = true;
    return ESP_OK;
}

uint8_t breath_intensity(uint32_t tick_ms, uint32_t period_ms)
{
    const float phase = static_cast<float>(tick_ms % period_ms) / static_cast<float>(period_ms);
    const float v = 0.5f - 0.5f * std::cos(phase * 2.0f * static_cast<float>(M_PI));
    return static_cast<uint8_t>(v * 255.0f);
}

bool blink_on(uint32_t tick_ms, uint32_t half_period_ms)
{
    return (tick_ms / half_period_ms) % 2 == 0;
}

IndicatorState compute_indicator_state(uint32_t since_boot_ms)
{
    if (s_indicator.factory_reset_active)         return IndicatorState::FactoryResetActive;
    if (s_indicator.button_preview_factory_reset) return IndicatorState::FactoryResetPreview;
    if (s_indicator.button_preview_commissioning) return IndicatorState::CommissioningPreview;
    if (since_boot_ms < kBootIndicatorMs)         return IndicatorState::Boot;
    if (s_indicator.window_open)                  return IndicatorState::Commissioning;
    if (!s_indicator.commissioned)                return IndicatorState::Commissioning;
    if (!s_indicator.thread_attached)             return IndicatorState::ThreadDetached;
    return IndicatorState::Running;
}

void render_indicator(IndicatorState state, uint32_t tick_ms)
{
    switch (state) {
    case IndicatorState::Boot: {
        const uint8_t i = breath_intensity(tick_ms, 1500);
        set_led_color(255, 255, 255, i);
        break;
    }
    case IndicatorState::Commissioning: {
        const bool on = blink_on(tick_ms, 250);
        set_led_color(0, 0, 255, on ? 255 : 0);
        break;
    }
    case IndicatorState::ThreadDetached: {
        const bool on = blink_on(tick_ms, 1000);
        set_led_color(255, 200, 0, on ? 255 : 30);
        break;
    }
    case IndicatorState::Running:
        set_led_color(0, 255, 0, 255);
        break;
    case IndicatorState::CommissioningPreview: {
        const bool on = blink_on(tick_ms, 125);
        set_led_color(0, 0, 255, on ? 255 : 0);
        break;
    }
    case IndicatorState::FactoryResetPreview: {
        const bool on = blink_on(tick_ms, 80);
        set_led_color(255, 0, 0, on ? 255 : 0);
        break;
    }
    case IndicatorState::FactoryResetActive: {
        const bool on = blink_on(tick_ms, 100);
        set_led_color(255, 0, 0, on ? 255 : 0);
        break;
    }
    }
}

esp_err_t update_temperature_measurement(float temperature_c)
{
    esp_matter_attr_val_t temp_val = esp_matter_nullable_int16(
        nullable<int16_t>(to_matter_temp(temperature_c)));
    return update(s_device.temp_ep_id,
                  chip::app::Clusters::TemperatureMeasurement::Id,
                  chip::app::Clusters::TemperatureMeasurement::Attributes::MeasuredValue::Id,
                  &temp_val);
}

esp_err_t update_button_state(bool pressed)
{
    esp_matter_attr_val_t state_val = esp_matter_bool(pressed);
    return update(s_device.button_ep_id,
                  chip::app::Clusters::BooleanState::Id,
                  chip::app::Clusters::BooleanState::Attributes::StateValue::Id,
                  &state_val);
}

esp_err_t update_adc_voltage(size_t channel_index, int voltage_mv)
{
    if (channel_index >= kAdcDiagnosticChannelCount ||
        s_device.adc_voltage_ep_ids[channel_index] == 0) {
        return ESP_ERR_INVALID_STATE;
    }
    s_adc_voltage_delegates[channel_index].SetVoltageMv(voltage_mv);
    return ESP_OK;
}

esp_err_t init_internal_temperature_sensor()
{
    temperature_sensor_config_t temp_sensor_config = TEMPERATURE_SENSOR_CONFIG_DEFAULT(20, 80);
    ESP_RETURN_ON_ERROR(temperature_sensor_install(&temp_sensor_config, &s_device.temp_handle), TAG, "Failed to install temperature sensor");
    ESP_RETURN_ON_ERROR(temperature_sensor_enable(s_device.temp_handle), TAG, "Failed to enable temperature sensor");
    return ESP_OK;
}

bool init_adc_calibration(adc_channel_t channel, adc_cali_handle_t *out_handle)
{
    adc_cali_handle_t handle = nullptr;
    esp_err_t ret = ESP_FAIL;
    bool calibrated = false;

#if ADC_CALI_SCHEME_CURVE_FITTING_SUPPORTED
    if (!calibrated) {
        adc_cali_curve_fitting_config_t cali_config = {
            .unit_id = ADC_UNIT_1,
            .chan = channel,
            .atten = kAdcAttenuation,
            .bitwidth = ADC_BITWIDTH_DEFAULT,
        };
        ret = adc_cali_create_scheme_curve_fitting(&cali_config, &handle);
        calibrated = (ret == ESP_OK);
    }
#endif

#if ADC_CALI_SCHEME_LINE_FITTING_SUPPORTED
    if (!calibrated) {
        adc_cali_line_fitting_config_t cali_config = {
            .unit_id = ADC_UNIT_1,
            .atten = kAdcAttenuation,
            .bitwidth = ADC_BITWIDTH_DEFAULT,
        };
        ret = adc_cali_create_scheme_line_fitting(&cali_config, &handle);
        calibrated = (ret == ESP_OK);
    }
#endif

    *out_handle = handle;
    if (!calibrated) {
        ESP_LOGW(TAG, "ADC calibration unavailable for channel %d: %s",
                 static_cast<int>(channel), esp_err_to_name(ret));
    }
    return calibrated;
}

esp_err_t init_adc_diagnostics()
{
    adc_oneshot_unit_init_cfg_t init_config = {
        .unit_id = ADC_UNIT_1,
    };
    ESP_RETURN_ON_ERROR(adc_oneshot_new_unit(&init_config, &s_device.adc1_handle),
                        TAG, "Failed to create ADC1 oneshot unit");

    adc_oneshot_chan_cfg_t channel_config = {
        .atten = kAdcAttenuation,
        .bitwidth = ADC_BITWIDTH_DEFAULT,
    };
    for (size_t i = 0; i < kAdcDiagnosticChannelCount; ++i) {
        ESP_RETURN_ON_ERROR(adc_oneshot_config_channel(
                                s_device.adc1_handle,
                                kAdcDiagnosticChannels[i],
                                &channel_config),
                            TAG, "Failed to configure ADC channel");
        s_device.adc_cali_enabled[i] = init_adc_calibration(
            kAdcDiagnosticChannels[i],
            &s_device.adc_cali_handles[i]);
    }
    ESP_LOGI(TAG, "ADC diagnostics enabled on GPIO0..GPIO6");
    return ESP_OK;
}

esp_err_t read_adc_voltage_mv(size_t channel_index, int *out_raw, int *out_mv)
{
    if (!s_device.adc1_handle || channel_index >= kAdcDiagnosticChannelCount || !out_raw || !out_mv) {
        return ESP_ERR_INVALID_ARG;
    }

    int raw = 0;
    ESP_RETURN_ON_ERROR(adc_oneshot_read(s_device.adc1_handle, kAdcDiagnosticChannels[channel_index], &raw),
                        TAG, "Failed to read ADC channel");

    int mv = -1;
    if (s_device.adc_cali_enabled[channel_index]) {
        ESP_RETURN_ON_ERROR(adc_cali_raw_to_voltage(s_device.adc_cali_handles[channel_index], raw, &mv),
                            TAG, "Failed to convert ADC raw value");
    }

    *out_raw = raw;
    *out_mv = mv;
    return ESP_OK;
}

void log_adc_diagnostics()
{
    if (!s_device.adc1_handle) {
        return;
    }

    char line[256] = {};
    size_t offset = 0;
    offset += snprintf(line + offset, sizeof(line) - offset, "ADC diagnostics:");
    for (size_t i = 0; i < kAdcDiagnosticChannelCount && offset < sizeof(line); ++i) {
        int raw = 0;
        int mv = -1;
        esp_err_t err = read_adc_voltage_mv(i, &raw, &mv);
        if (err != ESP_OK) {
            offset += snprintf(line + offset, sizeof(line) - offset,
                               " GPIO%d=err:%s",
                               static_cast<int>(kAdcDiagnosticGpios[i]),
                               esp_err_to_name(err));
            continue;
        }

        if (mv >= 0) {
            offset += snprintf(line + offset, sizeof(line) - offset,
                               " GPIO%d=%d/%dmV",
                               static_cast<int>(kAdcDiagnosticGpios[i]), raw, mv);
        } else {
            offset += snprintf(line + offset, sizeof(line) - offset,
                               " GPIO%d=%d/raw",
                               static_cast<int>(kAdcDiagnosticGpios[i]), raw);
        }
    }
    ESP_LOGI(TAG, "%s", line);
}

esp_err_t init_led_strip()
{
    led_strip_config_t strip_config = {
        .strip_gpio_num = static_cast<int>(kRgbLedGpio),
        .max_leds = 1,
    };
    led_strip_rmt_config_t rmt_config = {
        .resolution_hz = 10 * 1000 * 1000,
        .flags = {
            .with_dma = false,
        },
    };
    ESP_RETURN_ON_ERROR(led_strip_new_rmt_device(&strip_config, &rmt_config, &s_device.led_strip), TAG, "Failed to create LED strip");
    ESP_RETURN_ON_ERROR(led_strip_clear(s_device.led_strip), TAG, "Failed to clear RGB LED");
    s_device.last_led_r = 0;
    s_device.last_led_g = 0;
    s_device.last_led_b = 0;
    s_device.led_initialized = true;
    return ESP_OK;
}

esp_err_t init_boot_button()
{
    gpio_config_t boot_button_config = {};
    boot_button_config.pin_bit_mask = 1ULL << kBootButtonGpio;
    boot_button_config.mode = GPIO_MODE_INPUT;
    boot_button_config.pull_up_en = GPIO_PULLUP_ENABLE;
    boot_button_config.pull_down_en = GPIO_PULLDOWN_DISABLE;
    boot_button_config.intr_type = GPIO_INTR_DISABLE;
    return gpio_config(&boot_button_config);
}

void update_thread_state_cache()
{
    s_indicator.thread_attached = chip::DeviceLayer::ConnectivityMgr().IsThreadAttached();
    s_indicator.commissioned = chip::Server::GetInstance().GetFabricTable().FabricCount() > 0;
    s_indicator.window_open = chip::Server::GetInstance().GetCommissioningWindowManager().IsCommissioningWindowOpen();
}

void schedule_commissioning_window()
{
    chip::DeviceLayer::PlatformMgr().ScheduleWork(
        [](intptr_t) {
            CHIP_ERROR err = chip::Server::GetInstance().GetCommissioningWindowManager()
                .OpenBasicCommissioningWindow(
                    chip::System::Clock::Seconds32(kCommissioningWindowSeconds),
                    chip::CommissioningWindowAdvertisement::kAllSupported);
            if (err != CHIP_NO_ERROR) {
                ESP_LOGE(TAG, "OpenBasicCommissioningWindow failed: %s", chip::ErrorStr(err));
            } else {
                ESP_LOGI(TAG, "Basic commissioning window opened (%us)",
                         static_cast<unsigned>(kCommissioningWindowSeconds));
            }
        }, 0);
}

void schedule_factory_reset()
{
    s_indicator.factory_reset_active = true;
    chip::Server::GetInstance().ScheduleFactoryReset();
}

esp_err_t init_test_event_trigger_delegate()
{
    CHIP_ERROR err = s_test_event_trigger_delegate.Init(chip::ByteSpan(s_test_event_enable_key));
    if (err != CHIP_NO_ERROR) {
        ESP_LOGE(TAG, "Failed to initialize TestEventTrigger delegate: %s", chip::ErrorStr(err));
        return ESP_FAIL;
    }

    err = s_test_event_trigger_delegate.AddHandler(&s_bms_test_event_trigger_handler);
    if (err != CHIP_NO_ERROR) {
        ESP_LOGE(TAG, "Failed to register BMS TestEventTrigger handler: %s", chip::ErrorStr(err));
        return ESP_FAIL;
    }

    esp_err_t ret = esp_matter::test_event_trigger::set_delegate(&s_test_event_trigger_delegate);
    if (ret == ESP_OK) {
        ESP_LOGI(TAG, "Matter air reboot trigger registered: 0x%llx",
                 static_cast<unsigned long long>(kBmsAirRebootEventTrigger));
    }
    return ret;
}

esp_err_t init_power_management()
{
    esp_pm_config_t pm_config = {
        .max_freq_mhz = CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ,
        .min_freq_mhz = CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ,
#if CONFIG_FREERTOS_USE_TICKLESS_IDLE
        .light_sleep_enable = true,
#else
        .light_sleep_enable = false,
#endif
    };
    return esp_pm_configure(&pm_config);
}

void configure_thread_device_mode()
{
    CHIP_ERROR err = chip::DeviceLayer::ConnectivityMgr().SetThreadDeviceType(
        chip::DeviceLayer::ConnectivityManager::kThreadDeviceType_MinimalEndDevice);
    if (err == CHIP_NO_ERROR) {
        ESP_LOGI(TAG, "Thread device mode set to Minimal End Device");
    } else {
        ESP_LOGW(TAG, "Failed to switch Thread device mode: %s", chip::ErrorStr(err));
    }
}



void indicator_task(void *)
{
    uint32_t tick_ms = 0;
    IndicatorState last = IndicatorState::Boot;
    while (true) {
        const uint32_t since_boot_ms =
            (xTaskGetTickCount() - s_boot_tick) * portTICK_PERIOD_MS;
        const IndicatorState state = compute_indicator_state(since_boot_ms);
        if (state != last) {
            ESP_LOGI(TAG, "Indicator: %s", indicator_state_str(state));
            last = state;
        }
        render_indicator(state, tick_ms);
        vTaskDelay(pdMS_TO_TICKS(kIndicatorPeriodMs));
        tick_ms += kIndicatorPeriodMs;
    }
}

void telemetry_task(void *)
{
    uint32_t since_heartbeat_ms = 0;
    while (true) {
        float temperature_c = 0.0f;
        esp_err_t err = temperature_sensor_get_celsius(s_device.temp_handle, &temperature_c);
        if (err == ESP_OK) {
            ESP_LOGI(TAG, "Updating chip temperature: %.2fC", temperature_c);
            err = update_temperature_measurement(temperature_c);
            if (err != ESP_OK) {
                ESP_LOGW(TAG, "Failed to update temperature attribute: %s", esp_err_to_name(err));
            }
        } else {
            ESP_LOGW(TAG, "Failed to read chip temperature: %s", esp_err_to_name(err));
        }
        log_adc_diagnostics();
        for (size_t i = 0; i < kAdcDiagnosticChannelCount; ++i) {
            if (!bms::logic::IsAdcVoltageEnabled(i)) {
                continue;
            }

            int raw = 0;
            int mv = -1;
            err = read_adc_voltage_mv(i, &raw, &mv);
            if (err == ESP_OK && mv >= 0) {
                ESP_LOGI(TAG, "Updating GPIO%d voltage: endpoint=%u raw=%d voltage=%dmV",
                         static_cast<int>(kAdcDiagnosticGpios[i]),
                         static_cast<unsigned>(s_device.adc_voltage_ep_ids[i]),
                         raw,
                         mv);
                err = update_adc_voltage(i, mv);
                if (err != ESP_OK) {
                    ESP_LOGW(TAG, "Failed to update GPIO%d voltage attribute: %s",
                             static_cast<int>(kAdcDiagnosticGpios[i]), esp_err_to_name(err));
                }
            } else {
                ESP_LOGW(TAG, "Failed to read GPIO%d voltage for Matter: %s",
                         static_cast<int>(kAdcDiagnosticGpios[i]), esp_err_to_name(err));
            }
        }

        since_heartbeat_ms += kTelemetryPeriodMs;
        if (since_heartbeat_ms >= kHeartbeatPeriodMs) {
            since_heartbeat_ms = 0;
            const uint32_t uptime_s =
                (xTaskGetTickCount() - s_boot_tick) * portTICK_PERIOD_MS / 1000;
            ESP_LOGI(TAG,
                     "Heartbeat: thread_attached=%d commissioned=%d window_open=%d "
                     "free_heap=%u uptime_s=%u",
                     static_cast<int>(s_indicator.thread_attached),
                     static_cast<int>(s_indicator.commissioned),
                     static_cast<int>(s_indicator.window_open),
                     static_cast<unsigned>(esp_get_free_heap_size()),
                     static_cast<unsigned>(uptime_s));
        }
        vTaskDelay(pdMS_TO_TICKS(kTelemetryPeriodMs));
    }
}

void button_task(void *)
{
    bool last_sample = read_boot_button_pressed();
    bool reported_state = last_sample;
    TickType_t last_change_tick = xTaskGetTickCount();
    TickType_t press_start_tick = last_sample ? xTaskGetTickCount() : 0;
    bool preview_commissioning_set = false;
    bool preview_factory_reset_set = false;

    s_device.button_pressed = reported_state;
    if (update_button_state(reported_state) != ESP_OK) {
        ESP_LOGW(TAG, "Failed to publish initial button state");
    }

    while (true) {
        const bool current_sample = read_boot_button_pressed();
        const TickType_t now = xTaskGetTickCount();

        if (current_sample != last_sample) {
            last_sample = current_sample;
            last_change_tick = now;
        } else if (current_sample != reported_state &&
                   (now - last_change_tick) >= pdMS_TO_TICKS(kButtonDebounceMs)) {
            reported_state = current_sample;
            s_device.button_pressed = reported_state;
            ESP_LOGI(TAG, "BOOT button %s", reported_state ? "pressed" : "released");
            esp_err_t err = update_button_state(reported_state);
            if (err != ESP_OK) {
                ESP_LOGW(TAG, "Failed to update button state: %s", esp_err_to_name(err));
            }
            if (reported_state) {
                press_start_tick = now;
                preview_commissioning_set = false;
                preview_factory_reset_set = false;
            } else {
                const uint32_t held_ms = (now - press_start_tick) * portTICK_PERIOD_MS;
                s_indicator.button_preview_commissioning = false;
                s_indicator.button_preview_factory_reset = false;
                if (held_ms >= kFactoryResetHoldMs) {
                    ESP_LOGW(TAG, "Factory reset requested (BOOT held %ums)",
                             static_cast<unsigned>(held_ms));
                    schedule_factory_reset();
                } else if (held_ms >= kCommissioningHoldMs) {
                    ESP_LOGI(TAG, "Commissioning window requested (BOOT held %ums)",
                             static_cast<unsigned>(held_ms));
                    schedule_commissioning_window();
                }
            }
        }

        if (reported_state) {
            const uint32_t held_ms = (now - press_start_tick) * portTICK_PERIOD_MS;
            if (held_ms >= kFactoryResetHoldMs && !preview_factory_reset_set) {
                preview_factory_reset_set = true;
                s_indicator.button_preview_commissioning = false;
                s_indicator.button_preview_factory_reset = true;
            } else if (held_ms >= kCommissioningHoldMs && !preview_commissioning_set) {
                preview_commissioning_set = true;
                s_indicator.button_preview_commissioning = true;
            }
        }

        vTaskDelay(pdMS_TO_TICKS(kButtonPollPeriodMs));
    }
}

esp_err_t attr_callback(attribute::callback_type_t type, uint16_t endpoint_id,
                        uint32_t cluster_id, uint32_t attribute_id,
                        esp_matter_attr_val_t *val, void *priv_data)
{
    (void)type;
    (void)endpoint_id;
    (void)cluster_id;
    (void)attribute_id;
    (void)val;
    (void)priv_data;
    return ESP_OK;
}

esp_err_t identify_callback(identification::callback_type_t type, uint16_t endpoint_id,
                            uint8_t effect_id, uint8_t effect_variant, void *priv_data)
{
    ESP_LOGI(TAG, "Identify: endpoint=%u effect=%u", endpoint_id, effect_id);
    return ESP_OK;
}

void matter_event_callback(const chip::DeviceLayer::ChipDeviceEvent *event, intptr_t arg)
{
    switch (event->Type) {
    case chip::DeviceLayer::DeviceEventType::kThreadStateChange:
        s_indicator.thread_attached = chip::DeviceLayer::ConnectivityMgr().IsThreadAttached();
        ESP_LOGI(TAG, "Thread state change: attached=%d",
                 static_cast<int>(s_indicator.thread_attached));
        break;
    case chip::DeviceLayer::DeviceEventType::kCommissioningComplete:
        ESP_LOGI(TAG, "Commissioning complete");
        update_thread_state_cache();
        log_commissioning_state("commissioning-complete");
        break;
    case chip::DeviceLayer::DeviceEventType::kCommissioningSessionStarted:
        ESP_LOGI(TAG, "Commissioning session started");
        log_commissioning_state("session-started");
        break;
    case chip::DeviceLayer::DeviceEventType::kCommissioningSessionStopped:
        ESP_LOGI(TAG, "Commissioning session stopped");
        log_commissioning_state("session-stopped");
        break;
    case chip::DeviceLayer::DeviceEventType::kCommissioningWindowOpened:
        s_indicator.window_open = true;
        ESP_LOGI(TAG, "Commissioning window opened");
        log_commissioning_state("window-opened");
        break;
    case chip::DeviceLayer::DeviceEventType::kCommissioningWindowClosed:
        s_indicator.window_open = false;
        ESP_LOGI(TAG, "Commissioning window closed");
        log_commissioning_state("window-closed");
        break;
    case chip::DeviceLayer::DeviceEventType::kFabricCommitted:
        s_indicator.commissioned = chip::Server::GetInstance().GetFabricTable().FabricCount() > 0;
        ESP_LOGI(TAG, "Fabric committed");
        log_commissioning_state("fabric-committed");
        break;
    case chip::DeviceLayer::DeviceEventType::kFabricRemoved:
        s_indicator.commissioned = chip::Server::GetInstance().GetFabricTable().FabricCount() > 0;
        ESP_LOGI(TAG, "Fabric removed");
        log_commissioning_state("fabric-removed");
        break;
    case chip::DeviceLayer::DeviceEventType::kFabricUpdated:
        ESP_LOGI(TAG, "Fabric updated");
        log_commissioning_state("fabric-updated");
        break;
    case chip::DeviceLayer::DeviceEventType::kCHIPoBLEConnectionClosed:
        ESP_LOGI(TAG, "CHIPoBLE connection closed");
        break;
    case chip::DeviceLayer::DeviceEventType::kDnssdInitialized:
        ESP_LOGI(TAG, "DNS-SD initialized");
        break;
    case chip::DeviceLayer::DeviceEventType::kInterfaceIpAddressChanged:
        ESP_LOGI(TAG, "Interface IP address changed");
        break;
    default:
        break;
    }
}

} // namespace

extern "C" void app_main(void)
{
    s_boot_tick = xTaskGetTickCount();

    ESP_LOGI(TAG, "Starting %s %s — Matter over Thread (firmware %s)",
             kBoardIdentity.vendor_name, kBoardIdentity.product_name,
             kBoardIdentity.software_version_str);
    ESP_LOGI(TAG, "Reset reason: %s", reset_reason_str(esp_reset_reason()));

    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        nvs_flash_init();
    }

    esp_openthread_platform_config_t ot_config = {};
    ot_config.radio_config.radio_mode          = RADIO_MODE_NATIVE;
    ot_config.host_config.host_connection_mode = HOST_CONNECTION_MODE_NONE;
    ot_config.port_config.storage_partition_name = "nvs";
    ot_config.port_config.netif_queue_size       = 10;
    ot_config.port_config.task_queue_size        = 10;
    set_openthread_platform_config(&ot_config);
    ESP_LOGI(TAG, "OpenThread platform config installed");

    ESP_ERROR_CHECK(init_power_management());
    ESP_ERROR_CHECK(init_internal_temperature_sensor());
    ESP_ERROR_CHECK(init_adc_diagnostics());
    ESP_ERROR_CHECK(init_led_strip());
    ESP_ERROR_CHECK(init_boot_button());
    s_device.button_pressed = read_boot_button_pressed();

    ESP_ERROR_CHECK(bms_node_core::install_device_identity(s_device_info_provider));
    ESP_ERROR_CHECK(init_test_event_trigger_delegate());

    node::config_t node_config;
    node_t *node = node::create(&node_config, attr_callback, identify_callback);
    if (!node) {
        ESP_LOGE(TAG, "Failed to create Matter node");
        return;
    }
    ESP_ERROR_CHECK(ensure_serial_number_attribute());
    ESP_ERROR_CHECK(bms_node_core::thread_diagnostics::ensure_root_identity_attributes(TAG));

    temperature_sensor::config_t temp_config;
    endpoint_t *temp_ep = temperature_sensor::create(node, &temp_config, ENDPOINT_FLAG_NONE, nullptr);
    if (!temp_ep) {
        ESP_LOGE(TAG, "Failed to create temperature_sensor endpoint");
        return;
    }

    contact_sensor::config_t button_config;
    endpoint_t *button_ep = contact_sensor::create(node, &button_config, ENDPOINT_FLAG_NONE, nullptr);
    if (!button_ep) {
        ESP_LOGE(TAG, "Failed to create contact_sensor endpoint");
        return;
    }

    endpoint_t *adc_voltage_eps[kAdcDiagnosticChannelCount] = {};
    for (size_t i = 0; i < kAdcDiagnosticChannelCount; ++i) {
        electrical_sensor::config_t voltage_config;
        voltage_config.electrical_power_measurement.feature_flags =
            cluster::electrical_power_measurement::feature::direct_current::get_id();
        voltage_config.electrical_power_measurement.delegate = &s_adc_voltage_delegates[i];

        endpoint_t *voltage_ep = electrical_sensor::create(node, &voltage_config, ENDPOINT_FLAG_NONE, nullptr);
        if (!voltage_ep) {
            ESP_LOGE(TAG, "Failed to create electrical_sensor endpoint for GPIO%d voltage",
                     static_cast<int>(kAdcDiagnosticGpios[i]));
            return;
        }

        cluster_t *voltage_cluster = cluster::get(
            voltage_ep,
            chip::app::Clusters::ElectricalPowerMeasurement::Id);
        if (!voltage_cluster) {
            ESP_LOGE(TAG, "GPIO%d voltage ElectricalPowerMeasurement cluster not found",
                     static_cast<int>(kAdcDiagnosticGpios[i]));
            return;
        }

        if (!cluster::electrical_power_measurement::attribute::create_voltage(
                voltage_cluster,
                nullable<int64_t>())) {
            ESP_LOGE(TAG, "Failed to create GPIO%d voltage attribute",
                     static_cast<int>(kAdcDiagnosticGpios[i]));
            return;
        }
        adc_voltage_eps[i] = voltage_ep;
    }

    s_device.temp_ep_id = endpoint::get_id(temp_ep);
    s_device.button_ep_id = endpoint::get_id(button_ep);
    for (size_t i = 0; i < kAdcDiagnosticChannelCount; ++i) {
        s_device.adc_voltage_ep_ids[i] = endpoint::get_id(adc_voltage_eps[i]);
    }
    ESP_LOGI(TAG, "Endpoints: temperature=%u button=%u adc_voltage_gpio0=%u gpio1=%u gpio2=%u gpio3=%u gpio4=%u gpio5=%u gpio6=%u",
             s_device.temp_ep_id,
             s_device.button_ep_id,
             s_device.adc_voltage_ep_ids[0],
             s_device.adc_voltage_ep_ids[1],
             s_device.adc_voltage_ep_ids[2],
             s_device.adc_voltage_ep_ids[3],
             s_device.adc_voltage_ep_ids[4],
             s_device.adc_voltage_ep_ids[5],
             s_device.adc_voltage_ep_ids[6]);
    const bms::logic::LogicConfig &logic_config = bms::logic::GetActiveLogicConfig();
    ESP_LOGI(TAG, "Active logic source: %s", bms::logic::GetActiveLogicSource());
    ESP_LOGI(TAG, "Active logic ADC voltage channels: GPIO0=%d GPIO1=%d GPIO2=%d GPIO3=%d GPIO4=%d GPIO5=%d GPIO6=%d",
             static_cast<int>(logic_config.adc_voltage_enabled[0]),
             static_cast<int>(logic_config.adc_voltage_enabled[1]),
             static_cast<int>(logic_config.adc_voltage_enabled[2]),
             static_cast<int>(logic_config.adc_voltage_enabled[3]),
             static_cast<int>(logic_config.adc_voltage_enabled[4]),
             static_cast<int>(logic_config.adc_voltage_enabled[5]),
             static_cast<int>(logic_config.adc_voltage_enabled[6]));

    esp_matter::start(matter_event_callback);
    configure_thread_device_mode();

    update_thread_state_cache();
    log_commissioning_state("after-start");
    log_onboarding_codes();

    xTaskCreate(indicator_task, "indicator_task", 3072, nullptr, 4, nullptr);
    xTaskCreate(telemetry_task, "telemetry_task", 4096, nullptr, 5, nullptr);
    xTaskCreate(button_task, "button_task", 4096, nullptr, 5, nullptr);
}
