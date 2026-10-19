#pragma once

#include <stdint.h>
#include <stdbool.h>

// Reference resolution. Commands are given in "phone pixels" of this virtual
// screen and normalized to the HID logical range 0..65535. Only the aspect
// ratio matters (it must match the target phone); the phone's real resolution
// is irrelevant because Android maps 0..65535 across the full screen.
#define TOUCH_REF_WIDTH  1080
#define TOUCH_REF_HEIGHT 2400

void hid_touch_init(void);

// True when the HID interface is mounted and ready to send reports.
bool hid_touch_ready(void);

void hid_touch_tap(uint16_t x, uint16_t y);
void hid_touch_long_press(uint16_t x, uint16_t y, uint32_t duration_ms);
void hid_touch_swipe(uint16_t x1, uint16_t y1, uint16_t x2, uint16_t y2,
                     uint32_t duration_ms);
void hid_touch_drag(uint16_t x1, uint16_t y1, uint16_t x2, uint16_t y2,
                    uint32_t duration_ms);
