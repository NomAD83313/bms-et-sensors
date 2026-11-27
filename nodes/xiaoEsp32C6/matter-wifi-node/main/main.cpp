#include "bms_node_core/device_info.h"
#include "oled.h"
#include "rail_motion.h"
#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "esp_adc/adc_cali_scheme.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_check.h"
#include "esp_matter.h"
#include "esp_matter_endpoint.h"
#include "esp_wifi.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "nvs_flash.h"

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <clusters/PowerSource/AttributeIds.h>
#include <clusters/PowerSource/Enums.h>
#include <credentials/examples/DeviceAttestationCredsExample.h>
#include <platform/ConnectivityManager.h>
#include <setup_payload/OnboardingCodesUtil.h>

using namespace esp_matter;

#ifndef CONFIG_XIAO_BATTERY_DIVIDER
#define CONFIG_XIAO_BATTERY_DIVIDER 0
#endif

namespace {
constexpr char TAG[] = "xiao_matter";
constexpr uint32_t kOrientation = 0xFFF1FC00;
constexpr uint32_t kPower = 0x2F;
constexpr adc_channel_t kBatteryChannel = ADC_CHANNEL_0;
constexpr bms_node_core::BoardIdentity kIdentity = {
    .vendor_id = 0xFFF1,
    .product_id = 0x8009,
    .vendor_name = "BMS DOA",
    .product_name = "XIAO ESP32-C6 WiFi",
    .serial_prefix = "BMS-XC6-",
    .hw_version = 1,
    .hw_version_str = "v1.1",
    .software_version_str = MATTER_NODE_VERSION,
    .provide_rotating_id = false,
};
bms_node_core::DeviceInfoProvider identity(kIdentity);
i2c_master_bus_handle_t bus;
i2c_master_dev_handle_t sensor;
i2c_master_dev_handle_t charger;
adc_oneshot_unit_handle_t adc;
adc_cali_handle_t calibration;
uint16_t power_endpoint;
constexpr gpio_num_t kUserButton = GPIO_NUM_1;
using ChargeState = chip::app::Clusters::PowerSource::BatChargeStateEnum;
Oled oled;
bool oled_ready;
nullable<int32_t> motion[5];
nullable<uint32_t> battery_voltage;
nullable<uint8_t> battery_percent;
ChargeState charge_state = ChargeState::kUnknown;
struct RailSample {
    int32_t acceleration[3];
    bool valid;
    unsigned axis;
    RailState state;
    int64_t transition_ms;
};
std::atomic<unsigned> rail_axis{0};
QueueHandle_t sample_queue;
RailSample rail_sample{};

bool read_register(uint8_t reg, uint8_t *data, size_t size)
{
    return i2c_master_transmit_receive(sensor, &reg, 1, data, size, 50) == ESP_OK;
}

bool write_register(uint8_t reg, uint8_t value)
{
    const uint8_t data[] = {reg, value};
    return i2c_master_transmit(sensor, data, sizeof(data), 50) == ESP_OK;
}

void detect_charger()
{
    constexpr uint8_t kAddress = 0x6b;
    if (!bus || i2c_master_probe(bus, kAddress, 50) != ESP_OK) {
        return;
    }
    i2c_device_config_t config{};
    config.dev_addr_length = I2C_ADDR_BIT_LEN_7;
    config.device_address = kAddress;
    config.scl_speed_hz = 100000;
    if (i2c_master_bus_add_device(bus, &config, &charger) != ESP_OK) {
        return;
    }
    constexpr uint8_t kPartNumberRegister = 0x0b;
    uint8_t part;
    if (i2c_master_transmit_receive(charger, &kPartNumberRegister, 1, &part, 1, 50) == ESP_OK &&
        ((part >> 3) & 0x0f) == 0x08) {
        ESP_LOGI(TAG, "SY6974B charger detected at I2C address 0x6b");
        return;
    }
    i2c_master_bus_rm_device(charger);
    charger = nullptr;
    ESP_LOGW(TAG, "Device at I2C address 0x6b did not identify as SY6974B");
}

bool connect_sensor()
{
    for (uint8_t address : {0x15, 0x14}) {
        if (i2c_master_probe(bus, address, 50) != ESP_OK) {
            continue;
        }
        i2c_device_config_t config{};
        config.dev_addr_length = I2C_ADDR_BIT_LEN_7;
        config.device_address = address;
        config.scl_speed_hz = 100000;
        if (i2c_master_bus_add_device(bus, &config, &sensor) != ESP_OK) {
            continue;
        }
        uint8_t id;
        if (read_register(0x00, &id, 1) && id == 0x90 && write_register(0x19, 0x02)) {
            vTaskDelay(pdMS_TO_TICKS(20));
            // Normal mode, +/-2 g, high oversampling, 100 Hz.
            if (write_register(0x1A, 0x38)) {
                ESP_LOGI(TAG, "BMA400 detected at I2C address 0x%02x", address);
                vTaskDelay(pdMS_TO_TICKS(25));
                return true;
            }
        }
        i2c_master_bus_rm_device(sensor);
        sensor = nullptr;
    }
    return false;
}

int32_t acceleration_mg(const uint8_t *data)
{
    int raw = data[0] | ((data[1] & 0x0f) << 8);
    return ((raw ^ 0x800) - 0x800) * 1000 / 1024;
}

void sample_rail(void *)
{
    RailMotion estimator;
    RailSample sample{};
    TickType_t wake = xTaskGetTickCount();
    int64_t last_probe_ms = -1000;
    while (true) {
        const auto axis = rail_axis.load();
        if (axis != sample.axis) {
            sample.axis = axis;
            estimator = RailMotion{};
        }
        const int64_t now_ms = esp_timer_get_time() / 1000;
        if (bus && !sensor && now_ms - last_probe_ms >= 1000) {
            last_probe_ms = now_ms;
            connect_sensor();
        }
        uint8_t data[6];
        sample.valid = sensor && read_register(0x04, data, sizeof(data));
        if (sample.valid) {
            for (unsigned i = 0; i < 3; ++i) {
                sample.acceleration[i] = acceleration_mg(data + i * 2);
            }
            estimator.sample(sample.acceleration[sample.axis], esp_timer_get_time() / 1000);
        } else {
            estimator = RailMotion{};
            if (sensor) {
                i2c_master_bus_rm_device(sensor);
                sensor = nullptr;
            }
        }
        sample.state = estimator.state;
        sample.transition_ms = estimator.transition_ms;
        xQueueOverwrite(sample_queue, &sample);
        if (xTaskDelayUntil(&wake, pdMS_TO_TICKS(10)) == pdFALSE) {
            wake = xTaskGetTickCount();
            vTaskDelay(1);
        }
    }
}

void publish_orientation()
{
    constexpr double kCentidegrees = 18000.0 / 3.141592653589793;
    nullable<int32_t> values[5];
    if (rail_sample.valid) {
        const auto [x, y, z] = rail_sample.acceleration;
        values[0] = nullable<int32_t>(x);
        values[1] = nullable<int32_t>(y);
        values[2] = nullable<int32_t>(z);
        const double norm = std::hypot(x, y, z);
        if (norm >= 800 && norm <= 1200) {
            values[3] = nullable<int32_t>(std::lround(std::atan2(-x, std::hypot(y, z)) * kCentidegrees));
            if (std::hypot(y, z) >= 20) {
                values[4] = nullable<int32_t>(std::lround(std::atan2(y, z) * kCentidegrees));
            }
        }
    }
    for (uint32_t id = 0; id < 5; ++id) {
        motion[id] = values[id];
        auto value = esp_matter_nullable_int32(values[id]);
        attribute::update(0, kOrientation, id, &value);
    }
}

esp_err_t init_battery()
{
    adc_oneshot_unit_init_cfg_t unit{};
    unit.unit_id = ADC_UNIT_1;
    ESP_RETURN_ON_ERROR(adc_oneshot_new_unit(&unit, &adc), TAG, "ADC init failed");
    adc_oneshot_chan_cfg_t channel{};
    channel.atten = ADC_ATTEN_DB_12;
    channel.bitwidth = ADC_BITWIDTH_DEFAULT;
    ESP_RETURN_ON_ERROR(adc_oneshot_config_channel(adc, kBatteryChannel, &channel), TAG, "ADC channel failed");
    adc_cali_curve_fitting_config_t curve{};
    curve.unit_id = ADC_UNIT_1;
    curve.chan = kBatteryChannel;
    curve.atten = channel.atten;
    curve.bitwidth = channel.bitwidth;
    return adc_cali_create_scheme_curve_fitting(&curve, &calibration);
}

void publish_battery()
{
    using namespace chip::app::Clusters::PowerSource;
    nullable<uint32_t> voltage;
    nullable<uint8_t> percent;
    int sum = 0;
    for (int i = 0; calibration && i < 16; ++i) {
        int raw, mv;
        if (adc_oneshot_read(adc, kBatteryChannel, &raw) != ESP_OK ||
            adc_cali_raw_to_voltage(calibration, raw, &mv) != ESP_OK) {
            sum = 0;
            break;
        }
        sum += mv;
    }
    const int mv = sum / 8;
    if (mv >= 2500 && mv <= 4350) {
        voltage = nullable<uint32_t>(mv);
        percent = nullable<uint8_t>(std::clamp((mv - 3300) * 200 / 900, 0, 200));
    }
    auto mv_value = esp_matter_nullable_uint32(voltage);
    auto percent_value = esp_matter_nullable_uint8(percent);
    battery_voltage = voltage;
    battery_percent = percent;
    attribute::update(power_endpoint, kPower, 0x0B, &mv_value);
    attribute::update(power_endpoint, kPower, 0x0C, &percent_value);
    if (!percent.is_null()) {
        auto level = esp_matter_enum8(percent.value() <= 20 ? 2 : percent.value() <= 50 ? 1 : 0);
        attribute::update(power_endpoint, kPower, 0x0E, &level);
    }
    if (charger) {
        constexpr uint8_t kStatusRegister = 0x08;
        constexpr BatChargeStateEnum kChargeStates[] = {
            BatChargeStateEnum::kIsNotCharging, BatChargeStateEnum::kIsCharging,
            BatChargeStateEnum::kIsCharging, BatChargeStateEnum::kIsAtFullCharge,
        };
        uint8_t status;
        BatChargeStateEnum state = BatChargeStateEnum::kUnknown;
        if (i2c_master_transmit_receive(charger, &kStatusRegister, 1, &status, 1, 50) == ESP_OK) {
            state = kChargeStates[(status >> 3) & 0x03];
        }
        auto value = esp_matter_enum8(static_cast<uint8_t>(state));
        charge_state = state;
        attribute::update(power_endpoint, kPower, Attributes::BatChargeState::Id, &value);
    }
}

void show_number(int y, const char *label, nullable<int32_t> number, double scale, const char *unit)
{
    char line[22];
    if (number.is_null()) {
        std::snprintf(line, sizeof(line), "%s --", label);
    } else {
        std::snprintf(line, sizeof(line), "%s %+.1f %s", label, number.value() / scale, unit);
    }
    oled.text(0, y, line);
}

void draw_screen(unsigned page)
{
    oled.clear();
    switch (page) {
    case 3: {
        oled.text(0, 0, rail_sample.axis == 0 ? "RAIL X" : "RAIL Y", 2);
        const char *status = "CALIBRATE";
        switch (rail_sample.state) {
        case RailState::Stop: status = "STOP"; break;
        case RailState::Left: status = "LEFT"; break;
        case RailState::Right: status = "RIGHT"; break;
        case RailState::Unknown: break;
        }
        oled.text(0, 18, rail_sample.valid ? status : "NO SENSOR", 2);
        char line[22];
        if (rail_sample.transition_ms) {
            std::snprintf(line, sizeof(line), "T+%.3f S", rail_sample.transition_ms / 1000.0);
            oled.text(0, 39, line);
        }
        oled.text(0, 54, "HOLD USER: X/Y");
        break;
    }
    case 0: {
        oled.text(0, 0, "TILT", 2);
        show_number(16, "PITCH", motion[3], 100, "DEG");
        show_number(27, "ROLL", motion[4], 100, "DEG");
        // Draw a horizon using roll, shifted vertically by pitch.
        if (!motion[3].is_null() && !motion[4].is_null()) {
            const double roll = motion[4].value() * (3.141592653589793 / 18000.0);
            const int center = 50 + std::clamp<int32_t>(motion[3].value() / 500, -6, 6);
            for (int i = -26; i <= 26; ++i) {
                const int y = center + std::lround(i * std::sin(roll));
                if (y >= 39 && y <= 62) {
                    oled.pixel(64 + std::lround(i * std::cos(roll)), y);
                }
            }
            oled.pixel(64, 50);
        }
        break;
    }
    case 1:
        oled.text(0, 0, "ACCEL", 2);
        show_number(16, "X", motion[0], 1, "MG");
        show_number(30, "Y", motion[1], 1, "MG");
        show_number(44, "Z", motion[2], 1, "MG");
        break;
    case 2: {
        oled.text(0, 0, "BATTERY", 2);
        char line[22];
        if (battery_percent.is_null()) {
            oled.text(0, 16, "LEVEL --");
        } else {
            std::snprintf(line, sizeof(line), "%u.%u%%  %lu MV", battery_percent.value() / 2u,
                          (battery_percent.value() % 2u) * 5u,
                          static_cast<unsigned long>(battery_voltage.value()));
            oled.text(0, 16, line);
        }
        const char *status = "CHARGE UNKNOWN";
        switch (charge_state) {
        case ChargeState::kIsCharging: status = "CHARGING"; break;
        case ChargeState::kIsAtFullCharge: status = "CHARGE COMPLETE"; break;
        case ChargeState::kIsNotCharging: status = "NOT CHARGING"; break;
        default: break;
        }
        oled.text(0, 30, status);
        const bool wifi = chip::DeviceLayer::ConnectivityMgr().IsWiFiStationConnected();
        oled.text(0, 44, wifi ? "WIFI CONNECTED" : "WIFI OFFLINE");
        break;
    }
    }
    oled.show();
}

void telemetry(void *)
{
    constexpr TickType_t motion_period = pdMS_TO_TICKS(1000);
    constexpr TickType_t battery_period = pdMS_TO_TICKS(30000);
    TickType_t last_motion = xTaskGetTickCount() - motion_period;
    TickType_t last_battery = xTaskGetTickCount() - battery_period;
    bool previous_raw = false;
    bool pressed = false;
    bool axis_changed = false;
    TickType_t pressed_at = 0;
    unsigned page = 3;
    uint64_t previous_transition = UINT64_MAX;
    while (true) {
        const TickType_t now = xTaskGetTickCount();
        bool redraw = false;
        xQueuePeek(sample_queue, &rail_sample, 0);
        // Keep state and timestamp atomic over Matter. The low bits carry axis and state.
        const uint64_t transition = (static_cast<uint64_t>(rail_sample.transition_ms) << 3) |
                                    (rail_sample.axis << 2) | static_cast<uint8_t>(rail_sample.state);
        if (transition != previous_transition) {
            previous_transition = transition;
            auto value = esp_matter_uint64(transition);
            attribute::update(0, kOrientation, 5, &value);
            redraw = page == 3;
        }
        if (now - last_motion >= motion_period) {
            last_motion = now;
            publish_orientation();
            redraw = true;
        }
        if (now - last_battery >= battery_period) {
            last_battery = now;
            publish_battery();
            redraw = true;
        }
        const bool raw = gpio_get_level(kUserButton) == 0;
        if (raw == previous_raw && raw != pressed) {
            pressed = raw;
            if (pressed) {
                pressed_at = now;
                axis_changed = false;
            } else if (!axis_changed) {
                page = (page + 1) % 4;
                ESP_LOGI(TAG, "OLED page %u", page + 1);
                redraw = true;
            }
        }
        if (page == 3 && pressed && !axis_changed && now - pressed_at >= pdMS_TO_TICKS(800)) {
            const uint8_t axis = 1 - rail_axis.load();
            rail_axis.store(axis);
            nvs_handle_t config;
            if (nvs_open("rail", NVS_READWRITE, &config) == ESP_OK) {
                ESP_ERROR_CHECK_WITHOUT_ABORT(nvs_set_u8(config, "axis", axis));
                ESP_ERROR_CHECK_WITHOUT_ABORT(nvs_commit(config));
                nvs_close(config);
            }
            axis_changed = true;
        }
        previous_raw = raw;
        if (oled_ready && redraw) {
            draw_screen(page);
        }
        vTaskDelay(pdMS_TO_TICKS(25));
    }
}

esp_err_t attribute_callback(attribute::callback_type_t, uint16_t, uint32_t,
                             uint32_t, esp_matter_attr_val_t *, void *)
{
    return ESP_OK;
}

void event_callback(const chip::DeviceLayer::ChipDeviceEvent *event, intptr_t)
{
    if (event->Type == chip::DeviceLayer::DeviceEventType::kWiFiConnectivityChange) {
        const bool connected = event->WiFiConnectivityChange.Result == chip::DeviceLayer::kConnectivity_Established;
        gpio_set_level(GPIO_NUM_15, !connected);
        ESP_LOGI(TAG, "Wi-Fi %s", connected ? "connected" : "disconnected");
    }
}

void wifi_disconnect(void *, esp_event_base_t, int32_t, void *data)
{
    const auto *event = static_cast<const wifi_event_sta_disconnected_t *>(data);
    ESP_LOGW(TAG, "Wi-Fi disconnect reason %u", static_cast<unsigned>(event->reason));
}
}

extern "C" void app_main()
{
    ESP_ERROR_CHECK(nvs_flash_init());
    nvs_handle_t rail_config;
    if (nvs_open("rail", NVS_READONLY, &rail_config) == ESP_OK) {
        uint8_t axis;
        if (nvs_get_u8(rail_config, "axis", &axis) == ESP_OK && axis <= 1) {
            rail_axis.store(axis);
        }
        nvs_close(rail_config);
    }
    // Enable the RF switch and select the on-board antenna.
    ESP_ERROR_CHECK(gpio_set_level(GPIO_NUM_14, 0));
    ESP_ERROR_CHECK(gpio_set_direction(GPIO_NUM_14, GPIO_MODE_OUTPUT));
    ESP_ERROR_CHECK(gpio_set_level(GPIO_NUM_3, 0));
    ESP_ERROR_CHECK(gpio_set_direction(GPIO_NUM_3, GPIO_MODE_OUTPUT));
    ESP_ERROR_CHECK(gpio_set_level(GPIO_NUM_15, 1));
    ESP_ERROR_CHECK(gpio_set_direction(GPIO_NUM_15, GPIO_MODE_OUTPUT));
    gpio_config_t button{};
    button.pin_bit_mask = 1ULL << kUserButton;
    button.mode = GPIO_MODE_INPUT;
    button.pull_up_en = GPIO_PULLUP_ENABLE;
    ESP_ERROR_CHECK(gpio_config(&button));

    i2c_master_bus_config_t i2c{};
    i2c.i2c_port = I2C_NUM_0;
    i2c.sda_io_num = GPIO_NUM_22;
    i2c.scl_io_num = GPIO_NUM_23;
    i2c.clk_source = I2C_CLK_SRC_DEFAULT;
    i2c.glitch_ignore_cnt = 7;
    i2c.flags.enable_internal_pullup = true;
    ESP_ERROR_CHECK_WITHOUT_ABORT(i2c_new_master_bus(&i2c, &bus));
    detect_charger();
    oled_ready = oled.init(bus);
    ESP_LOGI(TAG, "SSD1306 OLED: %s", oled_ready ? "ready" : "unavailable");
    if (CONFIG_XIAO_BATTERY_DIVIDER) {
        ESP_ERROR_CHECK_WITHOUT_ABORT(init_battery());
    }

    ESP_ERROR_CHECK(bms_node_core::install_device_identity(identity));
    chip::Credentials::SetDeviceAttestationCredentialsProvider(chip::Credentials::Examples::GetExampleDACProvider());
    node::config_t config;
    auto *node_handle = node::create(&config, attribute_callback, nullptr);
    ESP_ERROR_CHECK(node_handle ? ESP_OK : ESP_ERR_NO_MEM);
    auto *orientation = cluster::create(endpoint::get(node_handle, 0), kOrientation, CLUSTER_FLAG_SERVER);
    ESP_ERROR_CHECK(orientation ? ESP_OK : ESP_ERR_NO_MEM);
    cluster::global::attribute::create_feature_map(orientation, 0);
    cluster::global::attribute::create_cluster_revision(orientation, 1);
    for (uint32_t id = 0; id < 5; ++id) {
        ESP_ERROR_CHECK(attribute::create(orientation, id, ATTRIBUTE_FLAG_NULLABLE,
            esp_matter_nullable_int32(nullable<int32_t>())) ? ESP_OK : ESP_ERR_NO_MEM);
    }
    ESP_ERROR_CHECK(attribute::create(orientation, 5, 0, esp_matter_uint64(0)) ? ESP_OK : ESP_ERR_NO_MEM);

    endpoint::power_source::config_t power_config;
    auto &power = power_config.power_source;
    power.status = 1;
    std::strcpy(power.description, "LiPo");
    power.feature_flags = cluster::power_source::feature::battery::get_id();
    power.features.battery.bat_replaceability = 3;
    if (charger) {
        power.feature_flags |= cluster::power_source::feature::rechargeable::get_id();
        power.features.rechargeable.bat_functional_while_charging = true;
    }
    auto *power_ep = endpoint::power_source::create(node_handle, &power_config, ENDPOINT_FLAG_NONE, nullptr);
    ESP_ERROR_CHECK(power_ep ? ESP_OK : ESP_ERR_NO_MEM);
    power_endpoint = endpoint::get_id(power_ep);
    auto *power_cluster = cluster::get(power_ep, kPower);
    using namespace cluster::power_source::attribute;
    ESP_ERROR_CHECK(create_bat_voltage(power_cluster, {}, 0, 4350) ? ESP_OK : ESP_ERR_NO_MEM);
    ESP_ERROR_CHECK(create_bat_percent_remaining(power_cluster, {}, 0, 200) ? ESP_OK : ESP_ERR_NO_MEM);

    ESP_ERROR_CHECK(esp_matter::start(event_callback));
    ESP_ERROR_CHECK_WITHOUT_ABORT(esp_event_handler_register(
        WIFI_EVENT, WIFI_EVENT_STA_DISCONNECTED, wifi_disconnect, nullptr));
    PrintOnboardingCodes(chip::RendezvousInformationFlags(chip::RendezvousInformationFlag::kBLE));
    sample_queue = xQueueCreate(1, sizeof(RailSample));
    ESP_ERROR_CHECK(sample_queue ? ESP_OK : ESP_ERR_NO_MEM);
    ESP_ERROR_CHECK(xTaskCreate(sample_rail, "rail_sample", 3072, nullptr, 6, nullptr) == pdPASS
                    ? ESP_OK : ESP_ERR_NO_MEM);
    ESP_ERROR_CHECK(xTaskCreate(telemetry, "telemetry", 4096, nullptr, 5, nullptr) == pdPASS
                    ? ESP_OK : ESP_ERR_NO_MEM);
}
