#include "driver/gpio.h"
#include "driver/temperature_sensor.h"
#include "esp_check.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_matter.h"
#include "esp_matter_endpoint.h"
#include "esp_matter_providers.h"
#include "esp_matter_test_event_trigger.h"
#include "esp_netif.h"
#include "esp_system.h"
#include "esp_wifi_default.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "led_strip.h"
#include "nvs_flash.h"
#include "sps30.h"

#include "bms_node_core/device_info.h"

#include <app/server/Server.h>
#include <app/TestEventTriggerDelegate.h>
#include <app/clusters/boolean-state-server/boolean-state-cluster.h>
#include <app/server-cluster/ServerClusterInterfaceRegistry.h>
#include <credentials/DeviceAttestationCredsProvider.h>
#include <credentials/examples/DeviceAttestationCredsExample.h>
#include <data_model_provider/esp_matter_data_model_provider.h>
#include <platform/CHIPDeviceLayer.h>
#include <platform/DeviceInstanceInfoProvider.h>
#include <clusters/BooleanState/AttributeIds.h>
#include <clusters/BooleanState/ClusterId.h>
#include <clusters/BasicInformation/AttributeIds.h>
#include <clusters/BasicInformation/ClusterId.h>
#include <clusters/Pm1ConcentrationMeasurement/AttributeIds.h>
#include <clusters/Pm1ConcentrationMeasurement/ClusterId.h>
#include <clusters/Pm10ConcentrationMeasurement/AttributeIds.h>
#include <clusters/Pm10ConcentrationMeasurement/ClusterId.h>
#include <clusters/Pm25ConcentrationMeasurement/AttributeIds.h>
#include <clusters/Pm25ConcentrationMeasurement/ClusterId.h>
#include <clusters/TemperatureMeasurement/AttributeIds.h>
#include <clusters/TemperatureMeasurement/ClusterId.h>
#include <platform/ConnectivityManager.h>
#include <platform/PlatformManager.h>
#include <setup_payload/OnboardingCodesUtil.h>

#include <cmath>
#include <cstring>

static const char *TAG = "bms_matter_node";
#ifndef MATTER_NODE_GIT_COMMIT
#define MATTER_NODE_GIT_COMMIT "unknown"
#endif
#ifndef MATTER_NODE_GIT_COMMIT_U32
#define MATTER_NODE_GIT_COMMIT_U32 0
#endif

constexpr bms_node_core::BoardIdentity kBoardIdentity = {
    .vendor_id            = 0xFFF1,
    .product_id           = 0x8008,
    .vendor_name          = "BMS DOA",
    .product_name         = "ESP32-C6-Pico WiFi SPS30",
    .serial_prefix        = "BMS-C6PWS-",
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
constexpr uart_port_t kSps30UartPort = UART_NUM_1;
constexpr gpio_num_t kSps30TxGpio = GPIO_NUM_16;
constexpr gpio_num_t kSps30RxGpio = GPIO_NUM_17;
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

enum class IndicatorState : uint8_t {
    Boot,
    Commissioning,
    WiFiDisconnected,
    Running,
    CommissioningPreview,
    FactoryResetPreview,
    FactoryResetActive,
};

struct DeviceContext {
    uint16_t temp_ep_id = 0;
    uint16_t button_ep_id = 0;
    uint16_t air_quality_ep_id = 0;
    temperature_sensor_handle_t temp_handle = nullptr;
    led_strip_handle_t led_strip = nullptr;
    Sps30Sensor sps30;
    bool sps30_ready = false;
    bool button_pressed = false;
    uint8_t last_led_r = 0;
    uint8_t last_led_g = 0;
    uint8_t last_led_b = 0;
    bool led_initialized = false;
};

struct IndicatorCtx {
    volatile bool wifi_connected = false;
    volatile bool commissioned = false;
    volatile bool window_open = false;
    volatile bool button_preview_commissioning = false;
    volatile bool button_preview_factory_reset = false;
    volatile bool factory_reset_active = false;
};

DeviceContext s_device;
IndicatorCtx s_indicator;
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

esp_err_t configure_wifi_hostname_from_serial()
{
    const char *hostname = s_device_info_provider.cached_serial_number();
    if (!hostname || hostname[0] == '\0') {
        return ESP_ERR_INVALID_STATE;
    }

    esp_err_t err = esp_event_loop_create_default();
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        ESP_LOGW(TAG, "Failed to create default event loop before hostname setup: %s", esp_err_to_name(err));
        return err;
    }

    err = esp_netif_init();
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "Failed to initialize esp-netif before hostname setup: %s", esp_err_to_name(err));
        return err;
    }

    esp_netif_t *sta_netif = esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");
    if (!sta_netif) {
        sta_netif = esp_netif_create_default_wifi_sta();
    }
    if (!sta_netif) {
        ESP_LOGW(TAG, "Failed to create Wi-Fi STA netif before hostname setup");
        return ESP_FAIL;
    }

    err = esp_netif_set_hostname(sta_netif, hostname);
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "Wi-Fi DHCP hostname set to serial: %s", hostname);
    } else {
        ESP_LOGW(TAG, "Failed to set Wi-Fi DHCP hostname %s: %s", hostname, esp_err_to_name(err));
    }
    return err;
}

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
    case IndicatorState::WiFiDisconnected:      return "wifi-disconnected/yellow";
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
    if (!s_indicator.wifi_connected)              return IndicatorState::WiFiDisconnected;
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
    case IndicatorState::WiFiDisconnected: {
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

esp_err_t update_float_attribute(uint16_t endpoint_id, uint32_t cluster_id, uint32_t attribute_id, float value)
{
    esp_matter_attr_val_t attr_val = esp_matter_nullable_float(nullable<float>(value));
    return update(endpoint_id, cluster_id, attribute_id, &attr_val);
}

esp_err_t update_sps30_measurements(const Sps30Reading &reading)
{
    esp_err_t err = update_float_attribute(
        s_device.air_quality_ep_id,
        chip::app::Clusters::Pm1ConcentrationMeasurement::Id,
        chip::app::Clusters::Pm1ConcentrationMeasurement::Attributes::MeasuredValue::Id,
        reading.mass_pm1_0_ug_m3);
    if (err != ESP_OK) {
        return err;
    }

    err = update_float_attribute(
        s_device.air_quality_ep_id,
        chip::app::Clusters::Pm25ConcentrationMeasurement::Id,
        chip::app::Clusters::Pm25ConcentrationMeasurement::Attributes::MeasuredValue::Id,
        reading.mass_pm2_5_ug_m3);
    if (err != ESP_OK) {
        return err;
    }

    err = update_float_attribute(
        s_device.air_quality_ep_id,
        chip::app::Clusters::Pm10ConcentrationMeasurement::Id,
        chip::app::Clusters::Pm10ConcentrationMeasurement::Attributes::MeasuredValue::Id,
        reading.mass_pm10_ug_m3);
    if (err != ESP_OK) {
        return err;
    }

    return ESP_OK;
}

esp_err_t update_button_state(bool pressed)
{
    auto *cluster = esp_matter::data_model::provider::get_instance().registry().Get(
        chip::app::ConcreteClusterPath(s_device.button_ep_id, chip::app::Clusters::BooleanState::Id));
    if (!cluster) {
        return ESP_ERR_NOT_FOUND;
    }

    auto *boolean_state = static_cast<chip::app::Clusters::BooleanStateCluster *>(cluster);
    esp_matter::lock::ScopedChipStackLock lock(portMAX_DELAY);
    const bool previous = boolean_state->GetStateValue();
    auto event_number = boolean_state->SetStateValue(pressed);
    if (previous == pressed || event_number.has_value()) {
        return ESP_OK;
    }
    return ESP_FAIL;
}

esp_err_t init_internal_temperature_sensor()
{
    temperature_sensor_config_t temp_sensor_config = TEMPERATURE_SENSOR_CONFIG_DEFAULT(20, 80);
    ESP_RETURN_ON_ERROR(temperature_sensor_install(&temp_sensor_config, &s_device.temp_handle), TAG, "Failed to install temperature sensor");
    ESP_RETURN_ON_ERROR(temperature_sensor_enable(s_device.temp_handle), TAG, "Failed to enable temperature sensor");
    return ESP_OK;
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

esp_err_t init_sps30()
{
    esp_err_t err = s_device.sps30.init(kSps30UartPort, kSps30TxGpio, kSps30RxGpio);
    s_device.sps30_ready = (err == ESP_OK);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "SPS30 unavailable on UART%d TX=GPIO%d RX=GPIO%d: %s",
                 static_cast<int>(kSps30UartPort),
                 static_cast<int>(kSps30TxGpio),
                 static_cast<int>(kSps30RxGpio),
                 esp_err_to_name(err));
        return err;
    }
    return ESP_OK;
}

void update_network_state_cache()
{
    s_indicator.wifi_connected = chip::DeviceLayer::ConnectivityMgr().IsWiFiStationConnected();
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

        if (s_device.sps30_ready) {
            Sps30Reading reading;
            err = s_device.sps30.read_measurement(reading);
            if (err == ESP_OK && reading.valid) {
                ESP_LOGI(TAG,
                         "SPS30: PM1.0=%.2f PM2.5=%.2f PM4.0=%.2f PM10=%.2f ug/m3",
                         reading.mass_pm1_0_ug_m3,
                         reading.mass_pm2_5_ug_m3,
                         reading.mass_pm4_0_ug_m3,
                         reading.mass_pm10_ug_m3);
                err = update_sps30_measurements(reading);
                if (err != ESP_OK) {
                    ESP_LOGW(TAG, "Failed to update SPS30 Matter attributes: %s", esp_err_to_name(err));
                }
            } else if (err == ESP_ERR_NOT_FOUND) {
                ESP_LOGD(TAG, "SPS30 measurement not ready yet");
            } else {
                ESP_LOGW(TAG, "Failed to read SPS30 measurement: %s", esp_err_to_name(err));
            }
        }

        since_heartbeat_ms += kTelemetryPeriodMs;
        if (since_heartbeat_ms >= kHeartbeatPeriodMs) {
            since_heartbeat_ms = 0;
            const uint32_t uptime_s =
                (xTaskGetTickCount() - s_boot_tick) * portTICK_PERIOD_MS / 1000;
            ESP_LOGI(TAG,
                     "Heartbeat: wifi_connected=%d commissioned=%d window_open=%d sps30_ready=%d "
                     "free_heap=%u uptime_s=%u",
                     static_cast<int>(s_indicator.wifi_connected),
                     static_cast<int>(s_indicator.commissioned),
                     static_cast<int>(s_indicator.window_open),
                     static_cast<int>(s_device.sps30_ready),
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
    ESP_LOGI(TAG, "BOOT button task ready, initial state=%d", static_cast<int>(reported_state));

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
    case chip::DeviceLayer::DeviceEventType::kWiFiConnectivityChange:
        s_indicator.wifi_connected = chip::DeviceLayer::ConnectivityMgr().IsWiFiStationConnected();
        ESP_LOGI(TAG, "Wi-Fi connectivity change: connected=%d",
                 static_cast<int>(s_indicator.wifi_connected));
        break;
    case chip::DeviceLayer::DeviceEventType::kCommissioningComplete:
        ESP_LOGI(TAG, "Commissioning complete");
        update_network_state_cache();
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

    ESP_LOGI(TAG, "Starting %s %s - Matter over Wi-Fi (firmware %s)",
             kBoardIdentity.vendor_name, kBoardIdentity.product_name,
             kBoardIdentity.software_version_str);
    ESP_LOGI(TAG, "Reset reason: %s", reset_reason_str(esp_reset_reason()));
    ESP_LOGI(TAG, "SPS30 UART: UART%d TX=GPIO%d RX=GPIO%d baud=115200",
             static_cast<int>(kSps30UartPort),
             static_cast<int>(kSps30TxGpio),
             static_cast<int>(kSps30RxGpio));

    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        nvs_flash_init();
    }

    ESP_ERROR_CHECK(init_internal_temperature_sensor());
    ESP_ERROR_CHECK(init_led_strip());
    ESP_ERROR_CHECK(init_boot_button());
    init_sps30();
    s_device.button_pressed = read_boot_button_pressed();

    ESP_ERROR_CHECK(bms_node_core::install_device_identity(s_device_info_provider));
    ESP_ERROR_CHECK(configure_wifi_hostname_from_serial());
    ESP_ERROR_CHECK(init_test_event_trigger_delegate());

    node::config_t node_config;
    node_t *node = node::create(&node_config, attr_callback, identify_callback);
    if (!node) {
        ESP_LOGE(TAG, "Failed to create Matter node");
        return;
    }
    ESP_ERROR_CHECK(ensure_serial_number_attribute());

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

    air_quality_sensor::config_t air_quality_config;
    endpoint_t *air_quality_ep = air_quality_sensor::create(node, &air_quality_config, ENDPOINT_FLAG_NONE, nullptr);
    if (!air_quality_ep) {
        ESP_LOGE(TAG, "Failed to create air_quality_sensor endpoint");
        return;
    }

    cluster::concentration_measurement::config_t pm_config;
    pm_config.measurement_medium = 0; // Air
    pm_config.feature_flags = cluster::concentration_measurement::feature::numeric_measurement::get_id();
    pm_config.features.numeric_measurement.measurement_unit = 4; // ug/m3

    if (!cluster::pm1_concentration_measurement::create(air_quality_ep, &pm_config, CLUSTER_FLAG_SERVER)) {
        ESP_LOGE(TAG, "Failed to create PM1 concentration cluster");
        return;
    }
    if (!cluster::pm25_concentration_measurement::create(air_quality_ep, &pm_config, CLUSTER_FLAG_SERVER)) {
        ESP_LOGE(TAG, "Failed to create PM2.5 concentration cluster");
        return;
    }
    if (!cluster::pm10_concentration_measurement::create(air_quality_ep, &pm_config, CLUSTER_FLAG_SERVER)) {
        ESP_LOGE(TAG, "Failed to create PM10 concentration cluster");
        return;
    }

    s_device.temp_ep_id = endpoint::get_id(temp_ep);
    s_device.button_ep_id = endpoint::get_id(button_ep);
    s_device.air_quality_ep_id = endpoint::get_id(air_quality_ep);
    ESP_LOGI(TAG, "Endpoints: temperature=%u button=%u air_quality=%u",
             s_device.temp_ep_id,
             s_device.button_ep_id,
             s_device.air_quality_ep_id);

    esp_matter::start(matter_event_callback);

    update_network_state_cache();
    log_commissioning_state("after-start");
    log_onboarding_codes();

    xTaskCreate(indicator_task, "indicator_task", 3072, nullptr, 4, nullptr);
    xTaskCreate(telemetry_task, "telemetry_task", 4096, nullptr, 5, nullptr);
    xTaskCreate(button_task, "button_task", 4096, nullptr, 5, nullptr);
}
