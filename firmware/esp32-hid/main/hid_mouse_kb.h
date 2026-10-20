#pragma once

#include <stdint.h>
#include <stdbool.h>

// Mouse (relative) — used by iOS AssistiveTouch as a pointer.
// The firmware owns the int8 HID range: it splits a large intent into <=127
// reports, waits for the endpoint to be ready, and leaves 10 ms between reports.
bool hid_mouse_move(int dx, int dy);
// Mouse-based swipe/drag: press, walk to (dx,dy) in paced int8 steps, release.
bool hid_mouse_swipe(int dx, int dy, uint32_t duration_ms);
bool hid_mouse_home(void);    // slam the pointer to the top-left corner
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
