#include "hid_mouse_kb.h"
#include "hid_descriptor.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "class/hid/hid_device.h"
#include "device/usbd.h"

#define KEY_TAP_MS   30
// Absolute pointer: let iOS settle the move before pressing, hold the button long
// enough for a click (not a long press), and leave a short gap after the release.
#define ABS_SETTLE_MS     100
#define ABS_CLICK_HOLD_MS 240  // Ghosthand's documented click hold is 220-260 ms
#define ABS_RELEASE_MS    30
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

//: Relative move with an explicit button state (a drag keeps the left button down).
static bool hid_mouse_move_with_buttons(int dx, int dy, uint8_t buttons)
{
    // The HID report carries int8 deltas. Split here so the host can express the
    // intent once (e.g. move 600 800) without ever wrapping at +/-127.
    for (int reports = 0; reports < 200 && (dx != 0 || dy != 0); reports++) {
        if (!wait_mouse_ready()) {
            return false;
        }
        int step_x = dx > 127 ? 127 : (dx < -127 ? -127 : dx);
        int step_y = dy > 127 ? 127 : (dy < -127 ? -127 : dy);
        if (!tud_hid_n_mouse_report(HID_ITF_MOUSE, HID_REPORT_ID_MOUSE, buttons,
                                    (int8_t) step_x, (int8_t) step_y, 0, 0)) {
            return false;
        }
        dx -= step_x;
        dy -= step_y;
        vTaskDelay(pdMS_TO_TICKS(MOUSE_GAP_MS));
    }
    return dx == 0 && dy == 0;
}

bool hid_mouse_move(int dx, int dy)
{
    return hid_mouse_move_with_buttons(dx, dy, 0);
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

// ---------------------------------------------------------------------------
// Absolute pointer (report ID 2). The payload excludes the report-ID byte,
// which TinyUSB prepends.
// ---------------------------------------------------------------------------
bool hid_mouse_abs_ready(void)
{
    return tud_hid_n_ready(HID_ITF_MOUSE);
}

//: Last absolute position queued to the host (not proof iOS applied it), used so a
//: click report carries the target instead of defaulting X/Y to 0.
static uint16_t g_abs_mouse_x = 0;
static uint16_t g_abs_mouse_y = 0;
static bool g_abs_mouse_position_valid = false;

//: One report, retried while the endpoint is between polls.
static bool hid_mouse_send_report(uint8_t report_id, const void *report, uint16_t report_len)
{
    for (int attempt = 0; attempt < 5; attempt++) {
        if (!wait_mouse_ready()) {
            return false;
        }
        if (tud_hid_n_report(HID_ITF_MOUSE, report_id, report, report_len)) {
            return true;
        }
        vTaskDelay(pdMS_TO_TICKS(5));
    }
    return false;
}

bool hid_mouse_abs_move(uint16_t x, uint16_t y)
{
    if (x > 32767 || y > 32767) {
        return false;  // outside the descriptor's logical range
    }
    hid_mouse_abs_report_t report = {
        .buttons = 0,
        .x = x,
        .y = y,
        .wheel = 0,
    };
    bool sent = hid_mouse_send_report(HID_REPORT_ID_MOUSE_ABS, &report, sizeof(report));
    // A single absolute report can be ignored when the host is between polls ("amove did
    // nothing until retried"); the position is idempotent, so send it once more.
    vTaskDelay(pdMS_TO_TICKS(30));
    bool again = hid_mouse_send_report(HID_REPORT_ID_MOUSE_ABS, &report, sizeof(report));
    if (!(sent || again)) {
        return false;
    }
    g_abs_mouse_x = x;
    g_abs_mouse_y = y;
    g_abs_mouse_position_valid = true;
    return true;
}

//: Button state in both mouse reports, with no movement.
//:
//: iOS can ignore the button bits of an absolute report in a two-collection mouse
//: (Ghosthand measured this on iOS 18): it sends the same press/release in the
//: relative report with zero deltas. The absolute report keeps the *last* position,
//: so a click never snaps the pointer back to (0, 0).
static bool hid_mouse_abs_button(uint8_t buttons)
{
    hid_mouse_abs_report_t abs_report = {
        .buttons = buttons,
        .x = g_abs_mouse_x,
        .y = g_abs_mouse_y,
        .wheel = 0,
    };
    bool abs_ok = hid_mouse_send_report(HID_REPORT_ID_MOUSE_ABS, &abs_report, sizeof(abs_report));
    // The relative mirror is best-effort: this rig's iOS accepts the absolute report's
    // buttons, and right after an absolute report the endpoint can be busy (ready=0)
    // for longer than the wait allows. Its failure must not turn a working click into
    // an error, nor leave the button down (the absolute release is what matters).
    int rel = -1;
    if (wait_mouse_ready()) {
        rel = tud_hid_n_mouse_report(HID_ITF_MOUSE, HID_REPORT_ID_MOUSE, buttons, 0, 0, 0, 0) ? 1 : 0;
    }
    printf("[abs_btn %d] abs=%d rel=%d ready=%d mounted=%d\n",
           buttons, abs_ok, rel, tud_hid_n_ready(HID_ITF_MOUSE), tud_mounted());
    return abs_ok;
}

//: Release both reports, retrying for ~1 s: a click must never leave the button down.
static bool hid_mouse_release_all(void)
{
    for (int attempt = 0; attempt < 20; attempt++) {
        if (hid_mouse_abs_button(0)) {
            return true;
        }
        vTaskDelay(pdMS_TO_TICKS(50));
    }
    return false;
}

bool hid_mouse_abs_click(void)
{
    if (!g_abs_mouse_position_valid) {
        return false;  // a click without a known absolute position would land at 0,0
    }
    // Let the absolute move settle before pressing (Ghosthand: 0-30 ms can lose the
    // click; 60 ms and up was stable).
    vTaskDelay(pdMS_TO_TICKS(ABS_SETTLE_MS));
    if (!hid_mouse_abs_button(MOUSE_BUTTON_LEFT)) {
        hid_mouse_release_all();
        return false;
    }
    vTaskDelay(pdMS_TO_TICKS(ABS_CLICK_HOLD_MS));
    if (!hid_mouse_abs_button(0)) {
        hid_mouse_release_all();
        return false;
    }
    vTaskDelay(pdMS_TO_TICKS(ABS_RELEASE_MS));
    return true;
}

//: Retry a relative button report: right after an absolute report the endpoint can be
//: busy, and a silently dropped press is why mswipe did not hold the button.
static bool hid_mouse_rel_button(uint8_t buttons)
{
    for (int attempt = 0; attempt < 30; attempt++) {
        if (wait_mouse_ready() &&
            tud_hid_n_mouse_report(HID_ITF_MOUSE, HID_REPORT_ID_MOUSE, buttons, 0, 0, 0, 0)) {
            return true;
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    return false;
}

bool hid_mouse_down(void)
{
    return hid_mouse_rel_button(MOUSE_BUTTON_LEFT);
}

bool hid_mouse_up(void)
{
    return hid_mouse_rel_button(0);
}

void hid_mouse_click(void)
{
    if (!hid_mouse_down()) {
        return;  // no press was delivered: do not leave a release-only sequence behind
    }
    vTaskDelay(pdMS_TO_TICKS(KEY_TAP_MS));
    if (!hid_mouse_up()) {
        hid_mouse_up();  // best effort: never leave the button down
    }
    vTaskDelay(pdMS_TO_TICKS(MOUSE_GAP_MS));
}

// iOS does not consume the digitizer, so a "swipe" there is a *drag*: hold the left
// button, move the relative pointer in paced int8 steps, release. The step count is
// derived from the requested duration so a short flick and a slow scroll differ, and the
// button is always released, even if a report is refused.
bool hid_mouse_swipe(int dx, int dy, uint32_t duration_ms)
{
    const int interval_ms = MOUSE_GAP_MS * 2;  // >= the ~10 ms host polling interval
    if (duration_ms < (uint32_t) interval_ms) {
        duration_ms = (uint32_t) interval_ms;
    }
    if (!wait_mouse_ready()) {
        return false;
    }

    // One report per interval, plus one so a single step never has to move both axes far.
    int steps = (int) (duration_ms / (uint32_t) interval_ms);
    if (steps < 1) {
        steps = 1;
    }
    if (steps > 200) {
        steps = 200;
    }

    // iOS ignores the relative report's button bits (measured: a relative `click` does
    // nothing while `aclick` works), so a drag must be pressed through the absolute
    // report - keeping its last position - just like the click. The relative moves keep
    // buttons=1, which is what Ghosthand does for the stroke itself.
    bool abs_buttons = g_abs_mouse_position_valid;
    if (abs_buttons) {
        if (!hid_mouse_abs_button(MOUSE_BUTTON_LEFT)) {
            hid_mouse_abs_button(0);
            return false;
        }
    } else if (!hid_mouse_down()) {
        return false;  // a drag without a held button would just move the pointer
    }
    vTaskDelay(pdMS_TO_TICKS(KEY_TAP_MS));

    int sent_x = 0;
    int sent_y = 0;
    bool ok = true;
    for (int i = 1; i <= steps; i++) {
        int target_x = (int) ((long) dx * i / steps);
        int target_y = (int) ((long) dy * i / steps);
        int step_x = target_x - sent_x;
        int step_y = target_y - sent_y;

        if (step_x != 0 || step_y != 0) {
            // Keep the button down for the whole stroke; the helper splits >127.
            if (!hid_mouse_move_with_buttons(step_x, step_y, MOUSE_BUTTON_LEFT)) {
                ok = false;
                break;
            }
            sent_x = target_x;
            sent_y = target_y;
        }
        vTaskDelay(pdMS_TO_TICKS(interval_ms));
    }

    vTaskDelay(pdMS_TO_TICKS(MOUSE_GAP_MS));
    if (abs_buttons) {
        if (!hid_mouse_abs_button(0)) {
            hid_mouse_abs_button(0);
            ok = false;
        }
    } else if (!hid_mouse_up()) {
        hid_mouse_up();
        ok = false;
    }
    vTaskDelay(pdMS_TO_TICKS(MOUSE_GAP_MS));
    return ok;
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
