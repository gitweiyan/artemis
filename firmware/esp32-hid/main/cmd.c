#include "cmd.h"
#include "hid_descriptor.h"
#include "hid_touch.h"
#include "hid_mouse_kb.h"
#include "sdkconfig.h"
#include "tusb.h"

#include <stdio.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static void print_help(void)
{
    printf("\n");
#if CONFIG_ARTEMIS_HID_INCLUDE_TOUCH
    printf("Commands (touch coords in phone pixels of %dx%d):\n"
           "  tap        <x> <y>            # touchscreen: tap\n"
           "  long_press <x> <y> <ms>       # touchscreen: long press\n"
           "  swipe      <x1> <y1> <x2> <y2> <ms>  # touchscreen: swipe\n"
           "  drag       <x1> <y1> <x2> <y2> <ms>  # touchscreen: drag\n",
           TOUCH_REF_WIDTH, TOUCH_REF_HEIGHT);
#else
    printf("Commands (iOS build: keyboard + mouse; touchscreen disabled):\n");
#endif
    printf("  mswipe <dx> <dy> [ms]          # mouse: press, move, release (iOS swipe/drag)\n"
           "  home                           # mouse: slam pointer to top-left\n"
           "  move       <dx> <dy>          # mouse: relative cursor move\n"
           "  click                          # mouse: left click\n"
           "  mdown / mup                    # mouse: press/release left button\n"
           "  key        <usage>             # keyboard: press+release one HID key (decimal)\n"
           "  keymod <modifier> <usage>      # keyboard: press with modifier (e.g. 8 11 = Cmd+H)\n"
           "  kdown <usage> / kup            # keyboard: press / release one key\n"
           "  type       <text>              # keyboard: type letters/digits/space\n"
           "  usb                            # show USB HID mount/ready state\n"
           "  wake                           # USB remote wakeup, then show state\n"
           "  help\n"
           "\n"
           "Useful key usages: Enter=40, Space=44, Tab=43, Esc=41, Left=80,\n"
           "Right=79, Up=82, Down=81 (decimal HID usage codes).\n\n");
}

static void trim_crlf(char *line)
{
    size_t n = strlen(line);
    while (n > 0 && (line[n - 1] == '\n' || line[n - 1] == '\r' ||
                     line[n - 1] == ' ' || line[n - 1] == '\t')) {
        line[--n] = '\0';
    }
}

static void handle_line(char *line)
{
    trim_crlf(line);
    if (line[0] == '\0') {
        return;
    }

    char cmd[32] = {0};
    if (sscanf(line, "%31s", cmd) != 1) {
        return;
    }

#if !CONFIG_ARTEMIS_HID_INCLUDE_TOUCH
    if (strcmp(cmd, "tap") == 0 || strcmp(cmd, "long_press") == 0 ||
        strcmp(cmd, "swipe") == 0 || strcmp(cmd, "drag") == 0) {
        printf("touchscreen disabled in this build (keyboard+mouse only)\n");
        return;
    }
#endif

    int a = 0, b = 0, c = 0, d = 0, e = 0;

    if (strcmp(cmd, "help") == 0 || strcmp(cmd, "h") == 0) {
        print_help();
    } else if (strcmp(cmd, "usb") == 0) {
        printf("usb mounted=%d suspended=%d mouse=%d keyboard=%d touch=%d mode=%s\n",
               tud_mounted(), tud_suspended(), hid_mouse_ready(),
               hid_keyboard_ready(), hid_touch_ready(), HID_MODE_NAME);
    } else if (strcmp(cmd, "wake") == 0) {
        if (tud_suspended()) {
            tud_remote_wakeup();
            for (int i = 0; i < 50 && tud_suspended(); i++) {
                vTaskDelay(pdMS_TO_TICKS(10));
            }
        }
        printf("usb mounted=%d suspended=%d mouse=%d keyboard=%d touch=%d mode=%s\n",
               tud_mounted(), tud_suspended(), hid_mouse_ready(),
               hid_keyboard_ready(), hid_touch_ready(), HID_MODE_NAME);
    } else if (strcmp(cmd, "tap") == 0) {
        if (sscanf(line, "%*s %d %d", &a, &b) == 2 && a >= 0 && b >= 0) {
            printf("[cmd] tap %d %d\n", a, b);
            hid_touch_tap((uint16_t) a, (uint16_t) b);
        } else {
            printf("usage: tap <x> <y>\n");
        }
    } else if (strcmp(cmd, "long_press") == 0) {
        if (sscanf(line, "%*s %d %d %d", &a, &b, &c) == 3 &&
            a >= 0 && b >= 0 && c > 0) {
            printf("[cmd] long_press %d %d %dms\n", a, b, c);
            hid_touch_long_press((uint16_t) a, (uint16_t) b, (uint32_t) c);
        } else {
            printf("usage: long_press <x> <y> <duration_ms>\n");
        }
    } else if (strcmp(cmd, "swipe") == 0) {
        if (sscanf(line, "%*s %d %d %d %d %d", &a, &b, &c, &d, &e) == 5 &&
            a >= 0 && b >= 0 && c >= 0 && d >= 0 && e > 0) {
            printf("[cmd] swipe (%d,%d) -> (%d,%d) %dms\n", a, b, c, d, e);
            hid_touch_swipe((uint16_t) a, (uint16_t) b, (uint16_t) c,
                            (uint16_t) d, (uint32_t) e);
        } else {
            printf("usage: swipe <x1> <y1> <x2> <y2> <duration_ms>\n");
        }
    } else if (strcmp(cmd, "drag") == 0) {
        if (sscanf(line, "%*s %d %d %d %d %d", &a, &b, &c, &d, &e) == 5 &&
            a >= 0 && b >= 0 && c >= 0 && d >= 0 && e > 0) {
            printf("[cmd] drag (%d,%d) -> (%d,%d) %dms\n", a, b, c, d, e);
            hid_touch_drag((uint16_t) a, (uint16_t) b, (uint16_t) c,
                           (uint16_t) d, (uint32_t) e);
        } else {
            printf("usage: drag <x1> <y1> <x2> <y2> <duration_ms>\n");
        }
    } else if (strcmp(cmd, "home") == 0) {
        printf("[cmd] home\n");
        if (!hid_mouse_home()) {
            printf("ERR mouse not ready\n");
        }
    } else if (strcmp(cmd, "move") == 0) {
        if (sscanf(line, "%*s %d %d", &a, &b) == 2 &&
            a >= -10000 && a <= 10000 && b >= -10000 && b <= 10000) {
            printf("[cmd] move %d %d\n", a, b);
            if (!hid_mouse_move(a, b)) {
                printf("ERR mouse not ready\n");
            }
        } else {
            printf("usage: move <dx> <dy> (each -10000..10000)\n");
        }
    } else if (strcmp(cmd, "mswipe") == 0) {
        int parsed = sscanf(line, "%*s %d %d %d", &a, &b, &c);
        if (parsed >= 2) {
            int duration = (parsed == 3) ? c : 300;
            if (duration < 0) {
                duration = 0;
            }
            printf("[cmd] mswipe %d %d %dms\n", a, b, duration);
            if (!hid_mouse_swipe(a, b, (uint32_t) duration)) {
                printf("ERR mswipe failed (usb not ready)\n");
            }
        } else {
            printf("usage: mswipe <dx> <dy> [duration_ms]\n");
        }
    } else if (strcmp(cmd, "click") == 0) {
        printf("[cmd] click\n");
        hid_mouse_click();
    } else if (strcmp(cmd, "mdown") == 0) {
        printf("[cmd] mdown\n");
        hid_mouse_down();
    } else if (strcmp(cmd, "mup") == 0) {
        printf("[cmd] mup\n");
        hid_mouse_up();
    } else if (strcmp(cmd, "key") == 0) {
        if (sscanf(line, "%*s %d", &a) == 1 && a > 0 && a <= 255) {
            printf("[cmd] key 0x%02X\n", a);
            hid_keyboard_tap((uint8_t) a);
        } else {
            printf("usage: key <usage 0-255>\n");
        }
    } else if (strcmp(cmd, "keymod") == 0) {
        if (sscanf(line, "%*s %d %d", &a, &b) == 2 && a >= 0 && a <= 255 &&
            b > 0 && b <= 255) {
            printf("[cmd] keymod 0x%02X 0x%02X\n", a, b);
            hid_keyboard_press((uint8_t) a, (uint8_t) b);
            vTaskDelay(pdMS_TO_TICKS(30));
            hid_keyboard_release();
        } else {
            printf("usage: keymod <modifier> <usage>\n");
        }
    } else if (strcmp(cmd, "kdown") == 0) {
        if (sscanf(line, "%*s %d", &a) == 1 && a > 0 && a <= 255) {
            printf("[cmd] kdown 0x%02X\n", a);
            hid_keyboard_press(0, (uint8_t) a);
        } else {
            printf("usage: kdown <usage 0-255>\n");
        }
    } else if (strcmp(cmd, "kup") == 0) {
        printf("[cmd] kup\n");
        hid_keyboard_release();
    } else if (strcmp(cmd, "type") == 0) {
        const char *text = line;
        while (*text == ' ' || *text == '\t') {
            text++;
        }
        text += strlen(cmd);
        while (*text == ' ' || *text == '\t') {
            text++;
        }
        printf("[cmd] type: %s\n", text);
        hid_keyboard_type(text);
    } else {
        printf("ERR unknown command: %s\n", cmd);
    }
}

static void cmd_task(void *arg)
{
    (void) arg;
    char line[128];
    char discard[64];

    printf("\n=== HID Touch/Mouse/Keyboard ready. Type 'help' for commands. ===\n");

    while (1) {
        if (fgets(line, sizeof(line), stdin) == NULL) {
            vTaskDelay(pdMS_TO_TICKS(50));
            continue;
        }
        size_t len = strlen(line);
        if (len > 0 && line[len - 1] != '\n') {
            // Overlong line: drain to the next newline, never execute a fragment.
            while (fgets(discard, sizeof(discard), stdin) != NULL) {
                size_t n = strlen(discard);
                if (n > 0 && discard[n - 1] == '\n') {
                    break;
                }
            }
            printf("ERR line too long\nOK\n");
            fflush(stdout);
            continue;
        }
        handle_line(line);
        // Every command ends with an explicit ack. The host keeps exactly one
        // command in flight, so a slow home/move cannot overrun the UART parser.
        printf("OK\n");
        fflush(stdout);
    }
}

void cmd_task_start(void)
{
    xTaskCreate(cmd_task, "cmd_task", 4096, NULL, 5, NULL);
}
