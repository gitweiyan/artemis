#include "cmd.h"
#include "hid_descriptor.h"
#include "hid_touch.h"
#include "hid_mouse_kb.h"
#include "hid_bci.h"
#include "sdkconfig.h"
#include "tusb.h"

#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static void print_help(void)
{
    printf("\n");
#if CONFIG_ARTEMIS_HID_BCI_ONLY
    printf("Commands (BCI-only spike; normalized x/y 0..1000):\n"
           "  bci   <x> <y>   # send BCI absolute pointer report (z absent)\n"
           "  bci_click       # press+release BCI button 0 (Select)\n"
           "  usb             # show USB mount/BCI ready state\n"
           "  help\n\n");
    return;
#endif
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
           "  amove      <x> <y>            # mouse: absolute pointer (normalized 0..1000)\n"
           "  aclick                         # mouse: click at the absolute pointer\n"
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

#if CONFIG_ARTEMIS_HID_BCI_ONLY
    if (strcmp(cmd, "move") == 0 || strcmp(cmd, "home") == 0 ||
        strcmp(cmd, "click") == 0 || strcmp(cmd, "mdown") == 0 ||
        strcmp(cmd, "mup") == 0 || strcmp(cmd, "mswipe") == 0 ||
        strcmp(cmd, "amove") == 0 || strcmp(cmd, "aclick") == 0 ||
        strcmp(cmd, "key") == 0 || strcmp(cmd, "keymod") == 0 ||
        strcmp(cmd, "kdown") == 0 || strcmp(cmd, "kup") == 0 ||
        strcmp(cmd, "type") == 0) {
        printf("disabled in BCI-only build; use bci/bci_click\n");
        return;
    }
#endif

    if (strcmp(cmd, "help") == 0 || strcmp(cmd, "h") == 0) {
        print_help();
    } else if (strcmp(cmd, "usb") == 0) {
#if CONFIG_ARTEMIS_HID_BCI_ONLY
        printf("usb mounted=%d suspended=%d bci=%d mode=%s\n",
               tud_mounted(), tud_suspended(), hid_bci_ready(), HID_MODE_NAME);
#else
        printf("usb mounted=%d suspended=%d mouse=%d keyboard=%d touch=%d mode=%s\n",
               tud_mounted(), tud_suspended(), hid_mouse_ready(),
               hid_keyboard_ready(), hid_touch_ready(), HID_MODE_NAME);
#endif
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
#if CONFIG_ARTEMIS_HID_BCI_ONLY
    } else if (strcmp(cmd, "bci") == 0) {
        if (sscanf(line, "%*s %d %d", &a, &b) == 2 && a >= 0 && a <= 1000 &&
            b >= 0 && b <= 1000) {
            printf("[cmd] bci %d %d\n", a, b);
            if (!hid_bci_pointer(a, b)) {
                printf("ERR BCI not ready (check usb)\n");
            }
        } else {
            printf("usage: bci <x 0..1000> <y 0..1000>\n");
        }
    } else if (strcmp(cmd, "bci_click") == 0) {
        printf("[cmd] bci_click\n");
        if (!hid_bci_select()) {
            printf("ERR BCI not ready (check usb)\n");
        }
#endif
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
    } else if (strcmp(cmd, "amove") == 0) {
        if (sscanf(line, "%*s %d %d", &a, &b) == 2 && a >= 0 && a <= 1000 &&
            b >= 0 && b <= 1000) {
            printf("[cmd] amove %d %d\n", a, b);
            if (!hid_mouse_abs_move((uint16_t) ((long) a * 32767 / 1000),
                                    (uint16_t) ((long) b * 32767 / 1000))) {
                printf("ERR abs mouse not ready (check usb)\n");
            }
        } else {
            printf("usage: amove <x 0..1000> <y 0..1000>\n");
        }
    } else if (strcmp(cmd, "aclick") == 0) {
        printf("[cmd] aclick\n");
        if (!hid_mouse_abs_click()) {
            printf("ERR abs mouse not ready (check usb)\n");
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
        // `key` takes exactly one usage. A second number used to be ignored silently, so
        // `key 8 11` (meant as Cmd+H) pressed the letter 'e' and looked like a dead
        // keyboard. Reject it and point at the right command instead.
        int extra = 0;
        int parsed = sscanf(line, "%*s %d %d", &a, &extra);
        if (parsed == 2) {
            printf("usage: key <usage> takes one number; for a shortcut use"
                   " keymod <modifier> <usage> (e.g. keymod 8 11 = Cmd+H)\n");
        } else if (parsed == 1 && a > 0 && a <= 255) {
            printf("[cmd] key 0x%02X\n", a);
            hid_keyboard_tap((uint8_t) a);
        } else {
            printf("usage: key <usage 0-255>\n");
        }
    } else if (strcmp(cmd, "keymod") == 0) {
        int extra = 0;
        int parsed = sscanf(line, "%*s %d %d %d", &a, &b, &extra);
        if (parsed > 2) {
            printf("usage: keymod <modifier> <usage> takes two numbers"
                   " (modifier 8 = Cmd, e.g. keymod 8 11 = Cmd+H)\n");
        } else if (parsed == 2 && a >= 0 && a <= 255 && b > 0 && b <= 255) {
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

//: A command line may hold this many bytes (excluding the terminator). The host's
//: MAX_ESP32_LINE is the same number; `type` splits long text to fit it.
#define CMD_LINE_MAX 126

static void cmd_task(void *arg)
{
    (void) arg;
    char line[CMD_LINE_MAX + 1];
    size_t len = 0;
    bool overlong = false;

#if CONFIG_ARTEMIS_HID_BCI_ONLY
    printf("\n=== BCI-only HID ready. Type 'help' for commands. ===\n");
#else
    printf("\n=== HID Touch/Mouse/Keyboard ready. Type 'help' for commands. ===\n");
#endif
    fflush(stdout);

    // Read character by character and end the line on LF, CRLF **or a bare CR**: a serial
    // console is a serial console, and a terminal set to send only CR used to fill the
    // line buffer and answer `ERR line too long` for every command (measured 2026-10-21 in
    // CoolTerm). The parser also resynchronises immediately on an overlong line instead of
    // draining through `fgets`, so a lost newline costs one command, not the session.
    while (1) {
        int ch = fgetc(stdin);
        if (ch == EOF) {
            vTaskDelay(pdMS_TO_TICKS(20));
            continue;
        }
        if (ch == '\r' || ch == '\n') {
            if (overlong) {
                printf("ERR line too long (>%d bytes); one command per line\nOK\n",
                       CMD_LINE_MAX);
            } else if (len > 0) {
                line[len] = '\0';
                handle_line(line);
                printf("OK\n");
            } else {
                // A bare terminator: the LF half of a CRLF pair, or an empty Enter.
                printf("OK\n");
            }
            fflush(stdout);
            len = 0;
            overlong = false;
            continue;
        }
        if (overlong) {
            continue;  // already reported; the terminator resyncs us
        }
        if (len >= CMD_LINE_MAX) {
            overlong = true;
            continue;
        }
        line[len++] = (char) ch;
    }
}

void cmd_task_start(void)
{
    xTaskCreate(cmd_task, "cmd_task", 4096, NULL, 5, NULL);
}
