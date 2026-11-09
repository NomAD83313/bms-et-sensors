#include "driver/gpio.h"
#include "driver/i2c.h"
#include "driver/ledc.h"
#include "driver/spi_master.h"
#include "esp_check.h"
#include "esp_event.h"
#include "esp_heap_caps.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_commands.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_vendor.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_matter.h"
#include "esp_matter_core.h"
#include "esp_matter_endpoint.h"
#include "esp_matter_providers.h"
#include "esp_matter_test_event_trigger.h"
#include "esp_netif.h"
#include "esp_system.h"
#include "esp_wifi.h"
#include "esp_wifi_default.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "nvs_flash.h"

#include "bms_node_core/device_info.h"

#include <app/TestEventTriggerDelegate.h>
#include <app/server/Server.h>
#include <credentials/DeviceAttestationCredsProvider.h>
#include <credentials/examples/DeviceAttestationCredsExample.h>
#include <platform/CHIPDeviceLayer.h>
#include <platform/CommissionableDataProvider.h>
#include <platform/DeviceInstanceInfoProvider.h>
#include <platform/ESP32/NetworkCommissioningDriver.h>
#include <clusters/BooleanState/AttributeIds.h>
#include <clusters/BooleanState/ClusterId.h>
#include <clusters/BasicInformation/AttributeIds.h>
#include <clusters/BasicInformation/ClusterId.h>
#include <clusters/PressureMeasurement/AttributeIds.h>
#include <clusters/PressureMeasurement/ClusterId.h>
#include <clusters/PowerSource/AttributeIds.h>
#include <clusters/PowerSource/ClusterId.h>
#include <clusters/PowerSource/Enums.h>
#include <clusters/RelativeHumidityMeasurement/AttributeIds.h>
#include <clusters/RelativeHumidityMeasurement/ClusterId.h>
#include <clusters/TemperatureMeasurement/AttributeIds.h>
#include <clusters/TemperatureMeasurement/ClusterId.h>
#include <platform/ConnectivityManager.h>
#include <platform/PlatformManager.h>
#include <protocols/secure_channel/PASESession.h>
#include <setup_payload/OnboardingCodesUtil.h>

#include <algorithm>
#include <cmath>
#include <cstdarg>
#include <cstdio>
#include <cstring>

extern "C" {
#include "qrcodegen.h"
}

static const char *TAG = "bms_matter_node";
#ifndef MATTER_NODE_GIT_COMMIT
#define MATTER_NODE_GIT_COMMIT "unknown"
#endif
#ifndef MATTER_NODE_GIT_COMMIT_U32
#define MATTER_NODE_GIT_COMMIT_U32 0
#endif

constexpr bms_node_core::BoardIdentity kBoardIdentity = {
    .vendor_id            = 0xFFF1,
    .product_id           = 0x8006,
    .vendor_name          = "BMS DOA",
    .product_name         = "M5StickC",
    .serial_prefix        = "BMS-M5C-",
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

constexpr gpio_num_t kStatusLedGpio = GPIO_NUM_10;
constexpr gpio_num_t kBootButtonGpio = GPIO_NUM_37;
constexpr gpio_num_t kPowerHoldGpio = GPIO_NUM_4;
constexpr gpio_num_t kLcdMosiGpio = GPIO_NUM_15;
constexpr gpio_num_t kLcdClkGpio = GPIO_NUM_13;
constexpr gpio_num_t kLcdDcGpio = GPIO_NUM_23;
constexpr gpio_num_t kLcdRstGpio = GPIO_NUM_18;
constexpr gpio_num_t kLcdCsGpio = GPIO_NUM_5;
constexpr gpio_num_t kEnvI2cSdaGpio = GPIO_NUM_32;
constexpr gpio_num_t kEnvI2cSclGpio = GPIO_NUM_33;
constexpr gpio_num_t kEnvHatI2cSdaGpio = GPIO_NUM_0;
constexpr gpio_num_t kEnvHatI2cSclGpio = GPIO_NUM_26;
constexpr i2c_port_t kEnvI2cPort = I2C_NUM_0;
constexpr gpio_num_t kAxpI2cSdaGpio = GPIO_NUM_21;
constexpr gpio_num_t kAxpI2cSclGpio = GPIO_NUM_22;
constexpr i2c_port_t kAxpI2cPort = I2C_NUM_1;
constexpr uint8_t kAxp192Addr = 0x34;
constexpr uint8_t kAxp192OutputControlReg = 0x12;
constexpr uint8_t kAxp192Ldo23VoltageReg = 0x28;
constexpr uint8_t kAxp192ChargeStatusReg = 0x01;
constexpr uint8_t kAxp192BatteryVoltageHighReg = 0x78;
constexpr uint8_t kAxp192Ldo2EnableBit = 1 << 2;
constexpr uint8_t kAxp192Ldo3EnableBit = 1 << 3;
constexpr uint8_t kAxp192Dcdc1EnableBit = 1 << 0;
constexpr uint8_t kAxp192ChargingBit = 1 << 6;
constexpr uint32_t kEnvI2cClockHz = 100 * 1000;
constexpr uint8_t kSht30Addr = 0x44;
constexpr uint8_t kSht40Addr = 0x44;
constexpr uint8_t kQmp6988AddrUnit = 0x70;
constexpr uint8_t kQmp6988AddrHat = 0x56;
constexpr uint8_t kQmp6988ChipId = 0x5c;
constexpr uint8_t kBmp280Addr = 0x76;
constexpr uint8_t kBmp280ChipId = 0x58;
constexpr spi_host_device_t kLcdSpiHost = SPI2_HOST;
constexpr int kDisplayWidth = 160;
constexpr int kDisplayHeight = 80;
constexpr int kLcdPanelWidth = 80;
constexpr int kLcdPanelHeight = 160;
constexpr int kDisplayFlushRows = 16;
constexpr int kDisplayPixelClockHz = 10 * 1000 * 1000;
constexpr int kDisplayGapX = 26;
constexpr int kDisplayGapY = 1;
constexpr uint32_t kTelemetryPeriodMs = 30000;
constexpr uint32_t kHeartbeatPeriodMs = 300000;
constexpr uint32_t kDisplayPeriodMs = 5000;
constexpr uint32_t kDisplayAwakeMs = 3000;
constexpr uint32_t kButtonPollPeriodMs = 50;
constexpr uint32_t kButtonDebounceMs = 30;
constexpr uint8_t kEnvMaxConsecutiveReadFailures = 3;
constexpr uint8_t kEnvReprobePeriodCycles = 1;
constexpr uint32_t kCommissioningHoldMs = 7000;
constexpr uint32_t kFactoryResetHoldMs = 15000;
constexpr uint32_t kCommissioningWindowSeconds = 180;
constexpr uint32_t kBootIndicatorMs = 5000;
constexpr uint32_t kIndicatorPeriodMs = 50;
constexpr uint32_t kAirRebootDelayMs = 500;
constexpr uint64_t kBmsAirRebootEventTrigger = 0xFFF10001ull;
constexpr uint8_t kLedBrightnessCap = 96;
constexpr int kBatteryFullMv = 4180;
constexpr int kBatteryChargingRiseMv = 6;
constexpr int kBatteryChargingHoldMv = -2;
constexpr ledc_mode_t kStatusLedSpeedMode = LEDC_LOW_SPEED_MODE;
constexpr ledc_timer_t kStatusLedTimer = LEDC_TIMER_0;
constexpr ledc_channel_t kStatusLedChannel = LEDC_CHANNEL_0;
constexpr ledc_timer_bit_t kStatusLedResolution = LEDC_TIMER_8_BIT;
constexpr uint32_t kStatusLedMaxDuty = 255;
constexpr uint32_t kUniquePasscodeBase = 10000000;
constexpr uint32_t kUniquePasscodeSpan = 80000000;
constexpr uint32_t kSpake2pIterationCount = 1000;
constexpr uint8_t kSpake2pSalt[] = {
    0x42, 0x4d, 0x53, 0x20, 0x4d, 0x35, 0x43, 0x32,
    0x20, 0x53, 0x50, 0x41, 0x4b, 0x45, 0x32, 0x50,
};

enum class IndicatorState : uint8_t {
    Boot,
    Commissioning,
    WiFiDisconnected,
    Running,
    CommissioningPreview,
    FactoryResetPreview,
    FactoryResetActive,
};

enum class BatteryChargeState : uint8_t {
    Unknown,
    NotCharging,
    Charging,
    Full,
};

struct DeviceContext {
    uint16_t temp_ep_id = 0;
    uint16_t button_ep_id = 0;
    uint16_t humidity_ep_id = 0;
    uint16_t pressure_ep_id = 0;
    uint16_t power_ep_id = 0;
    esp_lcd_panel_handle_t lcd_panel = nullptr;
    uint16_t *framebuffer = nullptr;
    uint16_t *lcd_flush_buffer = nullptr;
    SemaphoreHandle_t lcd_flush_done = nullptr;
    bool button_pressed = false;
    bool display_awake = true;
    TickType_t display_awake_until = 0;
    uint8_t screen_index = 0;
    int battery_raw = -1;
    int battery_mv = -1;
    int battery_delta_mv = 0;
    uint8_t battery_charge_rise_count = 0;
    BatteryChargeState battery_charge_state = BatteryChargeState::Unknown;
    bool env_i2c_ready = false;
    bool sht30_ready = false;
    bool sht40_ready = false;
    bool qmp6988_ready = false;
    bool bmp280_ready = false;
    bool temp_null_published = false;
    bool humidity_null_published = false;
    bool pressure_null_published = false;
    uint8_t temp_humidity_fail_count = 0;
    uint8_t qmp6988_fail_count = 0;
    uint8_t bmp280_fail_count = 0;
    uint8_t env_reprobe_count = 0;
    uint8_t qmp6988_addr = 0;
    const char *env_model_name = "ENV: WAIT";
    const char *env_bus_name = "";
    const char *temp_humidity_name = "TH";
    const char *pressure_name = "P";
    float last_temperature_c = NAN;
    float last_humidity_percent = NAN;
    float last_pressure_pa = NAN;
};

struct Qmp6988Calibration {
    int32_t a0 = 0;
    int32_t b00 = 0;
    int32_t a1 = 0;
    int32_t a2 = 0;
    int64_t bt1 = 0;
    int64_t bt2 = 0;
    int64_t bp1 = 0;
    int64_t b11 = 0;
    int64_t bp2 = 0;
    int64_t b12 = 0;
    int64_t b21 = 0;
    int64_t bp3 = 0;
};

struct Bmp280Calibration {
    uint16_t dig_t1 = 0;
    int16_t dig_t2 = 0;
    int16_t dig_t3 = 0;
    uint16_t dig_p1 = 0;
    int16_t dig_p2 = 0;
    int16_t dig_p3 = 0;
    int16_t dig_p4 = 0;
    int16_t dig_p5 = 0;
    int16_t dig_p6 = 0;
    int16_t dig_p7 = 0;
    int16_t dig_p8 = 0;
    int16_t dig_p9 = 0;
    int32_t t_fine = 0;
};

struct EnvI2cBusConfig {
    const char *name;
    gpio_num_t sda;
    gpio_num_t scl;
};

struct OnboardingInfo {
    char qr_code[256] = {};
    char manual_code[chip::kManualSetupLongCodeCharLength + 1] = {};
    uint32_t passcode = 0;
    uint16_t discriminator = 0;
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
OnboardingInfo s_onboarding;
Qmp6988Calibration s_qmp_calibration;
Bmp280Calibration s_bmp280_calibration;
TaskHandle_t s_telemetry_task_handle = nullptr;
TickType_t s_boot_tick = 0;
uint8_t s_test_event_enable_key[chip::TestEventTriggerDelegate::kEnableKeyLength] = {
    0x00, 0x11, 0x22, 0x33,
    0x44, 0x55, 0x66, 0x77,
    0x88, 0x99, 0xaa, 0xbb,
    0xcc, 0xdd, 0xee, 0xff,
};

bool lcd_color_transfer_done(esp_lcd_panel_io_handle_t, esp_lcd_panel_io_event_data_t *, void *user_ctx)
{
    auto *semaphore = static_cast<SemaphoreHandle_t *>(user_ctx);
    if (!semaphore || !*semaphore) {
        return false;
    }
    BaseType_t high_priority_task_woken = pdFALSE;
    xSemaphoreGiveFromISR(*semaphore, &high_priority_task_woken);
    return high_priority_task_woken == pdTRUE;
}

uint16_t lcd_swap_rgb565_bytes(uint16_t color)
{
    return static_cast<uint16_t>((color << 8) | (color >> 8));
}

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

bool is_invalid_setup_passcode(uint32_t passcode)
{
    switch (passcode) {
    case 0:
    case 11111111:
    case 22222222:
    case 33333333:
    case 44444444:
    case 55555555:
    case 66666666:
    case 77777777:
    case 88888888:
    case 99999999:
    case 12345678:
    case 87654321:
        return true;
    default:
        return false;
    }
}

uint32_t derive_setup_passcode_from_mac()
{
    uint8_t mac[6] = {};
    if (esp_base_mac_addr_get(mac) != ESP_OK) {
        return 20345744;
    }
    const uint32_t suffix = (static_cast<uint32_t>(mac[3]) << 16) |
                            (static_cast<uint32_t>(mac[4]) << 8) |
                            static_cast<uint32_t>(mac[5]);
    uint32_t passcode = kUniquePasscodeBase + (suffix % kUniquePasscodeSpan);
    while (passcode > 99999998 || is_invalid_setup_passcode(passcode)) {
        passcode++;
        if (passcode > 99999998) {
            passcode = kUniquePasscodeBase;
        }
    }
    return passcode;
}

uint16_t derive_setup_discriminator_from_mac()
{
    uint8_t mac[6] = {};
    if (esp_base_mac_addr_get(mac) != ESP_OK) {
        return 3344;
    }
    const uint16_t suffix = (static_cast<uint16_t>(mac[4]) << 8) | mac[5];
    return static_cast<uint16_t>(0x0800 | (suffix & 0x07ff));
}

class MacDerivedCommissionableDataProvider : public chip::DeviceLayer::CommissionableDataProvider
{
public:
    void Init()
    {
        m_passcode = derive_setup_passcode_from_mac();
        m_discriminator = derive_setup_discriminator_from_mac();
        ESP_LOGI(TAG, "MAC-derived Matter setup: passcode=%lu discriminator=%u (0x%03x)",
                 static_cast<unsigned long>(m_passcode),
                 static_cast<unsigned>(m_discriminator),
                 static_cast<unsigned>(m_discriminator));
    }

    CHIP_ERROR GetSetupDiscriminator(uint16_t &setupDiscriminator) override
    {
        setupDiscriminator = m_discriminator;
        return CHIP_NO_ERROR;
    }

    CHIP_ERROR SetSetupDiscriminator(uint16_t setupDiscriminator) override
    {
        m_discriminator = setupDiscriminator;
        return CHIP_NO_ERROR;
    }

    CHIP_ERROR GetSpake2pIterationCount(uint32_t &iterationCount) override
    {
        iterationCount = kSpake2pIterationCount;
        return CHIP_NO_ERROR;
    }

    CHIP_ERROR GetSpake2pSalt(chip::MutableByteSpan &saltBuf) override
    {
        VerifyOrReturnError(saltBuf.size() >= sizeof(kSpake2pSalt), CHIP_ERROR_BUFFER_TOO_SMALL);
        std::memcpy(saltBuf.data(), kSpake2pSalt, sizeof(kSpake2pSalt));
        saltBuf.reduce_size(sizeof(kSpake2pSalt));
        return CHIP_NO_ERROR;
    }

    CHIP_ERROR GetSpake2pVerifier(chip::MutableByteSpan &verifierBuf, size_t &outVerifierLen) override
    {
        chip::Crypto::Spake2pVerifier verifier;
        chip::Crypto::Spake2pVerifierSerialized serialized = {};
        chip::MutableByteSpan serialized_span(serialized);

        ReturnErrorOnFailure(chip::PASESession::GeneratePASEVerifier(
            verifier,
            kSpake2pIterationCount,
            chip::ByteSpan(kSpake2pSalt),
            false,
            m_passcode));
        ReturnErrorOnFailure(verifier.Serialize(serialized_span));

        outVerifierLen = serialized_span.size();
        VerifyOrReturnError(verifierBuf.size() >= outVerifierLen, CHIP_ERROR_BUFFER_TOO_SMALL);
        std::memcpy(verifierBuf.data(), serialized, outVerifierLen);
        verifierBuf.reduce_size(outVerifierLen);
        return CHIP_NO_ERROR;
    }

    CHIP_ERROR GetSetupPasscode(uint32_t &setupPasscode) override
    {
        setupPasscode = m_passcode;
        return CHIP_NO_ERROR;
    }

    CHIP_ERROR SetSetupPasscode(uint32_t setupPasscode) override
    {
        VerifyOrReturnError(!is_invalid_setup_passcode(setupPasscode), CHIP_ERROR_INVALID_ARGUMENT);
        m_passcode = setupPasscode;
        return CHIP_NO_ERROR;
    }

private:
    uint32_t m_passcode = 20345744;
    uint16_t m_discriminator = 3344;
};

MacDerivedCommissionableDataProvider s_commissionable_data_provider;

// Matter spec: temperature in 0.01 °C units (int16_t)
int16_t to_matter_temp(float celsius) { return static_cast<int16_t>(celsius * 100.0f); }

// Matter spec: relative humidity in 0.01 %RH units (uint16_t)
uint16_t to_matter_humidity(float percent)
{
    const float clipped = std::max(0.0f, std::min(100.0f, percent));
    return static_cast<uint16_t>(std::lround(clipped * 100.0f));
}

// Matter PressureMeasurement MeasuredValue is in kPa.
int16_t to_matter_pressure(float pascal)
{
    return static_cast<int16_t>(std::lround(pascal / 1000.0f));
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
    case IndicatorState::Boot:                  return "boot/red-breathe";
    case IndicatorState::Commissioning:         return "commissioning/red-double-pulse";
    case IndicatorState::WiFiDisconnected:      return "wifi-disconnected/red-slow-blink";
    case IndicatorState::Running:               return "running/red-steady-dim";
    case IndicatorState::CommissioningPreview:  return "button-preview-commissioning/red-fast-blink";
    case IndicatorState::FactoryResetPreview:   return "button-preview-factory-reset/red-warning";
    case IndicatorState::FactoryResetActive:    return "factory-reset/red-rapid";
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

void clear_staged_wifi_network()
{
    namespace net_comm = chip::DeviceLayer::NetworkCommissioning;

    net_comm::ESPWiFiDriver &wifi_driver = net_comm::ESPWiFiDriver::GetInstance();
    net_comm::NetworkIterator *networks = wifi_driver.GetNetworks();
    if (networks == nullptr) {
        ESP_LOGW(TAG, "Unable to inspect staged Wi-Fi network before commissioning");
        return;
    }

    net_comm::Network network;
    while (networks->Next(network)) {
        if (network.networkIDLen == 0) {
            continue;
        }

        char debug_text_buffer[128] = {};
        chip::MutableCharSpan debug_text(debug_text_buffer);
        uint8_t network_index = 0;
        const net_comm::Status status = wifi_driver.RemoveNetwork(
            chip::ByteSpan(network.networkID, network.networkIDLen),
            debug_text,
            network_index);

        if (status == net_comm::Status::kSuccess) {
            ESP_LOGW(TAG, "Cleared staged Wi-Fi network before commissioning");
        } else {
            ESP_LOGW(TAG, "Failed to clear staged Wi-Fi network before commissioning: %u",
                     static_cast<unsigned>(status));
        }
    }

    networks->Release();
}

void quiesce_uncommissioned_wifi()
{
    bool should_disconnect = false;

    {
        esp_matter::lock::ScopedChipStackLock lock(portMAX_DELAY);
        const size_t fabric_count = chip::Server::GetInstance().GetFabricTable().FabricCount();
        if (fabric_count > 0) {
            return;
        }

        ESP_LOGW(TAG, "No fabric is present; clearing stale Wi-Fi STA provisioning before commissioning");
        clear_staged_wifi_network();
        chip::DeviceLayer::ConnectivityMgr().ClearWiFiStationProvision();

        const CHIP_ERROR mode_err = chip::DeviceLayer::ConnectivityMgr().SetWiFiStationMode(
            chip::DeviceLayer::ConnectivityManager::kWiFiStationMode_ApplicationControlled);
        if (mode_err != CHIP_NO_ERROR) {
            ESP_LOGW(TAG, "Failed to hold Wi-Fi station in application-controlled mode: %s", chip::ErrorStr(mode_err));
        }
        should_disconnect = true;
    }

    if (should_disconnect) {
        const esp_err_t disconnect_ret = esp_wifi_disconnect();
        if (disconnect_ret != ESP_OK && disconnect_ret != ESP_ERR_WIFI_NOT_INIT && disconnect_ret != ESP_ERR_WIFI_NOT_STARTED) {
            ESP_LOGW(TAG, "Wi-Fi disconnect before commissioning failed: %s", esp_err_to_name(disconnect_ret));
        }
    }
}

void hold_wifi_station_for_commissioning()
{
    if (chip::DeviceLayer::ConnectivityMgr().IsWiFiStationConnected()) {
        return;
    }

    ESP_LOGW(TAG, "Clearing stale Wi-Fi provisioning and holding station idle before commissioning window");
    clear_staged_wifi_network();
    chip::DeviceLayer::ConnectivityMgr().ClearWiFiStationProvision();

    const CHIP_ERROR mode_err = chip::DeviceLayer::ConnectivityMgr().SetWiFiStationMode(
        chip::DeviceLayer::ConnectivityManager::kWiFiStationMode_ApplicationControlled);
    if (mode_err != CHIP_NO_ERROR) {
        ESP_LOGW(TAG, "Failed to hold Wi-Fi station in application-controlled mode: %s", chip::ErrorStr(mode_err));
    }

    const esp_err_t disconnect_ret = esp_wifi_disconnect();
    if (disconnect_ret != ESP_OK && disconnect_ret != ESP_ERR_WIFI_NOT_INIT && disconnect_ret != ESP_ERR_WIFI_NOT_STARTED) {
        ESP_LOGW(TAG, "Wi-Fi disconnect before commissioning failed: %s", esp_err_to_name(disconnect_ret));
    }
}

constexpr uint16_t kColorBg = 0x0841;
constexpr uint16_t kColorPanel = 0x18e3;
constexpr uint16_t kColorText = 0xffff;
constexpr uint16_t kColorMuted = 0x9cf3;
constexpr uint16_t kColorGood = 0x07e0;
constexpr uint16_t kColorWarn = 0xffe0;
constexpr uint16_t kColorBad = 0xf800;
constexpr uint16_t kColorAccent = 0x06df;
constexpr uint16_t kColorWhite = 0xffff;
constexpr uint16_t kColorBlack = 0x0000;

void fb_fill(uint16_t color)
{
    if (!s_device.framebuffer) {
        return;
    }
    for (int i = 0; i < kDisplayWidth * kDisplayHeight; ++i) {
        s_device.framebuffer[i] = color;
    }
}

void fb_rect(int x, int y, int w, int h, uint16_t color)
{
    if (!s_device.framebuffer) {
        return;
    }
    const int x0 = std::max(0, x);
    const int y0 = std::max(0, y);
    const int x1 = std::min(kDisplayWidth, x + w);
    const int y1 = std::min(kDisplayHeight, y + h);
    for (int yy = y0; yy < y1; ++yy) {
        for (int xx = x0; xx < x1; ++xx) {
            s_device.framebuffer[yy * kDisplayWidth + xx] = color;
        }
    }
}

void fb_rect_outline(int x, int y, int w, int h, uint16_t color)
{
    fb_rect(x, y, w, 1, color);
    fb_rect(x, y + h - 1, w, 1, color);
    fb_rect(x, y, 1, h, color);
    fb_rect(x + w - 1, y, 1, h, color);
}

const uint8_t *glyph_rows(char c)
{
    switch (c) {
    case 'A': { static const uint8_t r[] = {0x0e,0x11,0x11,0x1f,0x11,0x11,0x11}; return r; }
    case 'B': { static const uint8_t r[] = {0x1e,0x11,0x11,0x1e,0x11,0x11,0x1e}; return r; }
    case 'C': { static const uint8_t r[] = {0x0f,0x10,0x10,0x10,0x10,0x10,0x0f}; return r; }
    case 'D': { static const uint8_t r[] = {0x1e,0x11,0x11,0x11,0x11,0x11,0x1e}; return r; }
    case 'E': { static const uint8_t r[] = {0x1f,0x10,0x10,0x1e,0x10,0x10,0x1f}; return r; }
    case 'F': { static const uint8_t r[] = {0x1f,0x10,0x10,0x1e,0x10,0x10,0x10}; return r; }
    case 'G': { static const uint8_t r[] = {0x0f,0x10,0x10,0x13,0x11,0x11,0x0f}; return r; }
    case 'H': { static const uint8_t r[] = {0x11,0x11,0x11,0x1f,0x11,0x11,0x11}; return r; }
    case 'I': { static const uint8_t r[] = {0x1f,0x04,0x04,0x04,0x04,0x04,0x1f}; return r; }
    case 'J': { static const uint8_t r[] = {0x01,0x01,0x01,0x01,0x11,0x11,0x0e}; return r; }
    case 'K': { static const uint8_t r[] = {0x11,0x12,0x14,0x18,0x14,0x12,0x11}; return r; }
    case 'L': { static const uint8_t r[] = {0x10,0x10,0x10,0x10,0x10,0x10,0x1f}; return r; }
    case 'M': { static const uint8_t r[] = {0x11,0x1b,0x15,0x15,0x11,0x11,0x11}; return r; }
    case 'N': { static const uint8_t r[] = {0x11,0x19,0x15,0x13,0x11,0x11,0x11}; return r; }
    case 'O': { static const uint8_t r[] = {0x0e,0x11,0x11,0x11,0x11,0x11,0x0e}; return r; }
    case 'P': { static const uint8_t r[] = {0x1e,0x11,0x11,0x1e,0x10,0x10,0x10}; return r; }
    case 'Q': { static const uint8_t r[] = {0x0e,0x11,0x11,0x11,0x15,0x12,0x0d}; return r; }
    case 'R': { static const uint8_t r[] = {0x1e,0x11,0x11,0x1e,0x14,0x12,0x11}; return r; }
    case 'S': { static const uint8_t r[] = {0x0f,0x10,0x10,0x0e,0x01,0x01,0x1e}; return r; }
    case 'T': { static const uint8_t r[] = {0x1f,0x04,0x04,0x04,0x04,0x04,0x04}; return r; }
    case 'U': { static const uint8_t r[] = {0x11,0x11,0x11,0x11,0x11,0x11,0x0e}; return r; }
    case 'V': { static const uint8_t r[] = {0x11,0x11,0x11,0x11,0x11,0x0a,0x04}; return r; }
    case 'W': { static const uint8_t r[] = {0x11,0x11,0x11,0x15,0x15,0x15,0x0a}; return r; }
    case 'X': { static const uint8_t r[] = {0x11,0x11,0x0a,0x04,0x0a,0x11,0x11}; return r; }
    case 'Y': { static const uint8_t r[] = {0x11,0x11,0x0a,0x04,0x04,0x04,0x04}; return r; }
    case 'Z': { static const uint8_t r[] = {0x1f,0x01,0x02,0x04,0x08,0x10,0x1f}; return r; }
    case '0': { static const uint8_t r[] = {0x0e,0x11,0x13,0x15,0x19,0x11,0x0e}; return r; }
    case '1': { static const uint8_t r[] = {0x04,0x0c,0x04,0x04,0x04,0x04,0x0e}; return r; }
    case '2': { static const uint8_t r[] = {0x0e,0x11,0x01,0x02,0x04,0x08,0x1f}; return r; }
    case '3': { static const uint8_t r[] = {0x1e,0x01,0x01,0x0e,0x01,0x01,0x1e}; return r; }
    case '4': { static const uint8_t r[] = {0x02,0x06,0x0a,0x12,0x1f,0x02,0x02}; return r; }
    case '5': { static const uint8_t r[] = {0x1f,0x10,0x10,0x1e,0x01,0x01,0x1e}; return r; }
    case '6': { static const uint8_t r[] = {0x0e,0x10,0x10,0x1e,0x11,0x11,0x0e}; return r; }
    case '7': { static const uint8_t r[] = {0x1f,0x01,0x02,0x04,0x08,0x08,0x08}; return r; }
    case '8': { static const uint8_t r[] = {0x0e,0x11,0x11,0x0e,0x11,0x11,0x0e}; return r; }
    case '9': { static const uint8_t r[] = {0x0e,0x11,0x11,0x0f,0x01,0x01,0x0e}; return r; }
    case ':': { static const uint8_t r[] = {0x00,0x04,0x04,0x00,0x04,0x04,0x00}; return r; }
    case '.': { static const uint8_t r[] = {0x00,0x00,0x00,0x00,0x00,0x0c,0x0c}; return r; }
    case '-': { static const uint8_t r[] = {0x00,0x00,0x00,0x1f,0x00,0x00,0x00}; return r; }
    case '/': { static const uint8_t r[] = {0x01,0x01,0x02,0x04,0x08,0x10,0x10}; return r; }
    case '%': { static const uint8_t r[] = {0x19,0x19,0x02,0x04,0x08,0x13,0x13}; return r; }
    case '<': { static const uint8_t r[] = {0x02,0x04,0x08,0x10,0x08,0x04,0x02}; return r; }
    case '>': { static const uint8_t r[] = {0x08,0x04,0x02,0x01,0x02,0x04,0x08}; return r; }
    case ' ': { static const uint8_t r[] = {0x00,0x00,0x00,0x00,0x00,0x00,0x00}; return r; }
    default:  { static const uint8_t r[] = {0x1f,0x01,0x02,0x04,0x04,0x00,0x04}; return r; }
    }
}

void fb_char(int x, int y, char ch, uint16_t color, int scale)
{
    if (ch >= 'a' && ch <= 'z') {
        ch = static_cast<char>(ch - 'a' + 'A');
    }
    const uint8_t *rows = glyph_rows(ch);
    for (int row = 0; row < 7; ++row) {
        for (int col = 0; col < 5; ++col) {
            if (rows[row] & (1 << (4 - col))) {
                fb_rect(x + col * scale, y + row * scale, scale, scale, color);
            }
        }
    }
}

void fb_text(int x, int y, const char *text, uint16_t color, int scale = 1)
{
    int cursor = x;
    for (const char *p = text; p && *p; ++p) {
        fb_char(cursor, y, *p, color, scale);
        cursor += 6 * scale;
    }
}

void fb_textf(int x, int y, uint16_t color, int scale, const char *fmt, ...)
{
    char buf[96] = {};
    va_list args;
    va_start(args, fmt);
    std::vsnprintf(buf, sizeof(buf), fmt, args);
    va_end(args);
    fb_text(x, y, buf, color, scale);
}

void fb_header(const char *title, const char *page)
{
    fb_rect(0, 0, kDisplayWidth, 18, kColorPanel);
    fb_text(6, 5, title, kColorText, 1);
    fb_text(kDisplayWidth - 32, 5, page, kColorMuted, 1);
}

uint8_t display_screen_count()
{
    return (s_indicator.commissioned && !s_indicator.window_open) ? 3 : 4;
}

void page_label(char *buf, size_t len, uint8_t page_index)
{
    std::snprintf(buf, len, "%u/%u",
                  static_cast<unsigned>(page_index + 1),
                  static_cast<unsigned>(display_screen_count()));
}

int battery_percent_from_mv(int mv)
{
    if (mv <= 3300) {
        return 0;
    }
    if (mv >= 4200) {
        return 100;
    }
    return (mv - 3300) * 100 / 900;
}

uint8_t matter_battery_percent_remaining(int mv)
{
    return static_cast<uint8_t>(std::clamp(battery_percent_from_mv(mv) * 2, 0, 200));
}

chip::app::Clusters::PowerSource::BatChargeLevelEnum matter_battery_charge_level(int mv)
{
    const int percent = battery_percent_from_mv(mv);
    if (percent <= 10) {
        return chip::app::Clusters::PowerSource::BatChargeLevelEnum::kCritical;
    }
    if (percent <= 25) {
        return chip::app::Clusters::PowerSource::BatChargeLevelEnum::kWarning;
    }
    return chip::app::Clusters::PowerSource::BatChargeLevelEnum::kOk;
}

chip::app::Clusters::PowerSource::BatChargeStateEnum matter_battery_charge_state(BatteryChargeState state)
{
    switch (state) {
    case BatteryChargeState::Charging:
        return chip::app::Clusters::PowerSource::BatChargeStateEnum::kIsCharging;
    case BatteryChargeState::Full:
        return chip::app::Clusters::PowerSource::BatChargeStateEnum::kIsAtFullCharge;
    case BatteryChargeState::NotCharging:
        return chip::app::Clusters::PowerSource::BatChargeStateEnum::kIsNotCharging;
    case BatteryChargeState::Unknown:
    default:
        return chip::app::Clusters::PowerSource::BatChargeStateEnum::kUnknown;
    }
}

const char *battery_charge_state_text(BatteryChargeState state)
{
    switch (state) {
    case BatteryChargeState::Charging:
        return "CHG";
    case BatteryChargeState::Full:
        return "FULL";
    case BatteryChargeState::NotCharging:
        return "DIS";
    case BatteryChargeState::Unknown:
    default:
        return "WAIT";
    }
}

uint16_t battery_charge_state_color(BatteryChargeState state, uint16_t battery_color)
{
    switch (state) {
    case BatteryChargeState::Charging:
        return kColorAccent;
    case BatteryChargeState::Full:
        return kColorGood;
    case BatteryChargeState::NotCharging:
        return kColorWarn;
    case BatteryChargeState::Unknown:
    default:
        return battery_color;
    }
}

void fb_battery_icon(int x, int y, int percent, uint16_t color)
{
    fb_rect_outline(x, y, 34, 16, color);
    fb_rect(x + 34, y + 5, 3, 6, color);
    const int fill_w = std::max(0, std::min(30, percent * 30 / 100));
    fb_rect(x + 2, y + 2, fill_w, 12, color);
    if (s_device.battery_charge_state == BatteryChargeState::Charging) {
        fb_rect(x + 16, y + 3, 4, 4, kColorAccent);
        fb_rect(x + 12, y + 7, 8, 4, kColorAccent);
        fb_rect(x + 12, y + 11, 4, 3, kColorAccent);
    } else if (s_device.battery_charge_state == BatteryChargeState::NotCharging) {
        fb_rect(x + 12, y + 3, 8, 4, kColorWarn);
        fb_rect(x + 16, y + 7, 4, 4, kColorWarn);
        fb_rect(x + 12, y + 11, 8, 3, kColorWarn);
    }
}

void get_ip_text(char *buf, size_t len)
{
    std::snprintf(buf, len, "0.0.0.0");
    esp_netif_t *sta_netif = esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");
    if (!sta_netif) {
        return;
    }
    esp_netif_ip_info_t ip_info = {};
    if (esp_netif_get_ip_info(sta_netif, &ip_info) == ESP_OK) {
        esp_ip4addr_ntoa(&ip_info.ip, buf, len);
    }
}

int get_wifi_rssi()
{
    wifi_ap_record_t ap = {};
    if (esp_wifi_sta_get_ap_info(&ap) == ESP_OK) {
        return ap.rssi;
    }
    return 0;
}

void refresh_onboarding_codes()
{
    chip::DeviceLayer::GetCommissionableDataProvider()->GetSetupPasscode(s_onboarding.passcode);
    chip::DeviceLayer::GetCommissionableDataProvider()->GetSetupDiscriminator(s_onboarding.discriminator);

    chip::MutableCharSpan qr_code(s_onboarding.qr_code);
    if (GetQRCode(qr_code, chip::RendezvousInformationFlag::kBLE) != CHIP_NO_ERROR) {
        std::snprintf(s_onboarding.qr_code, sizeof(s_onboarding.qr_code), "QR ERROR");
    }

    chip::MutableCharSpan manual_code(s_onboarding.manual_code);
    if (GetManualPairingCode(manual_code, chip::RendezvousInformationFlag::kBLE) != CHIP_NO_ERROR) {
        std::snprintf(s_onboarding.manual_code, sizeof(s_onboarding.manual_code), "CODE ERROR");
    }
}

void fb_qr(int x, int y, int max_size, const char *payload)
{
    uint8_t qr[qrcodegen_BUFFER_LEN_FOR_VERSION(10)] = {};
    uint8_t temp[qrcodegen_BUFFER_LEN_FOR_VERSION(10)] = {};
    const bool ok = qrcodegen_encodeText(
        payload,
        temp,
        qr,
        qrcodegen_Ecc_MEDIUM,
        qrcodegen_VERSION_MIN,
        10,
        qrcodegen_Mask_AUTO,
        true);
    if (!ok) {
        fb_text(x, y, "QR FAIL", kColorBad, 2);
        return;
    }

    const int size = qrcodegen_getSize(qr);
    const int scale = std::max(1, max_size / (size + 2));
    const int quiet = scale;
    const int pixel_size = (size * scale) + quiet * 2;
    fb_rect(x, y, pixel_size, pixel_size, kColorWhite);
    for (int yy = 0; yy < size; ++yy) {
        for (int xx = 0; xx < size; ++xx) {
            if (qrcodegen_getModule(qr, xx, yy)) {
                fb_rect(x + quiet + xx * scale, y + quiet + yy * scale, scale, scale, kColorBlack);
            }
        }
    }
}

void render_status_screen()
{
    char ip[20] = {};
    char page[8] = {};
    get_ip_text(ip, sizeof(ip));
    page_label(page, sizeof(page), 0);
    const int percent = battery_percent_from_mv(s_device.battery_mv);
    const uint16_t battery_color = percent > 25 ? kColorGood : (percent > 10 ? kColorWarn : kColorBad);
    const uint16_t charge_color = battery_charge_state_color(s_device.battery_charge_state, battery_color);
    const char *charge_text = battery_charge_state_text(s_device.battery_charge_state);

    fb_fill(kColorBg);
    fb_header("M5 MATTER", page);
    fb_text(6, 22, s_indicator.commissioned ? "FABRIC PAIRED" : "FABRIC OPEN", s_indicator.commissioned ? kColorGood : kColorWarn, 1);
    fb_text(6, 36, s_indicator.wifi_connected ? "WIFI LINK UP" : "WIFI DOWN", s_indicator.wifi_connected ? kColorGood : kColorBad, 1);
    fb_textf(6, 50, kColorText, 1, "IP %s", ip);
    fb_textf(6, 64, kColorMuted, 1, "RSSI %d", get_wifi_rssi());
    fb_battery_icon(118, 24, percent, charge_color);
    fb_textf(118, 46, charge_color, 1, "%s", charge_text);
    fb_textf(118, 62, kColorMuted, 1, "%d%%", percent);
}

void render_pairing_screen()
{
    char page[8] = {};
    page_label(page, sizeof(page), 1);
    if (s_onboarding.qr_code[0] == '\0') {
        refresh_onboarding_codes();
    }
    fb_fill(kColorWhite);
    fb_qr(2, 20, 58, s_onboarding.qr_code);
    fb_rect(74, 0, kDisplayWidth - 74, kDisplayHeight, kColorBg);
    fb_header("PAIR", page);
    fb_text(80, 24, "SCAN", kColorAccent, 1);
    fb_text(80, 38, "MANUAL", kColorMuted, 1);
    fb_text(80, 52, s_onboarding.manual_code, kColorText, 1);
    fb_textf(80, 66, kColorMuted, 1, "D %u", static_cast<unsigned>(s_onboarding.discriminator));
}

void render_env_screen(uint8_t page_index)
{
    char page[8] = {};
    page_label(page, sizeof(page), page_index);
    fb_fill(kColorBg);
    fb_header(s_device.env_model_name, page);
    fb_textf(6, 20, kColorMuted, 1, "BUS %s", s_device.env_bus_name[0] ? s_device.env_bus_name : "SCAN");
    fb_textf(6, 32, (s_device.sht30_ready || s_device.sht40_ready) ? kColorGood : kColorWarn, 1,
             "%s %s",
             s_device.temp_humidity_name,
             (s_device.sht30_ready || s_device.sht40_ready) ? "READY" : (s_device.env_i2c_ready ? "LOST" : "WAIT"));
    fb_textf(86, 32, (s_device.qmp6988_ready || s_device.bmp280_ready) ? kColorGood : kColorWarn, 1,
             "%s %s",
             s_device.pressure_name,
             (s_device.qmp6988_ready || s_device.bmp280_ready) ? "READY" : (s_device.env_i2c_ready ? "LOST" : "WAIT"));

    if (std::isfinite(s_device.last_temperature_c)) {
        fb_textf(6, 46, kColorText, 1, "T %.1f C", static_cast<double>(s_device.last_temperature_c));
    } else {
        fb_text(6, 46, "T WAIT", kColorMuted, 1);
    }

    if (std::isfinite(s_device.last_humidity_percent)) {
        fb_textf(6, 58, kColorText, 1, "H %.1f %%", static_cast<double>(s_device.last_humidity_percent));
    } else {
        fb_text(6, 58, "H WAIT", kColorMuted, 1);
    }

    if (std::isfinite(s_device.last_pressure_pa)) {
        fb_textf(6, 70, kColorText, 1, "P %.1f HPA", static_cast<double>(s_device.last_pressure_pa / 100.0f));
    } else {
        fb_text(6, 70, "P WAIT", kColorMuted, 1);
    }
}

void render_device_screen()
{
    uint8_t mac[6] = {};
    char page[8] = {};
    const uint8_t page_index = s_indicator.commissioned ? 2 : 3;
    esp_base_mac_addr_get(mac);
    page_label(page, sizeof(page), page_index);
    fb_fill(kColorBg);
    fb_header("DEVICE", page);
    fb_text(6, 22, kBoardIdentity.product_name, kColorAccent, 1);
    fb_text(6, 36, s_device_info_provider.cached_serial_number(), kColorText, 1);
    fb_textf(6, 50, kColorMuted, 1, "MAC %02X%02X%02X%02X%02X%02X",
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    fb_textf(6, 64, kColorMuted, 1, "UP %lu S",
             static_cast<unsigned long>((xTaskGetTickCount() - s_boot_tick) * portTICK_PERIOD_MS / 1000));
}

void render_current_screen()
{
    const uint8_t screen_count = display_screen_count();
    s_device.screen_index %= screen_count;
    if (s_indicator.commissioned && !s_indicator.window_open) {
        switch (s_device.screen_index) {
        case 0: render_status_screen(); break;
        case 1: render_env_screen(1); break;
        default: render_device_screen(); break;
        }
    } else {
        switch (s_device.screen_index) {
        case 0: render_status_screen(); break;
        case 1: render_pairing_screen(); break;
        case 2: render_env_screen(2); break;
        default: render_device_screen(); break;
        }
    }
    if (s_device.lcd_panel && s_device.framebuffer && s_device.lcd_flush_buffer) {
        for (int panel_y = 0; panel_y < kLcdPanelHeight; panel_y += kDisplayFlushRows) {
            const int rows = std::min(kDisplayFlushRows, kLcdPanelHeight - panel_y);
            for (int row = 0; row < rows; ++row) {
                const int physical_y = panel_y + row;
                for (int physical_x = 0; physical_x < kLcdPanelWidth; ++physical_x) {
                    const int logical_x = physical_y;
                    const int logical_y = kDisplayHeight - 1 - physical_x;
                    const uint16_t color = s_device.framebuffer[logical_y * kDisplayWidth + logical_x];
                    s_device.lcd_flush_buffer[row * kLcdPanelWidth + physical_x] = lcd_swap_rgb565_bytes(color);
                }
            }
            if (s_device.lcd_flush_done) {
                xSemaphoreTake(s_device.lcd_flush_done, 0);
            }
            const esp_err_t draw_ret = esp_lcd_panel_draw_bitmap(s_device.lcd_panel,
                                                                 0,
                                                                 panel_y,
                                                                 kLcdPanelWidth,
                                                                 panel_y + rows,
                                                                 s_device.lcd_flush_buffer);
            if (draw_ret != ESP_OK) {
                ESP_LOGW(TAG, "LCD flush failed at physical row %d: %s", panel_y, esp_err_to_name(draw_ret));
                return;
            }
            if (s_device.lcd_flush_done) {
                xSemaphoreTake(s_device.lcd_flush_done, portMAX_DELAY);
            }
        }
    }
}

esp_err_t init_power_hold()
{
    gpio_config_t hold_config = {};
    hold_config.pin_bit_mask = 1ULL << kPowerHoldGpio;
    hold_config.mode = GPIO_MODE_OUTPUT;
    hold_config.pull_up_en = GPIO_PULLUP_DISABLE;
    hold_config.pull_down_en = GPIO_PULLDOWN_DISABLE;
    hold_config.intr_type = GPIO_INTR_DISABLE;
    ESP_RETURN_ON_ERROR(gpio_config(&hold_config), TAG, "Failed to configure power hold GPIO");
    gpio_set_level(kPowerHoldGpio, 1);
    return ESP_OK;
}

esp_err_t axp192_write_byte(uint8_t reg, uint8_t value)
{
    uint8_t data[] = {reg, value};
    return i2c_master_write_to_device(kAxpI2cPort, kAxp192Addr, data, sizeof(data), pdMS_TO_TICKS(100));
}

esp_err_t axp192_read_byte(uint8_t reg, uint8_t *value)
{
    return i2c_master_write_read_device(kAxpI2cPort, kAxp192Addr, &reg, 1, value, 1, pdMS_TO_TICKS(100));
}

esp_err_t set_axp192_bits(uint8_t reg, uint8_t mask, bool enabled)
{
    uint8_t value = 0;
    ESP_RETURN_ON_ERROR(axp192_read_byte(reg, &value), TAG, "Failed to read AXP192 register");
    value = enabled ? static_cast<uint8_t>(value | mask) : static_cast<uint8_t>(value & ~mask);
    return axp192_write_byte(reg, value);
}

esp_err_t set_lcd_backlight(bool enabled)
{
    return set_axp192_bits(kAxp192OutputControlReg, kAxp192Ldo2EnableBit, enabled);
}

esp_err_t send_lcd_command(esp_lcd_panel_io_handle_t io, uint8_t command, const uint8_t *data, size_t data_len)
{
    return esp_lcd_panel_io_tx_param(io, command, data, data_len);
}

esp_err_t init_st7735s_m5stickc_panel(esp_lcd_panel_io_handle_t io)
{
    ESP_RETURN_ON_ERROR(send_lcd_command(io, LCD_CMD_SWRESET, nullptr, 0), TAG, "Failed to reset ST7735S");
    vTaskDelay(pdMS_TO_TICKS(150));
    ESP_RETURN_ON_ERROR(send_lcd_command(io, LCD_CMD_SLPOUT, nullptr, 0), TAG, "Failed to wake ST7735S");
    vTaskDelay(pdMS_TO_TICKS(150));

    const uint8_t frctrl[] = {0x01, 0x2c, 0x2d};
    const uint8_t frmctr3[] = {0x01, 0x2c, 0x2d, 0x01, 0x2c, 0x2d};
    const uint8_t pwctr1[] = {0xa2, 0x02, 0x84};
    const uint8_t pwctr3[] = {0x0a, 0x00};
    const uint8_t pwctr4[] = {0x8a, 0x2a};
    const uint8_t pwctr5[] = {0x8a, 0xee};
    const uint8_t gmctrp1[] = {0x02, 0x1c, 0x07, 0x12, 0x37, 0x32, 0x29, 0x2d,
                               0x29, 0x25, 0x2b, 0x39, 0x00, 0x01, 0x03, 0x10};
    const uint8_t gmctrn1[] = {0x03, 0x1d, 0x07, 0x06, 0x2e, 0x2c, 0x29, 0x2d,
                               0x2e, 0x2e, 0x37, 0x3f, 0x00, 0x00, 0x02, 0x10};

    ESP_RETURN_ON_ERROR(send_lcd_command(io, 0xb1, frctrl, sizeof(frctrl)), TAG, "Failed to set ST7735S FRMCTR1");
    ESP_RETURN_ON_ERROR(send_lcd_command(io, 0xb2, frctrl, sizeof(frctrl)), TAG, "Failed to set ST7735S FRMCTR2");
    ESP_RETURN_ON_ERROR(send_lcd_command(io, 0xb3, frmctr3, sizeof(frmctr3)), TAG, "Failed to set ST7735S FRMCTR3");
    ESP_RETURN_ON_ERROR(send_lcd_command(io, 0xb4, (uint8_t[]){0x07}, 1), TAG, "Failed to set ST7735S INVCTR");
    ESP_RETURN_ON_ERROR(send_lcd_command(io, 0xc0, pwctr1, sizeof(pwctr1)), TAG, "Failed to set ST7735S PWCTR1");
    ESP_RETURN_ON_ERROR(send_lcd_command(io, 0xc1, (uint8_t[]){0xc5}, 1), TAG, "Failed to set ST7735S PWCTR2");
    ESP_RETURN_ON_ERROR(send_lcd_command(io, 0xc2, pwctr3, sizeof(pwctr3)), TAG, "Failed to set ST7735S PWCTR3");
    ESP_RETURN_ON_ERROR(send_lcd_command(io, 0xc3, pwctr4, sizeof(pwctr4)), TAG, "Failed to set ST7735S PWCTR4");
    ESP_RETURN_ON_ERROR(send_lcd_command(io, 0xc4, pwctr5, sizeof(pwctr5)), TAG, "Failed to set ST7735S PWCTR5");
    ESP_RETURN_ON_ERROR(send_lcd_command(io, 0xc5, (uint8_t[]){0x0e}, 1), TAG, "Failed to set ST7735S VMCTR1");
    ESP_RETURN_ON_ERROR(send_lcd_command(io, LCD_CMD_MADCTL, (uint8_t[]){LCD_CMD_BGR_BIT}, 1), TAG, "Failed to set ST7735S MADCTL");
    ESP_RETURN_ON_ERROR(send_lcd_command(io, LCD_CMD_COLMOD, (uint8_t[]){0x05}, 1), TAG, "Failed to set ST7735S COLMOD");
    vTaskDelay(pdMS_TO_TICKS(10));
    ESP_RETURN_ON_ERROR(send_lcd_command(io, LCD_CMD_CASET, (uint8_t[]){0x00, 0x02, 0x00, 0x81}, 4), TAG, "Failed to set ST7735S CASET");
    ESP_RETURN_ON_ERROR(send_lcd_command(io, LCD_CMD_RASET, (uint8_t[]){0x00, 0x01, 0x00, 0xa0}, 4), TAG, "Failed to set ST7735S RASET");
    ESP_RETURN_ON_ERROR(send_lcd_command(io, 0xe0, gmctrp1, sizeof(gmctrp1)), TAG, "Failed to set ST7735S GMCTRP1");
    ESP_RETURN_ON_ERROR(send_lcd_command(io, 0xe1, gmctrn1, sizeof(gmctrn1)), TAG, "Failed to set ST7735S GMCTRN1");
    ESP_RETURN_ON_ERROR(send_lcd_command(io, LCD_CMD_INVON, nullptr, 0), TAG, "Failed to invert ST7735S");
    ESP_RETURN_ON_ERROR(send_lcd_command(io, LCD_CMD_NORON, nullptr, 0), TAG, "Failed to set ST7735S normal mode");
    vTaskDelay(pdMS_TO_TICKS(10));
    ESP_RETURN_ON_ERROR(send_lcd_command(io, LCD_CMD_DISPON, nullptr, 0), TAG, "Failed to enable ST7735S");
    vTaskDelay(pdMS_TO_TICKS(100));
    return ESP_OK;
}

esp_err_t init_axp192()
{
    i2c_config_t config = {};
    config.mode = I2C_MODE_MASTER;
    config.sda_io_num = kAxpI2cSdaGpio;
    config.scl_io_num = kAxpI2cSclGpio;
    config.sda_pullup_en = GPIO_PULLUP_ENABLE;
    config.scl_pullup_en = GPIO_PULLUP_ENABLE;
    config.master.clk_speed = 100 * 1000;
    ESP_RETURN_ON_ERROR(i2c_param_config(kAxpI2cPort, &config), TAG, "Failed to configure AXP192 I2C");
    ESP_RETURN_ON_ERROR(i2c_driver_install(kAxpI2cPort, I2C_MODE_MASTER, 0, 0, 0), TAG, "Failed to install AXP192 I2C driver");

    ESP_RETURN_ON_ERROR(axp192_write_byte(kAxp192Ldo23VoltageReg, 0xcc), TAG, "Failed to configure AXP192 LDO2/LDO3");
    ESP_RETURN_ON_ERROR(
        set_axp192_bits(kAxp192OutputControlReg,
                        kAxp192Dcdc1EnableBit | kAxp192Ldo2EnableBit | kAxp192Ldo3EnableBit,
                        true),
        TAG,
        "Failed to enable AXP192 board rails");
    return ESP_OK;
}

esp_err_t init_lcd()
{
    s_device.framebuffer = static_cast<uint16_t *>(heap_caps_malloc(
        kDisplayWidth * kDisplayHeight * sizeof(uint16_t),
        MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT));
    if (!s_device.framebuffer) {
        ESP_LOGW(TAG, "Internal framebuffer allocation failed, display disabled");
        return ESP_ERR_NO_MEM;
    }

    s_device.lcd_flush_buffer = static_cast<uint16_t *>(heap_caps_malloc(
        kLcdPanelWidth * kDisplayFlushRows * sizeof(uint16_t),
        MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL));
    if (!s_device.lcd_flush_buffer) {
        ESP_LOGW(TAG, "DMA LCD flush buffer allocation failed, display disabled");
        return ESP_ERR_NO_MEM;
    }

    s_device.lcd_flush_done = xSemaphoreCreateBinary();
    if (!s_device.lcd_flush_done) {
        ESP_LOGW(TAG, "LCD flush semaphore allocation failed, display disabled");
        return ESP_ERR_NO_MEM;
    }

    set_lcd_backlight(false);

    spi_bus_config_t bus_config = {};
    bus_config.sclk_io_num = kLcdClkGpio;
    bus_config.mosi_io_num = kLcdMosiGpio;
    bus_config.miso_io_num = -1;
    bus_config.quadwp_io_num = -1;
    bus_config.quadhd_io_num = -1;
    bus_config.max_transfer_sz = kLcdPanelWidth * kDisplayFlushRows * sizeof(uint16_t);
    ESP_RETURN_ON_ERROR(spi_bus_initialize(kLcdSpiHost, &bus_config, SPI_DMA_CH_AUTO), TAG, "Failed to init LCD SPI bus");

    esp_lcd_panel_io_handle_t io_handle = nullptr;
    esp_lcd_panel_io_spi_config_t io_config = {};
    io_config.dc_gpio_num = kLcdDcGpio;
    io_config.cs_gpio_num = kLcdCsGpio;
    io_config.pclk_hz = kDisplayPixelClockHz;
    io_config.spi_mode = 0;
    io_config.trans_queue_depth = 1;
    io_config.lcd_cmd_bits = 8;
    io_config.lcd_param_bits = 8;
    io_config.on_color_trans_done = lcd_color_transfer_done;
    io_config.user_ctx = &s_device.lcd_flush_done;
    ESP_RETURN_ON_ERROR(
        esp_lcd_new_panel_io_spi(static_cast<esp_lcd_spi_bus_handle_t>(kLcdSpiHost), &io_config, &io_handle),
        TAG,
        "Failed to create LCD panel IO");

    esp_lcd_panel_dev_config_t panel_config = {};
    panel_config.reset_gpio_num = kLcdRstGpio;
    panel_config.rgb_ele_order = LCD_RGB_ELEMENT_ORDER_BGR;
    panel_config.bits_per_pixel = 16;
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_st7789(io_handle, &panel_config, &s_device.lcd_panel),
                        TAG, "Failed to create SPI LCD panel");

    ESP_RETURN_ON_ERROR(esp_lcd_panel_reset(s_device.lcd_panel), TAG, "Failed to reset LCD");
    ESP_RETURN_ON_ERROR(init_st7735s_m5stickc_panel(io_handle), TAG, "Failed to init ST7735S LCD");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_invert_color(s_device.lcd_panel, true), TAG, "Failed to invert LCD");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_set_gap(s_device.lcd_panel, kDisplayGapX, kDisplayGapY), TAG, "Failed to set LCD gap");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_disp_on_off(s_device.lcd_panel, true), TAG, "Failed to enable LCD");
    set_lcd_backlight(true);
    s_device.display_awake = true;
    s_device.display_awake_until = xTaskGetTickCount() + pdMS_TO_TICKS(kDisplayAwakeMs);
    fb_fill(kColorBg);
    render_current_screen();
    return ESP_OK;
}

esp_err_t init_battery_monitor()
{
    return ESP_OK;
}

void read_battery()
{
    uint8_t raw[2] = {};
    const uint8_t reg = kAxp192BatteryVoltageHighReg;
    if (i2c_master_write_read_device(kAxpI2cPort, kAxp192Addr, &reg, 1, raw, sizeof(raw), pdMS_TO_TICKS(100)) != ESP_OK) {
        return;
    }
    s_device.battery_raw = (static_cast<int>(raw[0]) << 4) | (raw[1] & 0x0f);
    const int new_battery_mv = (s_device.battery_raw * 11) / 10;
    const int previous_mv = s_device.battery_mv;
    const BatteryChargeState previous_state = s_device.battery_charge_state;
    s_device.battery_delta_mv = previous_mv >= 0 ? new_battery_mv - previous_mv : 0;

    uint8_t status = 0;
    if (axp192_read_byte(kAxp192ChargeStatusReg, &status) == ESP_OK && (status & kAxp192ChargingBit)) {
        s_device.battery_charge_state = BatteryChargeState::Charging;
    } else if (new_battery_mv >= kBatteryFullMv) {
        s_device.battery_charge_state = BatteryChargeState::Full;
    } else {
        s_device.battery_charge_state = BatteryChargeState::NotCharging;
    }

    s_device.battery_mv = new_battery_mv;
    if (previous_state != s_device.battery_charge_state) {
        ESP_LOGI(TAG, "Battery state: %s %+dmV %dmV",
                 battery_charge_state_text(s_device.battery_charge_state),
                 s_device.battery_delta_mv,
                 s_device.battery_mv);
    }
}

void enable_wifi_power_save()
{
    const esp_err_t ret = esp_wifi_set_ps(WIFI_PS_MIN_MODEM);
    if (ret != ESP_OK) {
        ESP_LOGW(TAG, "Wi-Fi modem power save request failed: %s", esp_err_to_name(ret));
        return;
    }
    ESP_LOGI(TAG, "Wi-Fi modem power save enabled");
}

esp_err_t set_led_brightness(uint8_t brightness)
{
    // M5StickC uses an active-low internal red LED.
    const uint32_t capped = (static_cast<uint32_t>(brightness) * kLedBrightnessCap) / 255;
    const uint32_t duty = kStatusLedMaxDuty - capped;
    ESP_RETURN_ON_ERROR(ledc_set_duty(kStatusLedSpeedMode, kStatusLedChannel, duty),
                        TAG, "Failed to set status LED duty");
    ESP_RETURN_ON_ERROR(ledc_update_duty(kStatusLedSpeedMode, kStatusLedChannel),
                        TAG, "Failed to update status LED duty");
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

void set_display_awake(bool awake)
{
    if (s_device.display_awake == awake) {
        return;
    }
    s_device.display_awake = awake;
    if (s_device.lcd_panel) {
        esp_err_t ret = esp_lcd_panel_disp_on_off(s_device.lcd_panel, awake);
        if (ret != ESP_OK) {
            ESP_LOGW(TAG, "Failed to %s LCD panel: %s", awake ? "wake" : "sleep", esp_err_to_name(ret));
        }
    }
    set_lcd_backlight(awake);
    ESP_LOGI(TAG, "Display %s", awake ? "awake" : "asleep");
}

void wake_display(uint32_t awake_ms = kDisplayAwakeMs)
{
    s_device.display_awake_until = xTaskGetTickCount() + pdMS_TO_TICKS(awake_ms);
    set_display_awake(true);
}

uint8_t double_pulse_intensity(uint32_t tick_ms)
{
    const uint32_t t = tick_ms % 1200;
    return (t < 90 || (t >= 180 && t < 270)) ? 255 : 0;
}

uint8_t warning_pulse_intensity(uint32_t tick_ms)
{
    const uint32_t t = tick_ms % 900;
    return (t < 80 || (t >= 160 && t < 240) || (t >= 320 && t < 400)) ? 255 : 0;
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
        set_led_brightness(i);
        break;
    }
    case IndicatorState::Commissioning: {
        set_led_brightness(double_pulse_intensity(tick_ms));
        break;
    }
    case IndicatorState::WiFiDisconnected: {
        const bool on = blink_on(tick_ms, 1000);
        set_led_brightness(on ? 120 : 0);
        break;
    }
    case IndicatorState::Running:
        set_led_brightness(0);
        break;
    case IndicatorState::CommissioningPreview: {
        const bool on = blink_on(tick_ms, 125);
        set_led_brightness(on ? 255 : 0);
        break;
    }
    case IndicatorState::FactoryResetPreview: {
        set_led_brightness(warning_pulse_intensity(tick_ms));
        break;
    }
    case IndicatorState::FactoryResetActive: {
        const bool on = blink_on(tick_ms, 100);
        set_led_brightness(on ? 255 : 0);
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

esp_err_t update_temperature_measurement_null()
{
    esp_matter_attr_val_t temp_val = esp_matter_nullable_int16(nullable<int16_t>());
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

esp_err_t update_humidity_measurement(float humidity_percent)
{
    esp_matter_attr_val_t humidity_val = esp_matter_nullable_uint16(
        nullable<uint16_t>(to_matter_humidity(humidity_percent)));
    return update(s_device.humidity_ep_id,
                  chip::app::Clusters::RelativeHumidityMeasurement::Id,
                  chip::app::Clusters::RelativeHumidityMeasurement::Attributes::MeasuredValue::Id,
                  &humidity_val);
}

esp_err_t update_humidity_measurement_null()
{
    esp_matter_attr_val_t humidity_val = esp_matter_nullable_uint16(nullable<uint16_t>());
    return update(s_device.humidity_ep_id,
                  chip::app::Clusters::RelativeHumidityMeasurement::Id,
                  chip::app::Clusters::RelativeHumidityMeasurement::Attributes::MeasuredValue::Id,
                  &humidity_val);
}

esp_err_t update_pressure_measurement(float pressure_pa)
{
    esp_matter_attr_val_t pressure_val = esp_matter_nullable_int16(
        nullable<int16_t>(to_matter_pressure(pressure_pa)));
    return update(s_device.pressure_ep_id,
                  chip::app::Clusters::PressureMeasurement::Id,
                  chip::app::Clusters::PressureMeasurement::Attributes::MeasuredValue::Id,
                  &pressure_val);
}

esp_err_t update_pressure_measurement_null()
{
    esp_matter_attr_val_t pressure_val = esp_matter_nullable_int16(nullable<int16_t>());
    return update(s_device.pressure_ep_id,
                  chip::app::Clusters::PressureMeasurement::Id,
                  chip::app::Clusters::PressureMeasurement::Attributes::MeasuredValue::Id,
                  &pressure_val);
}

esp_err_t update_power_source_battery()
{
    if (s_device.power_ep_id == 0 || s_device.battery_mv < 0) {
        return ESP_OK;
    }

    esp_matter_attr_val_t voltage_val =
        esp_matter_nullable_uint32(nullable<uint32_t>(static_cast<uint32_t>(s_device.battery_mv)));
    ESP_RETURN_ON_ERROR(update(s_device.power_ep_id,
                               chip::app::Clusters::PowerSource::Id,
                               chip::app::Clusters::PowerSource::Attributes::BatVoltage::Id,
                               &voltage_val),
                        TAG,
                        "Failed to publish battery voltage");

    esp_matter_attr_val_t percent_val =
        esp_matter_nullable_uint8(nullable<uint8_t>(matter_battery_percent_remaining(s_device.battery_mv)));
    ESP_RETURN_ON_ERROR(update(s_device.power_ep_id,
                               chip::app::Clusters::PowerSource::Id,
                               chip::app::Clusters::PowerSource::Attributes::BatPercentRemaining::Id,
                               &percent_val),
                        TAG,
                        "Failed to publish battery percent");

    esp_matter_attr_val_t level_val =
        esp_matter_enum8(chip::to_underlying(matter_battery_charge_level(s_device.battery_mv)));
    ESP_RETURN_ON_ERROR(update(s_device.power_ep_id,
                               chip::app::Clusters::PowerSource::Id,
                               chip::app::Clusters::PowerSource::Attributes::BatChargeLevel::Id,
                               &level_val),
                        TAG,
                        "Failed to publish battery charge level");

    esp_matter_attr_val_t state_val =
        esp_matter_enum8(chip::to_underlying(matter_battery_charge_state(s_device.battery_charge_state)));
    return update(s_device.power_ep_id,
                  chip::app::Clusters::PowerSource::Id,
                  chip::app::Clusters::PowerSource::Attributes::BatChargeState::Id,
                  &state_val);
}

uint16_t be16(const uint8_t msb, const uint8_t lsb)
{
    return (static_cast<uint16_t>(msb) << 8) | lsb;
}

uint16_t le16(const uint8_t lsb, const uint8_t msb)
{
    return (static_cast<uint16_t>(msb) << 8) | lsb;
}

int16_t le_i16(const uint8_t lsb, const uint8_t msb)
{
    return static_cast<int16_t>(le16(lsb, msb));
}

uint32_t be24(const uint8_t msb, const uint8_t mid, const uint8_t lsb)
{
    return (static_cast<uint32_t>(msb) << 16) |
           (static_cast<uint32_t>(mid) << 8) |
           lsb;
}

int32_t qmp_signed_20_from_calibration(uint8_t msb, uint8_t mid, uint8_t lsb)
{
    const uint32_t encoded = (static_cast<uint32_t>(msb) << 24) |
                             (static_cast<uint32_t>(mid) << 16) |
                             (static_cast<uint32_t>(lsb) << 8);
    return static_cast<int32_t>(encoded) >> 12;
}

esp_err_t i2c_write_bytes(uint8_t addr, const uint8_t *data, size_t len)
{
    return i2c_master_write_to_device(kEnvI2cPort, addr, data, len, pdMS_TO_TICKS(100));
}

esp_err_t i2c_write_byte(uint8_t addr, uint8_t reg, uint8_t value)
{
    const uint8_t data[] = {reg, value};
    return i2c_write_bytes(addr, data, sizeof(data));
}

esp_err_t i2c_read_reg(uint8_t addr, uint8_t reg, uint8_t *data, size_t len)
{
    return i2c_master_write_read_device(kEnvI2cPort, addr, &reg, 1, data, len, pdMS_TO_TICKS(100));
}

esp_err_t configure_env_i2c_bus(const EnvI2cBusConfig &bus)
{
    if (s_device.env_i2c_ready) {
        i2c_driver_delete(kEnvI2cPort);
        s_device.env_i2c_ready = false;
    }

    i2c_config_t config = {};
    config.mode = I2C_MODE_MASTER;
    config.sda_io_num = bus.sda;
    config.scl_io_num = bus.scl;
    config.sda_pullup_en = GPIO_PULLUP_ENABLE;
    config.scl_pullup_en = GPIO_PULLUP_ENABLE;
    config.master.clk_speed = kEnvI2cClockHz;

    ESP_RETURN_ON_ERROR(i2c_param_config(kEnvI2cPort, &config), TAG, "ENV I2C config failed");
    return i2c_driver_install(kEnvI2cPort, config.mode, 0, 0, 0);
}

uint8_t sht30_crc8(const uint8_t *data, size_t len)
{
    uint8_t crc = 0xff;
    for (size_t i = 0; i < len; ++i) {
        crc ^= data[i];
        for (int bit = 0; bit < 8; ++bit) {
            crc = (crc & 0x80) ? static_cast<uint8_t>((crc << 1) ^ 0x31) : static_cast<uint8_t>(crc << 1);
        }
    }
    return crc;
}

esp_err_t read_sht30(float *temperature_c, float *humidity_percent)
{
    const uint8_t command[] = {0x2c, 0x06};
    ESP_RETURN_ON_ERROR(i2c_write_bytes(kSht30Addr, command, sizeof(command)), TAG, "Failed to start SHT30 measurement");
    vTaskDelay(pdMS_TO_TICKS(20));

    uint8_t data[6] = {};
    ESP_RETURN_ON_ERROR(i2c_master_read_from_device(kEnvI2cPort, kSht30Addr, data, sizeof(data), pdMS_TO_TICKS(100)),
                        TAG, "Failed to read SHT30 measurement");
    if (sht30_crc8(&data[0], 2) != data[2] || sht30_crc8(&data[3], 2) != data[5]) {
        return ESP_ERR_INVALID_CRC;
    }

    const uint16_t raw_temp = be16(data[0], data[1]);
    const uint16_t raw_humidity = be16(data[3], data[4]);
    *temperature_c = -45.0f + 175.0f * static_cast<float>(raw_temp) / 65535.0f;
    *humidity_percent = 100.0f * static_cast<float>(raw_humidity) / 65535.0f;
    return ESP_OK;
}

esp_err_t read_sht40(float *temperature_c, float *humidity_percent)
{
    const uint8_t command = 0xfd;
    ESP_RETURN_ON_ERROR(i2c_write_bytes(kSht40Addr, &command, 1), TAG, "Failed to start SHT40 measurement");
    vTaskDelay(pdMS_TO_TICKS(30));

    uint8_t data[6] = {};
    ESP_RETURN_ON_ERROR(i2c_master_read_from_device(kEnvI2cPort, kSht40Addr, data, sizeof(data), pdMS_TO_TICKS(200)),
                        TAG, "Failed to read SHT40 measurement");
    if (sht30_crc8(&data[0], 2) != data[2] || sht30_crc8(&data[3], 2) != data[5]) {
        return ESP_ERR_INVALID_CRC;
    }

    const uint16_t raw_temp = be16(data[0], data[1]);
    const uint16_t raw_humidity = be16(data[3], data[4]);
    *temperature_c = -45.0f + 175.0f * static_cast<float>(raw_temp) / 65535.0f;
    *humidity_percent = -6.0f + 125.0f * static_cast<float>(raw_humidity) / 65535.0f;
    *humidity_percent = std::min(100.0f, std::max(0.0f, *humidity_percent));
    return ESP_OK;
}

int16_t qmp_compensated_temperature(const Qmp6988Calibration &cal, int32_t dt)
{
    const int64_t wk1 = static_cast<int64_t>(cal.a1) * dt;
    int64_t wk2 = (static_cast<int64_t>(cal.a2) * dt) >> 14;
    wk2 = (wk2 * dt) >> 10;
    wk2 = ((wk1 + wk2) / 32767) >> 19;
    return static_cast<int16_t>((cal.a0 + wk2) >> 4);
}

int32_t qmp_compensated_pressure(const Qmp6988Calibration &cal, int32_t dp, int16_t tx)
{
    int64_t wk1 = static_cast<int64_t>(cal.bt1) * tx;
    int64_t wk2 = (static_cast<int64_t>(cal.bp1) * dp) >> 5;
    wk1 += wk2;
    wk2 = (static_cast<int64_t>(cal.bt2) * tx) >> 1;
    wk2 = (wk2 * tx) >> 8;
    int64_t wk3 = wk2;
    wk2 = (static_cast<int64_t>(cal.b11) * tx) >> 4;
    wk2 = (wk2 * dp) >> 1;
    wk3 += wk2;
    wk2 = (static_cast<int64_t>(cal.bp2) * dp) >> 13;
    wk2 = (wk2 * dp) >> 1;
    wk3 += wk2;
    wk1 += wk3 >> 14;
    wk2 = static_cast<int64_t>(cal.b12) * tx;
    wk2 = (wk2 * tx) >> 22;
    wk2 = (wk2 * dp) >> 1;
    wk3 = wk2;
    wk2 = (static_cast<int64_t>(cal.b21) * tx) >> 6;
    wk2 = (wk2 * dp) >> 23;
    wk2 = (wk2 * dp) >> 1;
    wk3 += wk2;
    wk2 = (static_cast<int64_t>(cal.bp3) * dp) >> 12;
    wk2 = (wk2 * dp) >> 23;
    wk2 = wk2 * dp;
    wk3 += wk2;
    wk1 += wk3 >> 15;
    wk1 /= 32767L;
    wk1 >>= 11;
    wk1 += cal.b00;
    return static_cast<int32_t>(wk1);
}

esp_err_t qmp_read_calibration(uint8_t addr)
{
    uint8_t data[25] = {};
    ESP_RETURN_ON_ERROR(i2c_read_reg(addr, 0xa0, data, sizeof(data)), TAG, "Failed to read QMP6988 calibration");

    const int32_t raw_a0 = qmp_signed_20_from_calibration(data[18], data[19], static_cast<uint8_t>((data[24] & 0x0f) << 4));
    const int32_t raw_b00 = qmp_signed_20_from_calibration(data[0], data[1], data[24] & 0xf0);
    const int16_t raw_a1 = static_cast<int16_t>(be16(data[20], data[21]));
    const int16_t raw_a2 = static_cast<int16_t>(be16(data[22], data[23]));
    const int16_t raw_bt1 = static_cast<int16_t>(be16(data[2], data[3]));
    const int16_t raw_bt2 = static_cast<int16_t>(be16(data[4], data[5]));
    const int16_t raw_bp1 = static_cast<int16_t>(be16(data[6], data[7]));
    const int16_t raw_b11 = static_cast<int16_t>(be16(data[8], data[9]));
    const int16_t raw_bp2 = static_cast<int16_t>(be16(data[10], data[11]));
    const int16_t raw_b12 = static_cast<int16_t>(be16(data[12], data[13]));
    const int16_t raw_b21 = static_cast<int16_t>(be16(data[14], data[15]));
    const int16_t raw_bp3 = static_cast<int16_t>(be16(data[16], data[17]));

    s_qmp_calibration.a0 = raw_a0;
    s_qmp_calibration.b00 = raw_b00;
    s_qmp_calibration.a1 = 3608L * static_cast<int32_t>(raw_a1) - 1731677965L;
    s_qmp_calibration.a2 = 16889L * static_cast<int32_t>(raw_a2) - 87619360L;
    s_qmp_calibration.bt1 = 2982L * static_cast<int64_t>(raw_bt1) + 107370906L;
    s_qmp_calibration.bt2 = 329854L * static_cast<int64_t>(raw_bt2) + 108083093L;
    s_qmp_calibration.bp1 = 19923L * static_cast<int64_t>(raw_bp1) + 1133836764L;
    s_qmp_calibration.b11 = 2406L * static_cast<int64_t>(raw_b11) + 118215883L;
    s_qmp_calibration.bp2 = 3079L * static_cast<int64_t>(raw_bp2) - 181579595L;
    s_qmp_calibration.b12 = 6846L * static_cast<int64_t>(raw_b12) + 85590281L;
    s_qmp_calibration.b21 = 13836L * static_cast<int64_t>(raw_b21) + 79333336L;
    s_qmp_calibration.bp3 = 2915L * static_cast<int64_t>(raw_bp3) + 157155561L;
    return ESP_OK;
}

esp_err_t init_qmp6988(uint8_t addr)
{
    uint8_t chip_id = 0;
    ESP_RETURN_ON_ERROR(i2c_read_reg(addr, 0xd1, &chip_id, 1), TAG, "Failed to read QMP6988 chip id");
    if (chip_id != kQmp6988ChipId) {
        return ESP_ERR_NOT_FOUND;
    }

    ESP_RETURN_ON_ERROR(i2c_write_byte(addr, 0xe0, 0xe6), TAG, "Failed to reset QMP6988");
    vTaskDelay(pdMS_TO_TICKS(10));
    ESP_RETURN_ON_ERROR(i2c_write_byte(addr, 0xe0, 0x00), TAG, "Failed to release QMP6988 reset");
    vTaskDelay(pdMS_TO_TICKS(10));
    ESP_RETURN_ON_ERROR(qmp_read_calibration(addr), TAG, "Failed to decode QMP6988 calibration");
    ESP_RETURN_ON_ERROR(i2c_write_byte(addr, 0xf1, 0x02), TAG, "Failed to configure QMP6988 filter");
    ESP_RETURN_ON_ERROR(i2c_write_byte(addr, 0xf4, static_cast<uint8_t>((0x04 << 5) | (0x04 << 2) | 0x03)),
                        TAG, "Failed to configure QMP6988 measurement");
    vTaskDelay(pdMS_TO_TICKS(20));
    return ESP_OK;
}

esp_err_t read_qmp6988(float *pressure_pa)
{
    uint8_t data[6] = {};
    ESP_RETURN_ON_ERROR(i2c_read_reg(s_device.qmp6988_addr, 0xf7, data, sizeof(data)),
                        TAG, "Failed to read QMP6988 measurement");
    const int32_t p_raw = static_cast<int32_t>(be24(data[0], data[1], data[2]) - 8388608UL);
    const int32_t t_raw = static_cast<int32_t>(be24(data[3], data[4], data[5]) - 8388608UL);
    const int16_t t_int = qmp_compensated_temperature(s_qmp_calibration, t_raw);
    const int32_t p_int = qmp_compensated_pressure(s_qmp_calibration, p_raw, t_int);
    *pressure_pa = static_cast<float>(p_int) / 16.0f;
    return ESP_OK;
}

esp_err_t bmp280_read_calibration()
{
    uint8_t data[24] = {};
    ESP_RETURN_ON_ERROR(i2c_read_reg(kBmp280Addr, 0x88, data, sizeof(data)), TAG, "Failed to read BMP280 calibration");

    s_bmp280_calibration.dig_t1 = le16(data[0], data[1]);
    s_bmp280_calibration.dig_t2 = le_i16(data[2], data[3]);
    s_bmp280_calibration.dig_t3 = le_i16(data[4], data[5]);
    s_bmp280_calibration.dig_p1 = le16(data[6], data[7]);
    s_bmp280_calibration.dig_p2 = le_i16(data[8], data[9]);
    s_bmp280_calibration.dig_p3 = le_i16(data[10], data[11]);
    s_bmp280_calibration.dig_p4 = le_i16(data[12], data[13]);
    s_bmp280_calibration.dig_p5 = le_i16(data[14], data[15]);
    s_bmp280_calibration.dig_p6 = le_i16(data[16], data[17]);
    s_bmp280_calibration.dig_p7 = le_i16(data[18], data[19]);
    s_bmp280_calibration.dig_p8 = le_i16(data[20], data[21]);
    s_bmp280_calibration.dig_p9 = le_i16(data[22], data[23]);
    s_bmp280_calibration.t_fine = 0;
    if (s_bmp280_calibration.dig_t1 == 0 || s_bmp280_calibration.dig_p1 == 0) {
        return ESP_ERR_INVALID_RESPONSE;
    }
    return ESP_OK;
}

int32_t bmp280_compensate_temperature(int32_t adc_t)
{
    const int32_t var1 = ((((adc_t >> 3) - (static_cast<int32_t>(s_bmp280_calibration.dig_t1) << 1))) *
                          static_cast<int32_t>(s_bmp280_calibration.dig_t2)) >> 11;
    const int32_t var2 = (((((adc_t >> 4) - static_cast<int32_t>(s_bmp280_calibration.dig_t1)) *
                            ((adc_t >> 4) - static_cast<int32_t>(s_bmp280_calibration.dig_t1))) >> 12) *
                          static_cast<int32_t>(s_bmp280_calibration.dig_t3)) >> 14;
    s_bmp280_calibration.t_fine = var1 + var2;
    return (s_bmp280_calibration.t_fine * 5 + 128) >> 8;
}

esp_err_t bmp280_compensate_pressure(int32_t adc_p, float *pressure_pa)
{
    int64_t var1 = static_cast<int64_t>(s_bmp280_calibration.t_fine) - 128000;
    int64_t var2 = var1 * var1 * static_cast<int64_t>(s_bmp280_calibration.dig_p6);
    var2 += (var1 * static_cast<int64_t>(s_bmp280_calibration.dig_p5)) << 17;
    var2 += static_cast<int64_t>(s_bmp280_calibration.dig_p4) << 35;
    var1 = ((var1 * var1 * static_cast<int64_t>(s_bmp280_calibration.dig_p3)) >> 8) +
           ((var1 * static_cast<int64_t>(s_bmp280_calibration.dig_p2)) << 12);
    var1 = (((static_cast<int64_t>(1) << 47) + var1) *
            static_cast<int64_t>(s_bmp280_calibration.dig_p1)) >> 33;
    if (var1 == 0) {
        return ESP_ERR_INVALID_RESPONSE;
    }

    int64_t pressure = 1048576 - adc_p;
    pressure = (((pressure << 31) - var2) * 3125) / var1;
    var1 = (static_cast<int64_t>(s_bmp280_calibration.dig_p9) *
            (pressure >> 13) * (pressure >> 13)) >> 25;
    var2 = (static_cast<int64_t>(s_bmp280_calibration.dig_p8) * pressure) >> 19;
    pressure = ((pressure + var1 + var2) >> 8) +
               (static_cast<int64_t>(s_bmp280_calibration.dig_p7) << 4);
    *pressure_pa = static_cast<float>(pressure) / 256.0f;
    return ESP_OK;
}

esp_err_t init_bmp280()
{
    uint8_t chip_id = 0;
    ESP_RETURN_ON_ERROR(i2c_read_reg(kBmp280Addr, 0xd0, &chip_id, 1), TAG, "Failed to read BMP280 chip id");
    if (chip_id != kBmp280ChipId) {
        return ESP_ERR_NOT_FOUND;
    }

    ESP_RETURN_ON_ERROR(i2c_write_byte(kBmp280Addr, 0xe0, 0xb6), TAG, "Failed to reset BMP280");
    vTaskDelay(pdMS_TO_TICKS(10));
    ESP_RETURN_ON_ERROR(bmp280_read_calibration(), TAG, "Failed to decode BMP280 calibration");
    ESP_RETURN_ON_ERROR(i2c_write_byte(kBmp280Addr, 0xf5, 0x80), TAG, "Failed to configure BMP280 standby/filter");
    ESP_RETURN_ON_ERROR(i2c_write_byte(kBmp280Addr, 0xf4, 0x27), TAG, "Failed to configure BMP280 measurement");
    vTaskDelay(pdMS_TO_TICKS(20));
    return ESP_OK;
}

esp_err_t read_bmp280(float *pressure_pa)
{
    uint8_t data[6] = {};
    ESP_RETURN_ON_ERROR(i2c_read_reg(kBmp280Addr, 0xf7, data, sizeof(data)),
                        TAG, "Failed to read BMP280 measurement");
    const int32_t p_raw = (static_cast<int32_t>(data[0]) << 12) |
                          (static_cast<int32_t>(data[1]) << 4) |
                          (static_cast<int32_t>(data[2]) >> 4);
    const int32_t t_raw = (static_cast<int32_t>(data[3]) << 12) |
                          (static_cast<int32_t>(data[4]) << 4) |
                          (static_cast<int32_t>(data[5]) >> 4);
    bmp280_compensate_temperature(t_raw);
    return bmp280_compensate_pressure(p_raw, pressure_pa);
}

esp_err_t init_env_sensor()
{
    const EnvI2cBusConfig buses[] = {
        {"Grove", kEnvI2cSdaGpio, kEnvI2cSclGpio},
        {"Hat", kEnvHatI2cSdaGpio, kEnvHatI2cSclGpio},
    };

    for (const auto &bus : buses) {
        ESP_LOGI(TAG, "Probing M5 ENV sensor on %s I2C bus SDA=%d SCL=%d",
                 bus.name, static_cast<int>(bus.sda), static_cast<int>(bus.scl));
        esp_err_t err = configure_env_i2c_bus(bus);
        if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
            ESP_LOGW(TAG, "ENV %s I2C driver install failed: %s", bus.name, esp_err_to_name(err));
            continue;
        }

        s_device.env_i2c_ready = true;
        s_device.sht30_ready = false;
        s_device.sht40_ready = false;
        s_device.qmp6988_ready = false;
        s_device.bmp280_ready = false;
        s_device.qmp6988_addr = 0;
        s_device.temp_humidity_fail_count = 0;
        s_device.qmp6988_fail_count = 0;
        s_device.bmp280_fail_count = 0;
        s_device.env_model_name = "ENV: SCAN";
        s_device.env_bus_name = bus.name;
        s_device.temp_humidity_name = "TH";
        s_device.pressure_name = "P";

        float temperature_c = NAN;
        float humidity_percent = NAN;
        err = read_sht30(&temperature_c, &humidity_percent);
        if (err == ESP_OK) {
            s_device.sht30_ready = true;
            s_device.temp_humidity_name = "SHT30";
            s_device.last_temperature_c = temperature_c;
            s_device.last_humidity_percent = humidity_percent;
            ESP_LOGI(TAG, "ENV SHT30 ready on %s: %.2fC %.2f%%RH",
                     bus.name,
                     static_cast<double>(temperature_c),
                     static_cast<double>(humidity_percent));
        } else {
            ESP_LOGW(TAG, "ENV SHT30 not detected on %s at 0x%02x: %s",
                     bus.name, kSht30Addr, esp_err_to_name(err));
            err = read_sht40(&temperature_c, &humidity_percent);
            if (err == ESP_OK) {
                s_device.sht40_ready = true;
                s_device.temp_humidity_name = "SHT40";
                s_device.last_temperature_c = temperature_c;
                s_device.last_humidity_percent = humidity_percent;
                ESP_LOGI(TAG, "ENV SHT40 ready on %s: %.2fC %.2f%%RH",
                         bus.name,
                         static_cast<double>(temperature_c),
                         static_cast<double>(humidity_percent));
            } else {
                ESP_LOGW(TAG, "ENV SHT40 not detected on %s at 0x%02x: %s",
                         bus.name, kSht40Addr, esp_err_to_name(err));
            }
        }

        for (const uint8_t addr : {kQmp6988AddrUnit, kQmp6988AddrHat}) {
            err = init_qmp6988(addr);
            if (err == ESP_OK) {
                s_device.qmp6988_ready = true;
                s_device.qmp6988_addr = addr;
                s_device.pressure_name = "QMP";
                ESP_LOGI(TAG, "ENV QMP6988 ready on %s at 0x%02x", bus.name, addr);
                break;
            }
        }
        if (!s_device.qmp6988_ready) {
            ESP_LOGW(TAG, "ENV QMP6988 not detected on %s at 0x%02x or 0x%02x",
                     bus.name, kQmp6988AddrUnit, kQmp6988AddrHat);
            err = init_bmp280();
            if (err == ESP_OK) {
                s_device.bmp280_ready = true;
                s_device.pressure_name = "BMP280";
                ESP_LOGI(TAG, "ENV BMP280 ready on %s at 0x%02x", bus.name, kBmp280Addr);
            } else {
                ESP_LOGW(TAG, "ENV BMP280 not detected on %s at 0x%02x: %s",
                         bus.name, kBmp280Addr, esp_err_to_name(err));
            }
        }

        if (s_device.sht40_ready && s_device.bmp280_ready) {
            s_device.env_model_name = "ENV IV";
        } else if (s_device.sht30_ready && s_device.qmp6988_ready) {
            s_device.env_model_name = "ENV III";
        } else if (s_device.sht30_ready && s_device.bmp280_ready) {
            s_device.env_model_name = "ENV II";
        } else if (s_device.sht30_ready || s_device.sht40_ready || s_device.qmp6988_ready || s_device.bmp280_ready) {
            s_device.env_model_name = "ENV I2C";
        }

        if (s_device.sht30_ready || s_device.sht40_ready || s_device.qmp6988_ready || s_device.bmp280_ready) {
            ESP_LOGI(TAG, "Detected %s on %s bus: temp_humidity=%s pressure=%s",
                     s_device.env_model_name,
                     s_device.env_bus_name,
                     (s_device.sht30_ready || s_device.sht40_ready) ? s_device.temp_humidity_name : "none",
                     (s_device.qmp6988_ready || s_device.bmp280_ready) ? s_device.pressure_name : "none");
            s_device.env_reprobe_count = 0;
            return ESP_OK;
        }

        s_device.env_i2c_ready = false;
        s_device.env_model_name = "ENV: WAIT";
        s_device.env_bus_name = "";
        i2c_driver_delete(kEnvI2cPort);
    }
    return ESP_ERR_NOT_FOUND;
}

void mark_temp_humidity_lost(esp_err_t err)
{
    if (s_device.sht30_ready || s_device.sht40_ready) {
        ESP_LOGW(TAG, "ENV %s lost after %u failed reads: %s",
                 s_device.temp_humidity_name,
                 static_cast<unsigned>(s_device.temp_humidity_fail_count),
                 esp_err_to_name(err));
    }
    s_device.sht30_ready = false;
    s_device.sht40_ready = false;
    s_device.temp_humidity_fail_count = 0;
    s_device.temp_humidity_name = "TH";
    s_device.last_temperature_c = NAN;
    s_device.last_humidity_percent = NAN;
}

void mark_qmp6988_lost(esp_err_t err)
{
    if (s_device.qmp6988_ready) {
        ESP_LOGW(TAG, "ENV QMP6988 lost after %u failed reads: %s",
                 static_cast<unsigned>(s_device.qmp6988_fail_count),
                 esp_err_to_name(err));
    }
    s_device.qmp6988_ready = false;
    s_device.qmp6988_fail_count = 0;
    s_device.qmp6988_addr = 0;
    s_device.pressure_name = s_device.bmp280_ready ? "BMP280" : "P";
    s_device.last_pressure_pa = NAN;
}

void mark_bmp280_lost(esp_err_t err)
{
    if (s_device.bmp280_ready) {
        ESP_LOGW(TAG, "ENV BMP280 lost after %u failed reads: %s",
                 static_cast<unsigned>(s_device.bmp280_fail_count),
                 esp_err_to_name(err));
    }
    s_device.bmp280_ready = false;
    s_device.bmp280_fail_count = 0;
    s_device.pressure_name = s_device.qmp6988_ready ? "QMP" : "P";
    s_device.last_pressure_pa = NAN;
}

void maybe_reprobe_env_sensor()
{
    if ((s_device.sht30_ready || s_device.sht40_ready) && (s_device.qmp6988_ready || s_device.bmp280_ready)) {
        s_device.env_reprobe_count = 0;
        return;
    }

    if (++s_device.env_reprobe_count < kEnvReprobePeriodCycles) {
        return;
    }

    s_device.env_reprobe_count = 0;
    ESP_LOGI(TAG, "Re-probing ENV after missing sensor state");
    const esp_err_t err = init_env_sensor();
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "ENV re-probe did not find a sensor: %s", esp_err_to_name(err));
    }
}

void read_env_sensor()
{
    if (!s_device.env_i2c_ready ||
        !(s_device.sht30_ready || s_device.sht40_ready) ||
        !(s_device.qmp6988_ready || s_device.bmp280_ready)) {
        maybe_reprobe_env_sensor();
    }

    if (!s_device.env_i2c_ready) {
        return;
    }

    if (s_device.sht30_ready || s_device.sht40_ready) {
        float temperature_c = NAN;
        float humidity_percent = NAN;
        const esp_err_t err = s_device.sht40_ready ?
            read_sht40(&temperature_c, &humidity_percent) :
            read_sht30(&temperature_c, &humidity_percent);
        if (err == ESP_OK) {
            s_device.last_temperature_c = temperature_c;
            s_device.last_humidity_percent = humidity_percent;
            s_device.temp_humidity_fail_count = 0;
        } else {
            s_device.temp_humidity_fail_count++;
            if (s_device.temp_humidity_fail_count >= kEnvMaxConsecutiveReadFailures) {
                mark_temp_humidity_lost(err);
            } else {
                ESP_LOGW(TAG, "ENV %s read failed (%u/%u): %s",
                         s_device.temp_humidity_name,
                         static_cast<unsigned>(s_device.temp_humidity_fail_count),
                         static_cast<unsigned>(kEnvMaxConsecutiveReadFailures),
                         esp_err_to_name(err));
            }
        }
    }

    if (s_device.qmp6988_ready) {
        float pressure_pa = NAN;
        const esp_err_t err = read_qmp6988(&pressure_pa);
        if (err == ESP_OK) {
            s_device.last_pressure_pa = pressure_pa;
            s_device.qmp6988_fail_count = 0;
        } else {
            s_device.qmp6988_fail_count++;
            if (s_device.qmp6988_fail_count >= kEnvMaxConsecutiveReadFailures) {
                mark_qmp6988_lost(err);
            } else {
                ESP_LOGW(TAG, "ENV QMP6988 read failed (%u/%u): %s",
                         static_cast<unsigned>(s_device.qmp6988_fail_count),
                         static_cast<unsigned>(kEnvMaxConsecutiveReadFailures),
                         esp_err_to_name(err));
            }
        }
    }

    if (s_device.bmp280_ready) {
        float pressure_pa = NAN;
        const esp_err_t err = read_bmp280(&pressure_pa);
        if (err == ESP_OK) {
            s_device.last_pressure_pa = pressure_pa;
            s_device.bmp280_fail_count = 0;
        } else {
            s_device.bmp280_fail_count++;
            if (s_device.bmp280_fail_count >= kEnvMaxConsecutiveReadFailures) {
                mark_bmp280_lost(err);
            } else {
                ESP_LOGW(TAG, "ENV BMP280 read failed (%u/%u): %s",
                         static_cast<unsigned>(s_device.bmp280_fail_count),
                         static_cast<unsigned>(kEnvMaxConsecutiveReadFailures),
                         esp_err_to_name(err));
            }
        }
    }
}

esp_err_t init_internal_temperature_sensor()
{
    s_device.last_temperature_c = NAN;
    ESP_LOGI(TAG, "Internal temperature sensor is not available on ESP32-PICO-V3-02");
    return ESP_OK;
}

esp_err_t init_status_led()
{
    ledc_timer_config_t timer_config = {};
    timer_config.speed_mode = kStatusLedSpeedMode;
    timer_config.duty_resolution = kStatusLedResolution;
    timer_config.timer_num = kStatusLedTimer;
    timer_config.freq_hz = 5000;
    timer_config.clk_cfg = LEDC_AUTO_CLK;
    ESP_RETURN_ON_ERROR(ledc_timer_config(&timer_config), TAG, "Failed to configure status LED timer");

    ledc_channel_config_t channel_config = {};
    channel_config.gpio_num = kStatusLedGpio;
    channel_config.speed_mode = kStatusLedSpeedMode;
    channel_config.channel = kStatusLedChannel;
    channel_config.intr_type = LEDC_INTR_DISABLE;
    channel_config.timer_sel = kStatusLedTimer;
    channel_config.duty = kStatusLedMaxDuty;
    channel_config.hpoint = 0;
    ESP_RETURN_ON_ERROR(ledc_channel_config(&channel_config), TAG, "Failed to configure status LED channel");

    ESP_RETURN_ON_ERROR(set_led_brightness(0), TAG, "Failed to turn status LED off");
    return ESP_OK;
}

esp_err_t init_boot_button()
{
    gpio_config_t boot_button_config = {};
    boot_button_config.pin_bit_mask = 1ULL << kBootButtonGpio;
    boot_button_config.mode = GPIO_MODE_INPUT;
    boot_button_config.pull_up_en = GPIO_PULLUP_DISABLE;
    boot_button_config.pull_down_en = GPIO_PULLDOWN_DISABLE;
    boot_button_config.intr_type = GPIO_INTR_DISABLE;
    return gpio_config(&boot_button_config);
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
            hold_wifi_station_for_commissioning();
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
    s_telemetry_task_handle = xTaskGetCurrentTaskHandle();
    while (true) {
        read_env_sensor();
        if (std::isfinite(s_device.last_temperature_c)) {
            ESP_LOGI(TAG, "Updating ENV temperature: %.2fC", static_cast<double>(s_device.last_temperature_c));
            esp_err_t err = update_temperature_measurement(s_device.last_temperature_c);
            if (err != ESP_OK) {
                ESP_LOGW(TAG, "Failed to update temperature attribute: %s", esp_err_to_name(err));
            } else {
                s_device.temp_null_published = false;
            }
        } else if (!s_device.temp_null_published) {
            ESP_LOGW(TAG, "Publishing ENV temperature as null");
            esp_err_t err = update_temperature_measurement_null();
            if (err != ESP_OK) {
                ESP_LOGW(TAG, "Failed to publish null temperature attribute: %s", esp_err_to_name(err));
            } else {
                s_device.temp_null_published = true;
            }
        }
        if (std::isfinite(s_device.last_humidity_percent)) {
            ESP_LOGI(TAG, "Updating ENV humidity: %.2f%%", static_cast<double>(s_device.last_humidity_percent));
            esp_err_t err = update_humidity_measurement(s_device.last_humidity_percent);
            if (err != ESP_OK) {
                ESP_LOGW(TAG, "Failed to update humidity attribute: %s", esp_err_to_name(err));
            } else {
                s_device.humidity_null_published = false;
            }
        } else if (!s_device.humidity_null_published) {
            ESP_LOGW(TAG, "Publishing ENV humidity as null");
            esp_err_t err = update_humidity_measurement_null();
            if (err != ESP_OK) {
                ESP_LOGW(TAG, "Failed to publish null humidity attribute: %s", esp_err_to_name(err));
            } else {
                s_device.humidity_null_published = true;
            }
        }
        if (std::isfinite(s_device.last_pressure_pa)) {
            ESP_LOGI(TAG, "Updating ENV pressure: %.2f hPa", static_cast<double>(s_device.last_pressure_pa / 100.0f));
            esp_err_t err = update_pressure_measurement(s_device.last_pressure_pa);
            if (err != ESP_OK) {
                ESP_LOGW(TAG, "Failed to update pressure attribute: %s", esp_err_to_name(err));
            } else {
                s_device.pressure_null_published = false;
            }
        } else if (!s_device.pressure_null_published) {
            ESP_LOGW(TAG, "Publishing ENV pressure as null");
            esp_err_t err = update_pressure_measurement_null();
            if (err != ESP_OK) {
                ESP_LOGW(TAG, "Failed to publish null pressure attribute: %s", esp_err_to_name(err));
            } else {
                s_device.pressure_null_published = true;
            }
        }
        read_battery();
        esp_err_t battery_err = update_power_source_battery();
        if (battery_err != ESP_OK) {
            ESP_LOGW(TAG, "Failed to publish battery attributes: %s", esp_err_to_name(battery_err));
        }

        since_heartbeat_ms += kTelemetryPeriodMs;
        if (since_heartbeat_ms >= kHeartbeatPeriodMs) {
            since_heartbeat_ms = 0;
            const uint32_t uptime_s =
                (xTaskGetTickCount() - s_boot_tick) * portTICK_PERIOD_MS / 1000;
            ESP_LOGI(TAG,
                     "Heartbeat: wifi_connected=%d commissioned=%d window_open=%d "
                     "free_heap=%u uptime_s=%u",
                     static_cast<int>(s_indicator.wifi_connected),
                     static_cast<int>(s_indicator.commissioned),
                     static_cast<int>(s_indicator.window_open),
                     static_cast<unsigned>(esp_get_free_heap_size()),
                     static_cast<unsigned>(uptime_s));
        }
        ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(kTelemetryPeriodMs));
    }
}

void request_telemetry_report(const char *reason)
{
    if (!s_telemetry_task_handle) {
        return;
    }
    ESP_LOGI(TAG, "Telemetry report requested: %s", reason);
    xTaskNotifyGive(s_telemetry_task_handle);
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
            ESP_LOGI(TAG, "Button A %s", reported_state ? "pressed" : "released");
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
                    s_indicator.window_open = true;
                    s_device.screen_index = 1;
                    wake_display();
                    render_current_screen();
                    schedule_commissioning_window();
                } else {
                    const bool was_awake = s_device.display_awake;
                    wake_display();
                    if (was_awake) {
                        s_device.screen_index = (s_device.screen_index + 1) % display_screen_count();
                        ESP_LOGI(TAG, "Display screen changed: %u", static_cast<unsigned>(s_device.screen_index));
                    }
                    render_current_screen();
                    request_telemetry_report("button");
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

void display_task(void *)
{
    while (true) {
        update_network_state_cache();
        read_battery();
        if (s_device.display_awake &&
            static_cast<int32_t>(xTaskGetTickCount() - s_device.display_awake_until) >= 0) {
            set_display_awake(false);
        }
        if (s_device.display_awake) {
            render_current_screen();
        }
        vTaskDelay(pdMS_TO_TICKS(kDisplayPeriodMs));
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
        if (s_indicator.wifi_connected) {
            enable_wifi_power_save();
        }
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
        s_device.screen_index = 1;
        wake_display();
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

    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        nvs_flash_init();
    }

    ESP_ERROR_CHECK(init_power_hold());
    ESP_ERROR_CHECK(init_axp192());
    ESP_ERROR_CHECK(init_internal_temperature_sensor());
    ESP_ERROR_CHECK(init_status_led());
    ESP_ERROR_CHECK(init_boot_button());
    ESP_ERROR_CHECK(init_battery_monitor());
    esp_err_t env_ret = init_env_sensor();
    if (env_ret != ESP_OK) {
        ESP_LOGW(TAG, "ENV initialization skipped: %s", esp_err_to_name(env_ret));
    }
    read_battery();
    esp_err_t lcd_ret = init_lcd();
    if (lcd_ret != ESP_OK) {
        ESP_LOGW(TAG, "LCD initialization failed: %s", esp_err_to_name(lcd_ret));
    }
    s_device.button_pressed = read_boot_button_pressed();

    s_commissionable_data_provider.Init();
    esp_matter::set_custom_commissionable_data_provider(&s_commissionable_data_provider);
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

    humidity_sensor::config_t humidity_config;
    endpoint_t *humidity_ep = humidity_sensor::create(node, &humidity_config, ENDPOINT_FLAG_NONE, nullptr);
    if (!humidity_ep) {
        ESP_LOGE(TAG, "Failed to create humidity_sensor endpoint");
        return;
    }

    pressure_sensor::config_t pressure_config;
    endpoint_t *pressure_ep = pressure_sensor::create(node, &pressure_config, ENDPOINT_FLAG_NONE, nullptr);
    if (!pressure_ep) {
        ESP_LOGE(TAG, "Failed to create pressure_sensor endpoint");
        return;
    }

    power_source::config_t power_config;
    power_config.power_source.status =
        chip::to_underlying(chip::app::Clusters::PowerSource::PowerSourceStatusEnum::kActive);
    std::snprintf(power_config.power_source.description,
                  sizeof(power_config.power_source.description),
                  "M5StickC battery");
    power_config.power_source.feature_flags =
        cluster::power_source::feature::battery::get_id() |
        cluster::power_source::feature::rechargeable::get_id();
    power_config.power_source.features.battery.bat_charge_level =
        chip::to_underlying(matter_battery_charge_level(s_device.battery_mv));
    power_config.power_source.features.battery.bat_replaceability =
        chip::to_underlying(chip::app::Clusters::PowerSource::BatReplaceabilityEnum::kNotReplaceable);
    power_config.power_source.features.rechargeable.bat_charge_state =
        chip::to_underlying(matter_battery_charge_state(s_device.battery_charge_state));
    power_config.power_source.features.rechargeable.bat_functional_while_charging = true;

    endpoint_t *power_ep = power_source::create(node, &power_config, ENDPOINT_FLAG_NONE, nullptr);
    if (!power_ep) {
        ESP_LOGE(TAG, "Failed to create power_source endpoint");
        return;
    }
    cluster_t *power_cluster = cluster::get(power_ep, chip::app::Clusters::PowerSource::Id);
    if (!power_cluster) {
        ESP_LOGE(TAG, "Failed to get power_source cluster");
        return;
    }
    const nullable<uint32_t> initial_battery_voltage =
        s_device.battery_mv >= 0 ? nullable<uint32_t>(static_cast<uint32_t>(s_device.battery_mv)) : nullable<uint32_t>();
    const nullable<uint8_t> initial_battery_percent =
        s_device.battery_mv >= 0 ? nullable<uint8_t>(matter_battery_percent_remaining(s_device.battery_mv)) : nullable<uint8_t>();
    cluster::power_source::attribute::create_bat_voltage(power_cluster, initial_battery_voltage, nullable<uint32_t>(0), nullable<uint32_t>(0xFFFF));
    cluster::power_source::attribute::create_bat_percent_remaining(power_cluster, initial_battery_percent, nullable<uint8_t>(0), nullable<uint8_t>(200));
    cluster::power_source::attribute::create_bat_present(power_cluster, true);

    s_device.temp_ep_id = endpoint::get_id(temp_ep);
    s_device.button_ep_id = endpoint::get_id(button_ep);
    s_device.humidity_ep_id = endpoint::get_id(humidity_ep);
    s_device.pressure_ep_id = endpoint::get_id(pressure_ep);
    s_device.power_ep_id = endpoint::get_id(power_ep);
    ESP_LOGI(TAG, "Endpoints: temperature=%u button=%u humidity=%u pressure=%u power=%u",
             s_device.temp_ep_id,
             s_device.button_ep_id,
             s_device.humidity_ep_id,
             s_device.pressure_ep_id,
             s_device.power_ep_id);

    esp_matter::start(matter_event_callback);

    quiesce_uncommissioned_wifi();
    update_network_state_cache();
    refresh_onboarding_codes();
    log_commissioning_state("after-start");
    log_onboarding_codes();
    render_current_screen();

    xTaskCreate(indicator_task, "indicator_task", 3072, nullptr, 4, nullptr);
    xTaskCreate(display_task, "display_task", 4096, nullptr, 4, nullptr);
    xTaskCreate(telemetry_task, "telemetry_task", 4096, nullptr, 5, nullptr);
    xTaskCreate(button_task, "button_task", 4096, nullptr, 5, nullptr);
}
