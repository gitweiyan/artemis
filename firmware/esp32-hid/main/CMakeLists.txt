idf_component_register(
    SRCS "main.c" "hid_descriptor.c" "hid_touch.c" "hid_mouse_kb.c" "hid_bci.c" "cmd.c"
    INCLUDE_DIRS "."
    PRIV_REQUIRES esp_tinyusb freertos
)
