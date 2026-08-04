#ifndef WS_TCA9554PWR_H
#define WS_TCA9554PWR_H

#include <stdint.h>

#include "driver/i2c.h"
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define TCA9554_EXIO1 0x01
#define TCA9554_EXIO2 0x02
#define TCA9554_EXIO3 0x03
#define TCA9554_EXIO4 0x04
#define TCA9554_EXIO5 0x05
#define TCA9554_EXIO6 0x06
#define TCA9554_EXIO7 0x07

#define I2C_MASTER_SDA_IO     22
#define I2C_MASTER_SCL_IO     23
#define I2C_MASTER_NUM        0
#define I2C_MASTER_FREQ_HZ    400000
#define I2C_MASTER_TIMEOUT_MS 1000

#define TCA9554_ADDRESS      0x20
#define TCA9554_INPUT_REG    0x00
#define TCA9554_OUTPUT_REG   0x01
#define TCA9554_POLARITY_REG 0x02
#define TCA9554_CONFIG_REG   0x03

esp_err_t i2c_master_init(void);

uint8_t Read_REG(uint8_t reg);
void Write_REG(uint8_t reg, uint8_t data);

void Mode_EXIO(uint8_t pin, uint8_t state);
void Mode_EXIOS(uint8_t pin_state);

uint8_t Read_EXIO(uint8_t pin);
uint8_t Read_EXIOS(void);

void Set_EXIO(uint8_t pin, uint8_t state);
void Set_EXIOS(uint8_t pin_state);
void Set_Toggle(uint8_t pin);

void TCA9554PWR_Init(uint8_t pin_state);

#ifdef __cplusplus
}
#endif

#endif
