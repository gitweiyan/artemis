#include "hid_mouse_kb.h"
#include "hid_descriptor.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "class/hid/hid_device.h"

#define KEY_TAP_MS   30
// HID interrupt endpoints use a 10 ms polling interval. Leave enough time for
// the host to receive each key-down before sending the key-up report.
#define TYPE_GAP_MS  20
#define MOUSE_GAP_MS 10
#define READY_TIMEOUT_MS 200
#define MOUSE_HOME_STEPS 30

static bool wait_mouse_ready(void)
{
    for (int waited = 0; waited < READY_TIMEOUT_MS; waited += 5) {
        if (tud_hid_n_ready(HID_ITF_MOUSE)) {
            return true;
        }
        vTaskDelay(pdMS_TO_TICKS(5));
    }
    return false;
}

static bool wait_keyboard_ready(void)
{
    for (int waited = 0; waited < READY_TIMEOUT_MS; waited += 5) {
        if (tud_hid_n_ready(HID_ITF_KEYBOARD)) {
            return true;
        }
        vTaskDelay(pdMS_TO_TICKS(5));
    }
    return false;
}

// ---------------------------------------------------------------------------
// Mouse (relative pointer). iOS maps this to the AssistiveTouch pointer.
// ---------------------------------------------------------------------------
bool hid_mouse_ready(void)
{
    return tud_hid_n_ready(HID_ITF_MOUSE);
}

bool hid_mouse_move(int dx, int dy)
{
    // The HID report carries int8 deltas. Split here so the host can express the
    // intent once (e.g. move 600 800) without ever wrapping at +/-127.
    for (int reports = 0; reports < 200 && (dx != 0 || dy != 0); reports++) {
        if (!wait_mouse_ready()) {
            return false;
        }
        int step_x = dx > 127 ? 127 : (dx < -127 ? -127 : dx);
        int step_y = dy > 127 ? 127 : (dy < -127 ? -127 : dy);
        if (!tud_hid_n_mouse_report(HID_ITF_MOUSE, HID_REPORT_ID_MOUSE, 0,
                                    (int8_t) step_x, (int8_t) step_y, 0, 0)) {
            return false;
        }
        dx -= step_x;
        dy -= step_y;
        vTaskDelay(pdMS_TO_TICKS(MOUSE_GAP_MS));
    }
    return dx == 0 && dy == 0;
}

bool hid_mouse_home(void)
{
    for (int i = 0; i < MOUSE_HOME_STEPS; i++) {
        if (!hid_mouse_move(-127, -127)) {
            return false;
        }
    }
    return true;
}

void hid_mouse_down(void)
{
    if (!wait_mouse_ready()) {
        return;
    }
    tud_hid_n_mouse_report(HID_ITF_MOUSE, HID_REPORT_ID_MOUSE, MOUSE_BUTTON_LEFT, 0, 0, 0, 0);
}

void hid_mouse_up(void)
{
    if (!wait_mouse_ready()) {
        return;
    }
    tud_hid_n_mouse_report(HID_ITF_MOUSE, HID_REPORT_ID_MOUSE, 0, 0, 0, 0, 0);
}

void hid_mouse_click(void)
{
    hid_mouse_down();
    vTaskDelay(pdMS_TO_TICKS(KEY_TAP_MS));
    hid_mouse_up();
    vTaskDelay(pdMS_TO_TICKS(MOUSE_GAP_MS));
}

// ---------------------------------------------------------------------------
// Keyboard.
// ---------------------------------------------------------------------------
bool hid_keyboard_ready(void)
{
    return tud_hid_n_ready(HID_ITF_KEYBOARD);
}

void hid_keyboard_press(uint8_t modifier, uint8_t keycode)
{
    if (!wait_keyboard_ready()) {
        return;
    }
    uint8_t keycode_arr[6] = {keycode, 0, 0, 0, 0, 0};
    tud_hid_n_keyboard_report(HID_ITF_KEYBOARD, HID_REPORT_ID_KEYBOARD,
                              modifier, keycode_arr);
}

void hid_keyboard_release(void)
{
    if (!wait_keyboard_ready()) {
        return;
    }
    uint8_t keycode_arr[6] = {0};
    tud_hid_n_keyboard_report(HID_ITF_KEYBOARD, HID_REPORT_ID_KEYBOARD,
                              0, keycode_arr);
}

void hid_keyboard_tap(uint8_t keycode)
{
    hid_keyboard_press(0, keycode);
    vTaskDelay(pdMS_TO_TICKS(KEY_TAP_MS));
    hid_keyboard_release();
}

// Map printable ASCII to (modifier, HID usage). Covers letters, digits,
// space and newline — enough for basic typing in a demo.
static bool ascii_to_key(uint8_t *modifier, uint8_t *keycode, char c)
{
    if (c >= 'a' && c <= 'z') {
        *modifier = 0;
        *keycode = HID_KEY_A + (uint8_t)(c - 'a');
        return true;
    }
    if (c >= 'A' && c <= 'Z') {
        *modifier = KEYBOARD_MODIFIER_LEFTSHIFT;
        *keycode = HID_KEY_A + (uint8_t)(c - 'A');
        return true;
    }
    if (c >= '1' && c <= '9') {
        *modifier = 0;
        *keycode = HID_KEY_1 + (uint8_t)(c - '1');
        return true;
    }
    if (c == '0') {
        *modifier = 0;
        *keycode = HID_KEY_0;
        return true;
    }
    if (c == ' ') {
        *modifier = 0;
        *keycode = HID_KEY_SPACE;
        return true;
    }
    if (c == '\n' || c == '\r') {
        *modifier = 0;
        *keycode = HID_KEY_ENTER;
        return true;
    }
    return false;
}

void hid_keyboard_type(const char *text)
{
    while (*text) {
        uint8_t modifier = 0, keycode = 0;
        if (ascii_to_key(&modifier, &keycode, *text)) {
            hid_keyboard_press(modifier, keycode);
            vTaskDelay(pdMS_TO_TICKS(TYPE_GAP_MS));
            hid_keyboard_release();
            vTaskDelay(pdMS_TO_TICKS(TYPE_GAP_MS));
        }
        text++;
    }
}
