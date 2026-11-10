idf_component_register(
    SRCS "main.c" "hid_descriptor.c" "hid_touch.c" "hid_mouse_kb.c" "hid_bci.c" "cmd.c"
    INCLUDE_DIRS "."
    PRIV_REQUIRES esp_tinyusb freertos
)

# A v2 hello identifies the exact firmware build. The short git hash is enough to
# correlate a run with a build; a tarball or CI checkout without .git reports "dev".
execute_process(
    COMMAND git -C "${CMAKE_CURRENT_LIST_DIR}" describe --always --dirty --abbrev=7
    OUTPUT_VARIABLE ARTEMIS_FIRMWARE_BUILD
    OUTPUT_STRIP_TRAILING_WHITESPACE
    ERROR_QUIET
)
if(NOT ARTEMIS_FIRMWARE_BUILD)
    set(ARTEMIS_FIRMWARE_BUILD "dev")
endif()
target_compile_definitions(${COMPONENT_LIB} PRIVATE
    ARTEMIS_FIRMWARE_BUILD="${ARTEMIS_FIRMWARE_BUILD}"
)
