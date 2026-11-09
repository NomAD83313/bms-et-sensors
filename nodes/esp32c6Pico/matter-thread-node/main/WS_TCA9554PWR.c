#include "WS_TCA9554PWR.h"

#include <stdbool.h>

static esp_err_t i2c_write_reg(uint8_t reg, uint8_t data)
{
    i2c_cmd_handle_t cmd = i2c_cmd_link_create();
    i2c_master_start(cmd);
    i2c_master_write_byte(cmd, (TCA9554_ADDRESS << 1) | I2C_MASTER_WRITE, true);
    i2c_master_write_byte(cmd, reg, true);
    i2c_master_write_byte(cmd, data, true);
    i2c_master_stop(cmd);
    esp_err_t ret = i2c_master_cmd_begin(I2C_MASTER_NUM, cmd, I2C_MASTER_TIMEOUT_MS / portTICK_PERIOD_MS);
    i2c_cmd_link_delete(cmd);
    return ret;
}

static esp_err_t i2c_read_reg(uint8_t reg, uint8_t *data)
{
    i2c_cmd_handle_t cmd = i2c_cmd_link_create();
    i2c_master_start(cmd);
    i2c_master_write_byte(cmd, (TCA9554_ADDRESS << 1) | I2C_MASTER_WRITE, true);
    i2c_master_write_byte(cmd, reg, true);
    i2c_master_start(cmd);
    i2c_master_write_byte(cmd, (TCA9554_ADDRESS << 1) | I2C_MASTER_READ, true);
    i2c_master_read_byte(cmd, data, I2C_MASTER_NACK);
    i2c_master_stop(cmd);
    esp_err_t ret = i2c_master_cmd_begin(I2C_MASTER_NUM, cmd, I2C_MASTER_TIMEOUT_MS / portTICK_PERIOD_MS);
    i2c_cmd_link_delete(cmd);
    return ret;
}

esp_err_t i2c_master_init(void)
{
    i2c_config_t conf = {
        .mode = I2C_MODE_MASTER,
        .sda_io_num = I2C_MASTER_SDA_IO,
        .scl_io_num = I2C_MASTER_SCL_IO,
        .sda_pullup_en = GPIO_PULLUP_ENABLE,
        .scl_pullup_en = GPIO_PULLUP_ENABLE,
        .master.clk_speed = I2C_MASTER_FREQ_HZ,
    };

    esp_err_t ret = i2c_param_config(I2C_MASTER_NUM, &conf);
    if (ret != ESP_OK) {
        return ret;
    }

    ret = i2c_driver_install(I2C_MASTER_NUM, conf.mode, 0, 0, 0);
    if (ret == ESP_ERR_INVALID_STATE) {
        return ESP_OK;
    }
    return ret;
}

uint8_t Read_REG(uint8_t reg)
{
    uint8_t value = 0;
    if (i2c_read_reg(reg, &value) != ESP_OK) {
        return 0;
    }
    return value;
}

void Write_REG(uint8_t reg, uint8_t data)
{
    i2c_write_reg(reg, data);
}

void Mode_EXIO(uint8_t pin, uint8_t state)
{
    if (pin == 0 || pin > 7) {
        return;
    }
    uint8_t bits = Read_REG(TCA9554_CONFIG_REG);
    uint8_t mask = (uint8_t)(0x01u << (pin - 1));
    uint8_t next = state ? (uint8_t)(bits | mask) : (uint8_t)(bits & (uint8_t)~mask);
    Write_REG(TCA9554_CONFIG_REG, next);
}

void Mode_EXIOS(uint8_t pin_state)
{
    Write_REG(TCA9554_CONFIG_REG, pin_state);
}

uint8_t Read_EXIO(uint8_t pin)
{
    if (pin == 0 || pin > 7) {
        return 0;
    }
    uint8_t bits = Read_REG(TCA9554_INPUT_REG);
    return (uint8_t)((bits >> (pin - 1)) & 0x01u);
}

uint8_t Read_EXIOS(void)
{
    return Read_REG(TCA9554_INPUT_REG);
}

void Set_EXIO(uint8_t pin, uint8_t state)
{
    if (pin == 0 || pin > 7 || state > 1) {
        return;
    }
    uint8_t bits = Read_REG(TCA9554_OUTPUT_REG);
    uint8_t mask = (uint8_t)(0x01u << (pin - 1));
    uint8_t next = state ? (uint8_t)(bits | mask) : (uint8_t)(bits & (uint8_t)~mask);
    Write_REG(TCA9554_OUTPUT_REG, next);
}

void Set_EXIOS(uint8_t pin_state)
{
    Write_REG(TCA9554_OUTPUT_REG, pin_state);
}

void Set_Toggle(uint8_t pin)
{
    Set_EXIO(pin, (uint8_t)(!Read_EXIO(pin)));
}

void TCA9554PWR_Init(uint8_t pin_state)
{
    if (i2c_master_init() != ESP_OK) {
        return;
    }
    Mode_EXIOS(pin_state);
}
