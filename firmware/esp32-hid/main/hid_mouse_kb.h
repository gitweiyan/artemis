#pragma once

#include <stdint.h>
#include <stdbool.h>

// Mouse (relative) — used by iOS AssistiveTouch as a pointer.
void hid_mouse_move(int8_t dx, int8_t dy);
void hid_mouse_click(void);   // left button press + release
void hid_mouse_down(void);
void hid_mouse_up(void);
bool hid_mouse_ready(void);

// Keyboard — used by iOS/Android for typing and navigation.
void hid_keyboard_press(uint8_t modifier, uint8_t keycode);
void hid_keyboard_release(void);
void hid_keyboard_tap(uint8_t keycode);            // press + release one key
void hid_keyboard_type(const char *text);          // type letters/digits/space/newline
bool hid_keyboard_ready(void);
