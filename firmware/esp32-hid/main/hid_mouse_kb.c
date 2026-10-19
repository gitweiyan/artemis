#include "hid_mouse_kb.h"
#include "hid_descriptor.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "class/hid/hid_device.h"

#define KEY_TAP_MS   30
// HID interrupt endpoints use a 10 ms polling interval. Leave enough time for
// the host to receive each key-down before sending the key-up report.
#define TYPE_GAP_MS  20

// ---------------------------------------------------------------------------
// Mouse (relative pointer). iOS maps this to the AssistiveTouch pointer.
// ---------------------------------------------------------------------------
bool hid_mouse_ready(void)
{
    return tud_hid_n_ready(HID_ITF_MOUSE);
}

void hid_mouse_move(int8_t dx, int8_t dy)
{
    tud_hid_n_mouse_report(HID_ITF_MOUSE, HID_REPORT_ID_MOUSE, 0, dx, dy, 0, 0);
}

void hid_mouse_down(void)
{
    tud_hid_n_mouse_report(HID_ITF_MOUSE, HID_REPORT_ID_MOUSE, MOUSE_BUTTON_LEFT, 0, 0, 0, 0);
}

void hid_mouse_up(void)
{
    tud_hid_n_mouse_report(HID_ITF_MOUSE, HID_REPORT_ID_MOUSE, 0, 0, 0, 0, 0);
}

void hid_mouse_click(void)
{
    hid_mouse_down();
    vTaskDelay(pdMS_TO_TICKS(KEY_TAP_MS));
    hid_mouse_up();
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
    uint8_t keycode_arr[6] = {keycode, 0, 0, 0, 0, 0};
    tud_hid_n_keyboard_report(HID_ITF_KEYBOARD, HID_REPORT_ID_KEYBOARD,
                              modifier, keycode_arr);
}

void hid_keyboard_release(void)
{
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
