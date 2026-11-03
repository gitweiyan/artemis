#include "hid_descriptor.h"

#include "tinyusb.h"
#include "class/hid/hid_device.h"

// ---------------------------------------------------------------------------
// HID report descriptors: one per interface.
// ---------------------------------------------------------------------------

#if CONFIG_ARTEMIS_HID_INCLUDE_TOUCH
// Interface 0 — Touchscreen (single-touch digitizer), Report ID 1.
//
// Report layout (after the Report ID byte, which TinyUSB prepends):
//   bit 0      = Tip Switch
//   bit 1      = In Range
//   bits 2..7  = padding
//   byte 1     = Contact Identifier
//   bytes 2..3 = X (16-bit, logical range 0..65535)
//   bytes 4..5 = Y (16-bit, logical range 0..65535)
//   byte 6     = Contact Count
const uint8_t hid_report_descriptor_touch[] = {
    0x05, 0x0D,              //   Usage Page (Digitizers)
    0x09, 0x04,              //   Usage (Touch Screen)
    0xA1, 0x01,              //   Collection (Application)
    0x85, HID_REPORT_ID_TOUCH,  //  Report ID (1)
    0x09, 0x22,              //     Usage (Finger)
    0xA1, 0x02,              //     Collection (Logical)
    0x09, 0x42,              //       Usage (Tip Switch)
    0x15, 0x00,              //       Logical Minimum (0)
    0x25, 0x01,              //       Logical Maximum (1)
    0x75, 0x01,              //       Report Size (1)
    0x95, 0x01,              //       Report Count (1)
    0x81, 0x02,              //       Input (Data,Var,Abs) -> tip switch
    0x09, 0x32,              //       Usage (In Range)
    0x81, 0x02,              //       Input (Data,Var,Abs) -> in range
    0x95, 0x06,              //       Report Count (6)
    0x81, 0x03,              //       Input (Const,Var,Abs) -> padding
    0x75, 0x08,              //       Report Size (8)
    0x09, 0x51,              //       Usage (Contact Identifier)
    0x95, 0x01,              //       Report Count (1)
    0x81, 0x02,              //       Input (Data,Var,Abs) -> contact id
    0x05, 0x01,              //       Usage Page (Generic Desktop)
    0x09, 0x30,              //       Usage (X)
    0x09, 0x31,              //       Usage (Y)
    0x16, 0x00, 0x00,        //       Logical Minimum (0)
    0x26, 0xFF, 0xFF,        //       Logical Maximum (65535)
    0x75, 0x10,              //       Report Size (16)
    0x95, 0x02,              //       Report Count (2)
    0x81, 0x02,              //       Input (Data,Var,Abs) -> X, Y
    0x05, 0x0D,              //       Usage Page (Digitizers)
    0x09, 0x54,              //       Usage (Contact Count)
    0x25, 0x0A,              //       Logical Maximum (10)
    0x75, 0x08,              //       Report Size (8)
    0x95, 0x01,              //       Report Count (1)
    0x81, 0x02,              //       Input (Data,Var,Abs) -> contact count
    0xC0,                    //     End Collection
    0xC0,                    //   End Collection
};
#endif // CONFIG_ARTEMIS_HID_INCLUDE_TOUCH

// Mouse (relative boot mouse), no Report ID.
const uint8_t hid_report_descriptor_mouse[] = {
    TUD_HID_REPORT_DESC_MOUSE()
};

// Keyboard (boot keyboard), no Report ID.
const uint8_t hid_report_descriptor_keyboard[] = {
    TUD_HID_REPORT_DESC_KEYBOARD()
};

#if CONFIG_ARTEMIS_HID_BCI_ONLY
// BCI-only spike: full Apple BCI report collection, not just a pointer collection.
// Report 1 signal quality, Report 2 32 buttons, Report 3 absolute 2D pointer,
// Report 4 input item selection + output scan information.
const uint8_t hid_report_descriptor_bci[] = {
    0x05, 0x60,              // Usage Page (Brain Control Interface)
    0x09, 0x01,              // Usage (BCI Application)
    0xA1, 0x01,              // Collection (Application)

    // Report ID 1: signal quality (button id, neural strength).
    0x05, 0x60,              //   Usage Page (BCI)
    0x09, 0x02,              //   Usage (Signal Quality)
    0x85, 0x01,              //   Report ID (1)
    0xA1, 0x02,              //   Collection (Logical)
    0x09, 0x02,              //     Usage (Signal Quality)
    0x15, 0x00,              //     Logical Minimum (0)
    0x26, 0xFF, 0x00,        //     Logical Maximum (255)
    0x75, 0x08,              //     Report Size (8)
    0x95, 0x02,              //     Report Count (2)
    0x81, 0x06,              //     Input (Data,Var,Rel)
    0xC0,                    //   End Collection

    // Report ID 2: 32 buttons (button 0 = Select).
    0x05, 0x09,              //   Usage Page (Button)
    0x19, 0x01,              //   Usage Minimum (1)
    0x29, 0x20,              //   Usage Maximum (32)
    0x15, 0x00,              //   Logical Minimum (0)
    0x25, 0x01,              //   Logical Maximum (1)
    0x75, 0x01,              //   Report Size (1)
    0x95, 0x20,              //   Report Count (32)
    0x85, HID_REPORT_ID_BCI_BUTTON,
    0x81, 0x02,              //   Input (Data,Var,Abs)

    // Report ID 3: absolute 2D pointer (x, y valid; z absent = -128).
    0x05, 0x01,              //   Usage Page (Generic Desktop)
    0x09, 0x01,              //   Usage (Pointer)
    0x85, HID_REPORT_ID_BCI_POINTER,
    0xA1, 0x00,              //   Collection (Physical)
    0x09, 0x30,              //     Usage (X)
    0x09, 0x31,              //     Usage (Y)
    0x09, 0x32,              //     Usage (Z)
    0x15, 0x81,              //     Logical Minimum (-127)
    0x25, 0x7F,              //     Logical Maximum (127)
    0x75, 0x08,              //     Report Size (8)
    0x95, 0x03,              //     Report Count (3)
    0x81, 0x02,              //     Input (Data,Var,Abs) absolute
    0xC0,                    //   End Collection

    // Report ID 4: input item selection.
    0x05, 0x60,              //   Usage Page (BCI)
    0x09, 0x04,              //   Usage (Item Selection)
    0x85, 0x04,              //   Report ID (4)
    0xA1, 0x02,              //   Collection (Logical)
    0x09, 0x04,              //     Usage (Item Selection)
    0x15, 0x00,              //     Logical Minimum (0)
    0x26, 0xFF, 0x00,        //     Logical Maximum (255)
    0x75, 0x08,              //     Report Size (8)
    0x95, 0x01,              //     Report Count (1)
    0x81, 0x06,              //     Input (Data,Var,Rel)
    0xC0,                    //   End Collection

    // Report ID 4: output scan information (6 bytes).
    0x05, 0x60,              //   Usage Page (BCI)
    0x85, 0x04,              //   Report ID (4)
    0xA1, 0x02,              //   Collection (Logical)
    0x09, 0x03,              //     Usage (Number of Items)
    0x15, 0x00,              //     Logical Minimum (0)
    0x26, 0xFF, 0x00,        //     Logical Maximum (255)
    0x75, 0x08,              //     Report Size (8)
    0x95, 0x06,              //     Report Count (6)
    0x91, 0x03,              //     Output (Const,Var,Abs)
    0xC0,                    //   End Collection

    0xC0,                    // End Collection
};
#endif // CONFIG_ARTEMIS_HID_BCI_ONLY

// ---------------------------------------------------------------------------
// String descriptors (index 0 = supported languages).
// ---------------------------------------------------------------------------
const char *hid_string_descriptor[HID_STRING_COUNT] = {
    (char[]){0x09, 0x04},    // 0: English (0x0409)
    "MobileAgent",           // 1: Manufacturer
#if CONFIG_ARTEMIS_HID_INCLUDE_TOUCH
    "HID Touch Mouse KB",    // 2: Product (Android / composite build)
#else
    "HID Keyboard Mouse",    // 2: Product (iOS build)
#endif
    "000001",                // 3: Serial
    "HID interface",         // 4: HID interface
};

// ---------------------------------------------------------------------------
// Configuration descriptor: 1 configuration, HID_HID_COUNT interfaces.
// ---------------------------------------------------------------------------
#define TUSB_DESC_TOTAL_LEN (TUD_CONFIG_DESC_LEN + HID_HID_COUNT * TUD_HID_DESC_LEN)

const uint8_t hid_configuration_descriptor[] = {
    TUD_CONFIG_DESCRIPTOR(1, HID_HID_COUNT, 0, TUSB_DESC_TOTAL_LEN,
                          TUSB_DESC_CONFIG_ATT_REMOTE_WAKEUP, 100),
#if CONFIG_ARTEMIS_HID_BCI_ONLY
    // interface 0: BCI absolute pointer + button report
    TUD_HID_DESCRIPTOR(HID_ITF_BCI, 4, HID_ITF_PROTOCOL_NONE,
                       sizeof(hid_report_descriptor_bci), 0x81, 16, 10),
#elif CONFIG_ARTEMIS_HID_INCLUDE_TOUCH
    // interface 0: touchscreen (digitizer)
    TUD_HID_DESCRIPTOR(HID_ITF_TOUCH, 4, HID_ITF_PROTOCOL_NONE,
                       sizeof(hid_report_descriptor_touch), 0x81, 16, 10),
#endif
#if !CONFIG_ARTEMIS_HID_BCI_ONLY
    // mouse (boot protocol)
    TUD_HID_DESCRIPTOR(HID_ITF_MOUSE, 4, HID_ITF_PROTOCOL_MOUSE,
                       sizeof(hid_report_descriptor_mouse), 0x82, 16, 10),
    // keyboard (boot protocol)
    TUD_HID_DESCRIPTOR(HID_ITF_KEYBOARD, 4, HID_ITF_PROTOCOL_KEYBOARD,
                       sizeof(hid_report_descriptor_keyboard), 0x83, 16, 10),
#endif
};

// ---------------------------------------------------------------------------
// TinyUSB HID callbacks.
// ---------------------------------------------------------------------------
uint8_t const *tud_hid_descriptor_report_cb(uint8_t instance)
{
    switch (instance) {
#if CONFIG_ARTEMIS_HID_BCI_ONLY
    case HID_ITF_BCI:
        return hid_report_descriptor_bci;
#else
#if CONFIG_ARTEMIS_HID_INCLUDE_TOUCH
    case HID_ITF_TOUCH:
        return hid_report_descriptor_touch;
#endif
    case HID_ITF_MOUSE:
        return hid_report_descriptor_mouse;
    case HID_ITF_KEYBOARD:
        return hid_report_descriptor_keyboard;
#endif
    default:
        return NULL;
    }
}

uint16_t tud_hid_get_report_cb(uint8_t instance, uint8_t report_id,
                               hid_report_type_t report_type, uint8_t *buffer,
                               uint16_t reqlen)
{
    (void) instance;
    (void) report_id;
    (void) report_type;
    (void) buffer;
    (void) reqlen;
    return 0;
}

void tud_hid_set_report_cb(uint8_t instance, uint8_t report_id,
                           hid_report_type_t report_type,
                           uint8_t const *buffer, uint16_t bufsize)
{
    (void) instance;
    (void) report_id;
    (void) report_type;
    (void) buffer;
    (void) bufsize;
}
