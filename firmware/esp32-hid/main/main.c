#include "esp_log.h"
#include "esp_err.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "tinyusb.h"
#include "tinyusb_default_config.h"
#include "sdkconfig.h"

#include "hid_descriptor.h"
#include "hid_touch.h"
#include "hid_mouse_kb.h"
#include "cmd.h"

static const char *TAG = "main";

void app_main(void)
{
#if CONFIG_ARTEMIS_HID_INCLUDE_TOUCH
    ESP_LOGI(TAG, "Initializing USB HID (touchscreen + mouse + keyboard)...");
#else
    ESP_LOGI(TAG, "Initializing USB HID (mouse + keyboard, iOS build)...");
#endif

    tinyusb_config_t tusb_cfg = TINYUSB_DEFAULT_CONFIG();

    // Keep the default device descriptor (Espressif VID), but use our own
    // configuration + string descriptors for the configured HID interfaces.
    tusb_cfg.descriptor.device = NULL;
    tusb_cfg.descriptor.full_speed_config = hid_configuration_descriptor;
    tusb_cfg.descriptor.string = hid_string_descriptor;
    tusb_cfg.descriptor.string_count = HID_STRING_COUNT;
#if (TUD_OPT_HIGH_SPEED)
    tusb_cfg.descriptor.high_speed_config = hid_configuration_descriptor;
#endif

    esp_err_t ret = tinyusb_driver_install(&tusb_cfg);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "TinyUSB driver install failed: %s", esp_err_to_name(ret));
        return;
    }

    ESP_LOGI(TAG, "USB HID initialized. Connect the USB-OTG port to the phone.");

    hid_touch_init();
    cmd_task_start();                      // interactive console on UART0
}
