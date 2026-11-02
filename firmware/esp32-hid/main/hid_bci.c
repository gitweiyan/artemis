#include "hid_bci.h"

#include "sdkconfig.h"

#if CONFIG_ARTEMIS_HID_BCI_ONLY

#include <math.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "tusb.h"

#include "hid_descriptor.h"

typedef struct __attribute__((packed)) {
    int8_t x;
    int8_t y;
    int8_t z;
} bci_pointer_report_t;

static int8_t axis_from_normalized(int value)
{
    if (value < 0) {
        value = 0;
    } else if (value > 1000) {
        value = 1000;
    }
    // BCI logical range is -127..127; -128 is reserved for "axis absent".
    long mapped = lround((double) value * 254.0 / 1000.0 - 127.0);
    if (mapped < -127) {
        mapped = -127;
    } else if (mapped > 127) {
        mapped = 127;
    }
    return (int8_t) mapped;
}

bool hid_bci_ready(void)
{
    return tud_hid_n_ready(HID_ITF_BCI);
}

bool hid_bci_pointer(int x_norm, int y_norm)
{
    if (!hid_bci_ready()) {
        return false;
    }
    bci_pointer_report_t report = {
        .x = axis_from_normalized(x_norm),
        .y = axis_from_normalized(y_norm),
        .z = -128,  // 2D pointer: z absent
    };
    return tud_hid_n_report(HID_ITF_BCI, HID_REPORT_ID_BCI_POINTER,
                            &report, sizeof(report));
}

bool hid_bci_select(void)
{
    if (!hid_bci_ready()) {
        return false;
    }
    uint8_t buttons[4] = {0x01, 0x00, 0x00, 0x00};  // button 0 = Select
    if (!tud_hid_n_report(HID_ITF_BCI, HID_REPORT_ID_BCI_BUTTON,
                          buttons, sizeof(buttons))) {
        return false;
    }
    vTaskDelay(pdMS_TO_TICKS(30));
    uint8_t released[4] = {0};
    return tud_hid_n_report(HID_ITF_BCI, HID_REPORT_ID_BCI_BUTTON,
                            released, sizeof(released));
}

#endif  // CONFIG_ARTEMIS_HID_BCI_ONLY
