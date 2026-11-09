#include "driver/gpio.h"
#include "driver/ledc.h"
#include "esp_check.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_matter.h"
#include "esp_matter_endpoint.h"
#include "esp_matter_providers.h"
#include "esp_netif.h"
#include "esp_system.h"
#include "esp_wifi.h"
#include "esp_wifi_default.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs_flash.h"

#include "bms_node_core/device_info.h"

#include <app/server/Server.h>
#include <clusters/BasicInformation/AttributeIds.h>
#include <clusters/BasicInformation/ClusterId.h>
#include <clusters/OnOff/AttributeIds.h>
#include <clusters/OnOff/ClusterId.h>
#include <platform/CHIPDeviceLayer.h>
#include <platform/ConnectivityManager.h>
#include <setup_payload/OnboardingCodesUtil.h>

#include <cmath>
#include <cstdio>

static const char *TAG = "bms_matter_node";

#ifndef ESP32SCAM_NODE_GIT_COMMIT
#define ESP32SCAM_NODE_GIT_COMMIT "unknown"
#endif

constexpr bms_node_core::BoardIdentity kBoardIdentity = {
    .vendor_id            = 0xFFF1,
    .product_id           = 0x8005,
    .vendor_name          = "BMS DOA",
    .product_name         = "ESP32-S-CAM",
    .serial_prefix        = "BMS-CAM-",
    .hw_version           = 1,
    .hw_version_str       = "v1.0",
    .software_version_str = ESP32SCAM_NODE_GIT_COMMIT,
    .provide_rotating_id  = false,
};

static bms_node_core::DeviceInfoProvider s_device_info_provider(kBoardIdentity);

using namespace esp_matter;
using namespace esp_matter::attribute;
using namespace esp_matter::endpoint;

namespace {

constexpr gpio_num_t kFlashLedGpio = GPIO_NUM_4;
constexpr gpio_num_t kStatusLedGpio = GPIO_NUM_33;

constexpr ledc_mode_t kLedSpeedMode = LEDC_LOW_SPEED_MODE;
constexpr ledc_timer_t kLedTimer = LEDC_TIMER_1;
constexpr ledc_channel_t kLedChannel = LEDC_CHANNEL_1;
constexpr ledc_timer_bit_t kLedResolution = LEDC_TIMER_8_BIT;
constexpr uint32_t kLedMaxDuty = 255;
constexpr uint8_t kLedBrightnessCap = 96;

constexpr uint32_t kIndicatorPeriodMs = 50;
constexpr uint32_t kBootIndicatorMs = 5000;

enum class IndicatorState : uint8_t {
    Boot,
    Commissioning,
    WiFiDisconnected,
    Running,
};

struct DeviceContext {
    uint16_t led_ep_id = 0;
    bool flash_on = false;
};

struct IndicatorCtx {
    volatile bool wifi_connected = false;
    volatile bool commissioned = false;
    volatile bool window_open = false;
};

DeviceContext s_device;
IndicatorCtx s_indicator;
TickType_t s_boot_tick = 0;

const char *reset_reason_str(esp_reset_reason_t reason)
{
    switch (reason) {
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

esp_err_t init_flash_led()
{
    gpio_config_t cfg = {};
    cfg.pin_bit_mask = 1ULL << kFlashLedGpio;
    cfg.mode = GPIO_MODE_OUTPUT;
    cfg.pull_up_en = GPIO_PULLUP_DISABLE;
    cfg.pull_down_en = GPIO_PULLDOWN_DISABLE;
    cfg.intr_type = GPIO_INTR_DISABLE;
    ESP_RETURN_ON_ERROR(gpio_config(&cfg), TAG, "gpio_config flash LED");
    return gpio_set_level(kFlashLedGpio, 0);
}

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

esp_err_t set_led_brightness(uint8_t brightness)
{
    const uint32_t capped = (static_cast<uint32_t>(brightness) * kLedBrightnessCap) / 255;
    const uint32_t duty = kLedMaxDuty - capped;
    ESP_RETURN_ON_ERROR(ledc_set_duty(kLedSpeedMode, kLedChannel, duty), TAG, "ledc_set_duty");
    ESP_RETURN_ON_ERROR(ledc_update_duty(kLedSpeedMode, kLedChannel), TAG, "ledc_update_duty");
    return ESP_OK;
}

esp_err_t init_status_led()
{
    ledc_timer_config_t timer_cfg = {};
    timer_cfg.speed_mode = kLedSpeedMode;
    timer_cfg.duty_resolution = kLedResolution;
    timer_cfg.timer_num = kLedTimer;
    timer_cfg.freq_hz = 5000;
    timer_cfg.clk_cfg = LEDC_AUTO_CLK;
    ESP_RETURN_ON_ERROR(ledc_timer_config(&timer_cfg), TAG, "ledc_timer_config");

    ledc_channel_config_t channel_cfg = {};
    channel_cfg.gpio_num = kStatusLedGpio;
    channel_cfg.speed_mode = kLedSpeedMode;
    channel_cfg.channel = kLedChannel;
    channel_cfg.intr_type = LEDC_INTR_DISABLE;
    channel_cfg.timer_sel = kLedTimer;
    channel_cfg.duty = kLedMaxDuty;
    channel_cfg.hpoint = 0;
    ESP_RETURN_ON_ERROR(ledc_channel_config(&channel_cfg), TAG, "ledc_channel_config");

    return set_led_brightness(0);
}

uint8_t breath_intensity(uint32_t tick_ms, uint32_t period_ms)
{
    const float phase = static_cast<float>(tick_ms % period_ms) / static_cast<float>(period_ms);
    const float value = 0.5f - 0.5f * std::cos(phase * 2.0f * static_cast<float>(M_PI));
    return static_cast<uint8_t>(value * 255.0f);
}

bool blink_on(uint32_t tick_ms, uint32_t half_period_ms)
{
    return (tick_ms / half_period_ms) % 2 == 0;
}

uint8_t double_pulse_intensity(uint32_t tick_ms)
{
    const uint32_t t = tick_ms % 1200;
    return (t < 90 || (t >= 180 && t < 270)) ? 255 : 0;
}

IndicatorState compute_indicator_state(uint32_t since_boot_ms)
{
    if (since_boot_ms < kBootIndicatorMs) {
        return IndicatorState::Boot;
    }
    if (s_indicator.window_open || !s_indicator.commissioned) {
        return IndicatorState::Commissioning;
    }
    if (!s_indicator.wifi_connected) {
        return IndicatorState::WiFiDisconnected;
    }
    return IndicatorState::Running;
}

void render_indicator(IndicatorState state, uint32_t tick_ms)
{
    switch (state) {
    case IndicatorState::Boot:
        set_led_brightness(breath_intensity(tick_ms, 1500));
        break;
    case IndicatorState::Commissioning:
        set_led_brightness(double_pulse_intensity(tick_ms));
        break;
    case IndicatorState::WiFiDisconnected:
        set_led_brightness(blink_on(tick_ms, 1000) ? 120 : 0);
        break;
    case IndicatorState::Running:
        set_led_brightness(40);
        break;
    }
}

esp_err_t ensure_serial_number_attribute()
{
    cluster_t *basic = cluster::get(static_cast<uint16_t>(0), chip::app::Clusters::BasicInformation::Id);
    if (!basic) {
        return ESP_ERR_NOT_FOUND;
    }
    attribute_t *attr = attribute::get(
        0,
        chip::app::Clusters::BasicInformation::Id,
        chip::app::Clusters::BasicInformation::Attributes::SerialNumber::Id);
    if (attr) {
        return ESP_OK;
    }
    attr = cluster::basic_information::attribute::create_serial_number(basic, nullptr, 0);
    return attr ? ESP_OK : ESP_FAIL;
}

void log_onboarding_codes()
{
    char qr_buffer[256] = {};
    chip::MutableCharSpan qr(qr_buffer);
    if (GetQRCode(qr, chip::RendezvousInformationFlag::kBLE) == CHIP_NO_ERROR) {
        ESP_LOGI(TAG, "BLE QR code: %s", qr.data());
    }

    char manual_buffer[chip::kManualSetupLongCodeCharLength + 1] = {};
    chip::MutableCharSpan manual(manual_buffer);
    if (GetManualPairingCode(manual, chip::RendezvousInformationFlag::kBLE) == CHIP_NO_ERROR) {
        ESP_LOGI(TAG, "BLE manual code: %s", manual.data());
    }
}

void update_network_state_cache()
{
    s_indicator.wifi_connected = chip::DeviceLayer::ConnectivityMgr().IsWiFiStationConnected();
    s_indicator.commissioned = chip::Server::GetInstance().GetFabricTable().FabricCount() > 0;
    s_indicator.window_open =
        chip::Server::GetInstance().GetCommissioningWindowManager().IsCommissioningWindowOpen();
}

void log_commissioning_state(const char *reason)
{
    update_network_state_cache();
    const size_t fabric_count = chip::Server::GetInstance().GetFabricTable().FabricCount();
    ESP_LOGI(TAG, "Commissioning state (%s): fabrics=%u commissioned=%d window_open=%d wifi=%d",
             reason,
             static_cast<unsigned>(fabric_count),
             static_cast<int>(s_indicator.commissioned),
             static_cast<int>(s_indicator.window_open),
             static_cast<int>(s_indicator.wifi_connected));
}

esp_err_t attr_callback(attribute::callback_type_t type,
                        uint16_t endpoint_id,
                        uint32_t cluster_id,
                        uint32_t attribute_id,
                        esp_matter_attr_val_t *val,
                        void *priv_data)
{
    if (type == attribute::PRE_UPDATE &&
        endpoint_id == s_device.led_ep_id &&
        cluster_id == chip::app::Clusters::OnOff::Id &&
        attribute_id == chip::app::Clusters::OnOff::Attributes::OnOff::Id) {
        const bool on = val->val.b;
        s_device.flash_on = on;
        gpio_set_level(kFlashLedGpio, on ? 1 : 0);
        ESP_LOGI(TAG, "Flash LED: %s", on ? "ON" : "OFF");
    }
    return ESP_OK;
}

esp_err_t identify_callback(identification::callback_type_t type,
                            uint16_t endpoint_id,
                            uint8_t effect_id,
                            uint8_t effect_variant,
                            void *priv_data)
{
    if (endpoint_id == s_device.led_ep_id) {
        gpio_set_level(kFlashLedGpio, type == identification::START ? 1 : (s_device.flash_on ? 1 : 0));
    }
    return ESP_OK;
}

void matter_event_callback(const chip::DeviceLayer::ChipDeviceEvent *event, intptr_t)
{
    switch (event->Type) {
    case chip::DeviceLayer::DeviceEventType::kWiFiConnectivityChange:
        s_indicator.wifi_connected = chip::DeviceLayer::ConnectivityMgr().IsWiFiStationConnected();
        ESP_LOGI(TAG, "Wi-Fi: connected=%d", static_cast<int>(s_indicator.wifi_connected));
        break;
    case chip::DeviceLayer::DeviceEventType::kCommissioningWindowOpened:
        s_indicator.window_open = true;
        log_commissioning_state("window-opened");
        break;
    case chip::DeviceLayer::DeviceEventType::kCommissioningWindowClosed:
        s_indicator.window_open = false;
        log_commissioning_state("window-closed");
        break;
    case chip::DeviceLayer::DeviceEventType::kFabricCommitted:
        s_indicator.commissioned = chip::Server::GetInstance().GetFabricTable().FabricCount() > 0;
        log_commissioning_state("fabric-committed");
        break;
    case chip::DeviceLayer::DeviceEventType::kFabricRemoved:
        s_indicator.commissioned = chip::Server::GetInstance().GetFabricTable().FabricCount() > 0;
        log_commissioning_state("fabric-removed");
        break;
    case chip::DeviceLayer::DeviceEventType::kDnssdInitialized:
        ESP_LOGI(TAG, "DNS-SD initialized");
        break;
    default:
        break;
    }
}

void indicator_task(void *)
{
    uint32_t tick_ms = 0;
    IndicatorState last = IndicatorState::Boot;
    while (true) {
        const uint32_t since_boot_ms = (xTaskGetTickCount() - s_boot_tick) * portTICK_PERIOD_MS;
        const IndicatorState state = compute_indicator_state(since_boot_ms);
        if (state != last) {
            static const char *kNames[] = {"boot", "commissioning", "wifi-disconnected", "running"};
            ESP_LOGI(TAG, "Indicator: %s", kNames[static_cast<uint8_t>(state)]);
            last = state;
        }
        render_indicator(state, tick_ms);
        vTaskDelay(pdMS_TO_TICKS(kIndicatorPeriodMs));
        tick_ms += kIndicatorPeriodMs;
    }
}

} // namespace

extern "C" void app_main(void)
{
    s_boot_tick = xTaskGetTickCount();

    ESP_LOGI(TAG, "Starting %s %s firmware=%s",
             kBoardIdentity.vendor_name,
             kBoardIdentity.product_name,
             kBoardIdentity.software_version_str);
    ESP_LOGI(TAG, "Reset reason: %s", reset_reason_str(esp_reset_reason()));

    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ESP_ERROR_CHECK(nvs_flash_init());
    }

    uint8_t mac[6] = {};
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    char device_id[32] = {};
    std::snprintf(device_id, sizeof(device_id), "esp32s-cam-%02X%02X%02X", mac[3], mac[4], mac[5]);
    ESP_LOGI(TAG, "Device ID: %s", device_id);

    ESP_ERROR_CHECK(init_status_led());
    ESP_ERROR_CHECK(init_flash_led());
    ESP_ERROR_CHECK(bms_node_core::install_device_identity(s_device_info_provider));
    ESP_ERROR_CHECK(configure_wifi_hostname_from_serial());

    node::config_t node_cfg;
    node_t *node = node::create(&node_cfg, attr_callback, identify_callback);
    if (!node) {
        ESP_LOGE(TAG, "Failed to create Matter node");
        return;
    }
    ESP_ERROR_CHECK(ensure_serial_number_attribute());

    on_off_light::config_t led_cfg;
    endpoint_t *led_ep = on_off_light::create(node, &led_cfg, ENDPOINT_FLAG_NONE, nullptr);
    if (!led_ep) {
        ESP_LOGE(TAG, "Failed to create on_off_light endpoint");
        return;
    }
    s_device.led_ep_id = endpoint::get_id(led_ep);
    ESP_LOGI(TAG, "on_off_light endpoint id=%u (GPIO %d)", s_device.led_ep_id, static_cast<int>(kFlashLedGpio));

    esp_matter::start(matter_event_callback);
    update_network_state_cache();
    log_commissioning_state("after-start");
    log_onboarding_codes();

    if (xTaskCreate(indicator_task, "indicator_task", 3072, nullptr, 4, nullptr) != pdPASS) {
        ESP_LOGE(TAG, "Failed to create indicator_task");
    }

}
