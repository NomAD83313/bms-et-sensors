#pragma once

#include "driver/gpio.h"
#include "driver/uart.h"
#include "esp_err.h"

struct Sps30Reading {
    float mass_pm1_0_ug_m3 = 0.0f;
    float mass_pm2_5_ug_m3 = 0.0f;
    float mass_pm4_0_ug_m3 = 0.0f;
    float mass_pm10_ug_m3 = 0.0f;
    bool valid = false;
};

class Sps30Sensor {
public:
    esp_err_t init(uart_port_t uart_port, gpio_num_t tx_gpio, gpio_num_t rx_gpio);
    esp_err_t read_measurement(Sps30Reading &reading);

private:
    esp_err_t send_command(uint8_t command, const uint8_t *data, size_t data_len, uint8_t *response, size_t response_capacity, size_t &response_len);

    uart_port_t uart_port_ = UART_NUM_1;
};
