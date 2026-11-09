#include "sps30.h"

#include "esp_check.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include <algorithm>
#include <cstring>
#include <vector>

namespace {

constexpr const char *TAG = "sps30";
constexpr int kSps30UartBaud = 115200;
constexpr int kUartRxBufferSize = 512;
constexpr int kUartTxBufferSize = 256;
constexpr uint8_t kShdlcFrameDelimiter = 0x7E;
constexpr uint8_t kShdlcEscape = 0x7D;
constexpr uint8_t kShdlcAddress = 0x00;
constexpr uint8_t kCmdStartMeasurement = 0x00;
constexpr uint8_t kCmdStopMeasurement = 0x01;
constexpr uint8_t kCmdReadMeasurement = 0x03;
constexpr uint8_t kStateOk = 0x00;
constexpr uint32_t kResponseTimeoutMs = 1000;
constexpr size_t kMeasurementPayloadSize = 40;

uint8_t shdlc_checksum(const uint8_t *data, size_t len)
{
    uint16_t sum = 0;
    for (size_t i = 0; i < len; ++i) {
        sum += data[i];
    }
    return static_cast<uint8_t>(0xFFu - (sum & 0xFFu));
}

void append_escaped(std::vector<uint8_t> &frame, uint8_t value)
{
    switch (value) {
    case 0x7E:
        frame.push_back(kShdlcEscape);
        frame.push_back(0x5E);
        break;
    case 0x7D:
        frame.push_back(kShdlcEscape);
        frame.push_back(0x5D);
        break;
    case 0x11:
        frame.push_back(kShdlcEscape);
        frame.push_back(0x31);
        break;
    case 0x13:
        frame.push_back(kShdlcEscape);
        frame.push_back(0x33);
        break;
    default:
        frame.push_back(value);
        break;
    }
}

bool unescape_byte(uint8_t escaped, uint8_t &value)
{
    switch (escaped) {
    case 0x5E:
        value = 0x7E;
        return true;
    case 0x5D:
        value = 0x7D;
        return true;
    case 0x31:
        value = 0x11;
        return true;
    case 0x33:
        value = 0x13;
        return true;
    default:
        return false;
    }
}

float be_float(const uint8_t *data)
{
    const uint32_t raw = (static_cast<uint32_t>(data[0]) << 24) |
                         (static_cast<uint32_t>(data[1]) << 16) |
                         (static_cast<uint32_t>(data[2]) << 8) |
                         static_cast<uint32_t>(data[3]);
    float value = 0.0f;
    std::memcpy(&value, &raw, sizeof(value));
    return value;
}

} // namespace

esp_err_t Sps30Sensor::init(uart_port_t uart_port, gpio_num_t tx_gpio, gpio_num_t rx_gpio)
{
    uart_port_ = uart_port;

    uart_config_t uart_config = {};
    uart_config.baud_rate = kSps30UartBaud;
    uart_config.data_bits = UART_DATA_8_BITS;
    uart_config.parity = UART_PARITY_DISABLE;
    uart_config.stop_bits = UART_STOP_BITS_1;
    uart_config.flow_ctrl = UART_HW_FLOWCTRL_DISABLE;
    uart_config.source_clk = UART_SCLK_DEFAULT;

    esp_err_t err = uart_driver_install(uart_port_, kUartRxBufferSize, kUartTxBufferSize, 0, nullptr, 0);
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        ESP_LOGE(TAG, "Failed to install UART driver: %s", esp_err_to_name(err));
        return err;
    }
    ESP_RETURN_ON_ERROR(uart_param_config(uart_port_, &uart_config), TAG, "Failed to configure UART");
    ESP_RETURN_ON_ERROR(uart_set_pin(uart_port_, tx_gpio, rx_gpio, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE),
                        TAG, "Failed to assign UART pins");
    uart_flush_input(uart_port_);

    size_t response_len = 0;
    uint8_t response[8] = {};
    send_command(kCmdStopMeasurement, nullptr, 0, response, sizeof(response), response_len);
    vTaskDelay(pdMS_TO_TICKS(100));

    const uint8_t start_args[] = { 0x01, 0x03 };
    ESP_RETURN_ON_ERROR(send_command(kCmdStartMeasurement, start_args, sizeof(start_args),
                                     response, sizeof(response), response_len),
                        TAG, "Failed to start SPS30 UART measurement");
    vTaskDelay(pdMS_TO_TICKS(1000));

    ESP_LOGI(TAG, "SPS30 measurement started on UART%d TX=GPIO%d RX=GPIO%d",
             static_cast<int>(uart_port_),
             static_cast<int>(tx_gpio),
             static_cast<int>(rx_gpio));
    return ESP_OK;
}

esp_err_t Sps30Sensor::send_command(uint8_t command, const uint8_t *data, size_t data_len,
                                    uint8_t *response, size_t response_capacity, size_t &response_len)
{
    if (data_len > 255 || response_capacity == 0) {
        return ESP_ERR_INVALID_ARG;
    }

    std::vector<uint8_t> payload;
    payload.reserve(3 + data_len);
    payload.push_back(kShdlcAddress);
    payload.push_back(command);
    payload.push_back(static_cast<uint8_t>(data_len));
    for (size_t i = 0; i < data_len; ++i) {
        payload.push_back(data[i]);
    }
    const uint8_t checksum = shdlc_checksum(payload.data(), payload.size());

    std::vector<uint8_t> frame;
    frame.reserve(payload.size() + 4);
    frame.push_back(kShdlcFrameDelimiter);
    for (uint8_t value : payload) {
        append_escaped(frame, value);
    }
    append_escaped(frame, checksum);
    frame.push_back(kShdlcFrameDelimiter);

    uart_flush_input(uart_port_);
    const int written = uart_write_bytes(uart_port_, frame.data(), frame.size());
    if (written != static_cast<int>(frame.size())) {
        return ESP_FAIL;
    }
    ESP_RETURN_ON_ERROR(uart_wait_tx_done(uart_port_, pdMS_TO_TICKS(100)), TAG, "UART TX timeout");

    std::vector<uint8_t> rx_payload;
    rx_payload.reserve(response_capacity + 8);
    bool in_frame = false;
    bool escaped = false;
    const TickType_t deadline = xTaskGetTickCount() + pdMS_TO_TICKS(kResponseTimeoutMs);

    while (xTaskGetTickCount() < deadline) {
        uint8_t byte = 0;
        const int got = uart_read_bytes(uart_port_, &byte, 1, pdMS_TO_TICKS(20));
        if (got <= 0) {
            continue;
        }
        if (byte == kShdlcFrameDelimiter) {
            if (!in_frame) {
                in_frame = true;
                rx_payload.clear();
                escaped = false;
                continue;
            }
            if (rx_payload.size() < 5) {
                continue;
            }

            const uint8_t received_checksum = rx_payload.back();
            rx_payload.pop_back();
            const uint8_t expected_checksum = shdlc_checksum(rx_payload.data(), rx_payload.size());
            if (received_checksum != expected_checksum) {
                ESP_LOGW(TAG, "SPS30 UART checksum mismatch");
                return ESP_ERR_INVALID_CRC;
            }
            if (rx_payload[0] != kShdlcAddress || rx_payload[1] != command) {
                ESP_LOGW(TAG, "SPS30 UART unexpected response address/command");
                return ESP_FAIL;
            }
            if (rx_payload[2] != kStateOk) {
                ESP_LOGW(TAG, "SPS30 UART command 0x%02X failed with state 0x%02X", command, rx_payload[2]);
                return ESP_FAIL;
            }
            const size_t payload_len = rx_payload[3];
            if (rx_payload.size() != payload_len + 4 || payload_len > response_capacity) {
                return ESP_ERR_INVALID_SIZE;
            }
            std::copy(rx_payload.begin() + 4, rx_payload.end(), response);
            response_len = payload_len;
            return ESP_OK;
        }

        if (!in_frame) {
            continue;
        }
        if (escaped) {
            uint8_t unescaped = 0;
            if (!unescape_byte(byte, unescaped)) {
                return ESP_FAIL;
            }
            rx_payload.push_back(unescaped);
            escaped = false;
        } else if (byte == kShdlcEscape) {
            escaped = true;
        } else {
            rx_payload.push_back(byte);
        }
    }

    return ESP_ERR_TIMEOUT;
}

esp_err_t Sps30Sensor::read_measurement(Sps30Reading &reading)
{
    uint8_t response[kMeasurementPayloadSize] = {};
    size_t response_len = 0;
    ESP_RETURN_ON_ERROR(send_command(kCmdReadMeasurement, nullptr, 0, response, sizeof(response), response_len),
                        TAG, "Failed to read SPS30 measurement");
    if (response_len == 0) {
        return ESP_ERR_NOT_FOUND;
    }
    if (response_len != kMeasurementPayloadSize) {
        return ESP_ERR_INVALID_SIZE;
    }

    reading.mass_pm1_0_ug_m3 = be_float(response + 0);
    reading.mass_pm2_5_ug_m3 = be_float(response + 4);
    reading.mass_pm4_0_ug_m3 = be_float(response + 8);
    reading.mass_pm10_ug_m3 = be_float(response + 12);
    reading.valid = true;
    return ESP_OK;
}
