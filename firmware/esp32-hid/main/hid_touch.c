#include "hid_touch.h"
#include "hid_descriptor.h"
#include "sdkconfig.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "class/hid/hid_device.h"

#if CONFIG_ARTEMIS_HID_INCLUDE_TOUCH

#define TAP_DOWN_MS     30
#define DRAG_HOLD_MS    200
#define SWIPE_STEPS     20
#define MIN_GESTURE_MS  10

// ---------------------------------------------------------------------------
// Coordinate normalization: phone pixel -> HID logical 0..65535.
// ---------------------------------------------------------------------------
static uint16_t norm_x(uint16_t x)
{
    uint32_t v = (uint32_t) x * 65535u / TOUCH_REF_WIDTH;
    return (uint16_t) (v > 65535u ? 65535u : v);
}

static uint16_t norm_y(uint16_t y)
{
    uint32_t v = (uint32_t) y * 65535u / TOUCH_REF_HEIGHT;
    return (uint16_t) (v > 65535u ? 65535u : v);
}

// ---------------------------------------------------------------------------
// Send one raw report. `count` is the number of active contacts (0 or 1).
// ---------------------------------------------------------------------------
static bool send_report(bool tip, bool in_range, uint16_t x, uint16_t y,
                        uint8_t count)
{
    if (!tud_hid_n_ready(HID_ITF_TOUCH)) {
        return false;
    }
    hid_touch_report_t r = {
        .flags = 0,
        .contact_id = 0,
        .x = x,
        .y = y,
        .contact_count = count,
    };
    if (tip) {
        r.flags |= 0x01;
    }
    if (in_range) {
        r.flags |= 0x02;
    }
    return tud_hid_n_report(HID_ITF_TOUCH, HID_REPORT_ID_TOUCH, &r, sizeof(r));
}

// Send MOVE reports from (hx1,hy1) to (hx2,hy2); caller is already touching.
static void move_steps(uint16_t hx1, uint16_t hy1, uint16_t hx2, uint16_t hy2,
                       uint32_t duration_ms)
{
    int32_t dx = (int32_t) hx2 - hx1;
    int32_t dy = (int32_t) hy2 - hy1;
    uint32_t step_ms = duration_ms / SWIPE_STEPS;
    if (step_ms == 0) {
        step_ms = 1;
    }
    for (int i = 1; i <= SWIPE_STEPS; i++) {
        uint16_t x = (uint16_t) (hx1 + dx * i / SWIPE_STEPS);
        uint16_t y = (uint16_t) (hy1 + dy * i / SWIPE_STEPS);
        send_report(true, true, x, y, 1);
        vTaskDelay(pdMS_TO_TICKS(step_ms));
    }
}

// ---------------------------------------------------------------------------
// Public API.
// ---------------------------------------------------------------------------
void hid_touch_init(void)
{
    // Normalization constants are compile-time; nothing to do.
}

bool hid_touch_ready(void)
{
    return tud_hid_n_ready(HID_ITF_TOUCH);
}

void hid_touch_tap(uint16_t x, uint16_t y)
{
    uint16_t hx = norm_x(x), hy = norm_y(y);
    send_report(true, true, hx, hy, 1);
    vTaskDelay(pdMS_TO_TICKS(TAP_DOWN_MS));
    send_report(false, false, hx, hy, 0);
}

void hid_touch_long_press(uint16_t x, uint16_t y, uint32_t duration_ms)
{
    uint16_t hx = norm_x(x), hy = norm_y(y);
    send_report(true, true, hx, hy, 1);
    vTaskDelay(pdMS_TO_TICKS(duration_ms));
    send_report(false, false, hx, hy, 0);
}

void hid_touch_swipe(uint16_t x1, uint16_t y1, uint16_t x2, uint16_t y2,
                     uint32_t duration_ms)
{
    uint16_t hx1 = norm_x(x1), hy1 = norm_y(y1);
    uint16_t hx2 = norm_x(x2), hy2 = norm_y(y2);
    if (duration_ms < MIN_GESTURE_MS) {
        duration_ms = MIN_GESTURE_MS;
    }

    send_report(true, true, hx1, hy1, 1);      // DOWN
    vTaskDelay(pdMS_TO_TICKS(MIN_GESTURE_MS));
    move_steps(hx1, hy1, hx2, hy2, duration_ms);
    send_report(false, false, hx2, hy2, 0);    // UP
    vTaskDelay(pdMS_TO_TICKS(MIN_GESTURE_MS));
}

void hid_touch_drag(uint16_t x1, uint16_t y1, uint16_t x2, uint16_t y2,
                    uint32_t duration_ms)
{
    uint16_t hx1 = norm_x(x1), hy1 = norm_y(y1);
    uint16_t hx2 = norm_x(x2), hy2 = norm_y(y2);
    if (duration_ms < MIN_GESTURE_MS) {
        duration_ms = MIN_GESTURE_MS;
    }

    send_report(true, true, hx1, hy1, 1);      // DOWN
    vTaskDelay(pdMS_TO_TICKS(DRAG_HOLD_MS));   // long-press first
    move_steps(hx1, hy1, hx2, hy2, duration_ms);
    send_report(false, false, hx2, hy2, 0);    // UP
    vTaskDelay(pdMS_TO_TICKS(MIN_GESTURE_MS));
}

#else // CONFIG_ARTEMIS_HID_INCLUDE_TOUCH

// iOS build: the digitizer interface is not exposed. The console still parses
// the touch commands so a script can share one command set, but they are no-ops.
bool hid_touch_ready(void)
{
    return false;
}

void hid_touch_init(void)
{
}

void hid_touch_tap(uint16_t x, uint16_t y)
{
    (void) x;
    (void) y;
}

void hid_touch_long_press(uint16_t x, uint16_t y, uint32_t duration_ms)
{
    (void) x;
    (void) y;
    (void) duration_ms;
}

void hid_touch_swipe(uint16_t x1, uint16_t y1, uint16_t x2, uint16_t y2,
                     uint32_t duration_ms)
{
    (void) x1;
    (void) y1;
    (void) x2;
    (void) y2;
    (void) duration_ms;
}

void hid_touch_drag(uint16_t x1, uint16_t y1, uint16_t x2, uint16_t y2,
                    uint32_t duration_ms)
{
    (void) x1;
    (void) y1;
    (void) x2;
    (void) y2;
    (void) duration_ms;
}

#endif // CONFIG_ARTEMIS_HID_INCLUDE_TOUCH
