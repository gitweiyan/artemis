#pragma once

#include <stdint.h>

#include "sdkconfig.h"

// HID interface instances (order of interfaces in the config descriptor).
// iOS does not support external HID digitizers; an iOS-only build can omit
// the touchscreen interface entirely (CONFIG_ARTEMIS_HID_INCLUDE_TOUCH=n).
#if CONFIG_ARTEMIS_HID_BCI_ONLY
#define HID_ITF_BCI      0
// Compatibility aliases so the legacy mouse/keyboard sources still compile.
// BCI-only commands reject move/key/click, so these are never used at runtime.
#define HID_ITF_TOUCH    0
#define HID_ITF_MOUSE    0
#define HID_ITF_KEYBOARD 0
#define HID_HID_COUNT    1
#define HID_MODE_NAME    "bci"
#elif CONFIG_ARTEMIS_HID_INCLUDE_TOUCH
#define HID_ITF_TOUCH    0
#define HID_ITF_MOUSE    1
#define HID_ITF_KEYBOARD 2
#define HID_HID_COUNT    3
#define HID_MODE_NAME    "composite"
#else
#define HID_ITF_MOUSE    0
#define HID_ITF_KEYBOARD 1
#define HID_HID_COUNT    2
#define HID_MODE_NAME    "ios"
#endif

// Report IDs (one per interface). Keyboard and mouse are boot interfaces and
// use report ID 0 (no report-ID prefix); only the digitizer needs an ID.
#define HID_REPORT_ID_TOUCH    1
#define HID_REPORT_ID_MOUSE    0
//: Absolute-pointer report on the same mouse interface (second collection).
#define HID_REPORT_ID_MOUSE_ABS 2
#define HID_REPORT_ID_KEYBOARD 0

// BCI report IDs (Apple BCI HID reference).
#define HID_REPORT_ID_BCI_BUTTON  2
#define HID_REPORT_ID_BCI_POINTER 3

// BCI pointer payload: x, y, z as signed 8-bit values.
// -128 means "this axis is absent"; 2D reports send z = -128.
#define HID_BCI_POINTER_LEN 3

// Number of entries in hid_string_descriptor.
#define HID_STRING_COUNT 5

// Payload sent to the host for the touchscreen interface. The report ID byte
// is prepended automatically by tud_hid_n_report(), so this struct is only the
// payload (7 bytes):
//   byte 0: flags        (bit0 = tip switch, bit1 = in range, bits 2..7 padding)
//   byte 1: contact id
//   byte 2..3: X         (uint16 little-endian)
//   byte 4..5: Y         (uint16 little-endian)
//   byte 6: contact count
#define HID_TOUCH_REPORT_LEN 7

typedef struct __attribute__((packed)) {
    uint8_t  flags;
    uint8_t  contact_id;
    uint16_t x;
    uint16_t y;
    uint8_t  contact_count;
} hid_touch_report_t;

// Defined in hid_descriptor.c
extern const uint8_t hid_configuration_descriptor[];
extern const char *hid_string_descriptor[HID_STRING_COUNT];
