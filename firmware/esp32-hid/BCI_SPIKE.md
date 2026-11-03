# BCI-only HID spike

Apple BCI HID reference: the collection is Report 1 signal quality, Report 2
32 buttons, Report 3 absolute pointer and Report 4 item selection plus output
scan information. Report 3 is the pointer when the HID Input item is declared
Absolute. This build exposes **only** that BCI interface; it is for hardware
validation on iOS 26+, not the normal robot rig.

## Build

The checked-in `sdkconfig.bci.defaults` is used with the normal iOS build:

```bash
cd firmware/esp32-hid
idf.py -B build-bci -D SDKCONFIG=build-bci/sdkconfig \
  -D SDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.bci.defaults" \
  set-target esp32s3
idf.py -B build-bci -D SDKCONFIG=build-bci/sdkconfig \
  -D SDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.bci.defaults" build
```

The build directory already exists at `build-bci/`. Artifacts:

```text
build-bci/bootloader/bootloader.bin
build-bci/partition_table/partition-table.bin
build-bci/hid_touchscreen.bin
```

## Flash

Replace `PORT` with the ESP32-S3 USB serial port:

```bash
cd firmware/esp32-hid/build-bci
/Users/xf/.espressif/tools/python/v6.1/venv/bin/esptool \
  --chip esp32s3 -p PORT -b 460800 \
  write_flash \
  0x0 bootloader/bootloader.bin \
  0x8000 partition_table/partition-table.bin \
  0x10000 hid_touchscreen.bin
```

Use the USB-OTG port for the iPhone; the UART console is separate. Console baud:
**115200**.

## Serial commands

```text
usb                 # expect: mounted=1 suspended=0 bci=1 mode=bci
bci 500 500         # absolute pointer to screen center
bci 0 0             # top-left
bci 1000 1000       # bottom-right
bci_click           # BCI button 0 = Select (press + release)
help
```

`bci <x> <y>` uses normalized 0..1000 coordinates. After waiting for the
interface to drain, it sends the signal-quality report first and then the
pointer report:

```text
Report ID 1: 0x00 0xFF (signal quality; the full collection expects it first)
Report ID 3: x, y, z = -128 (2D; z absent)
```

Report 4 (item selection, output scan information) is declared in the descriptor
but not sent by the firmware yet.

The host maps the logical range directly to the screen, so one step is about:

```text
1080-wide screen:  ~4.3 px
2400-high screen:  ~9.4 px
```

This is coarse absolute positioning, not pixel-perfect. Verify the host's axis
orientation and range with the three corner commands before using it for clicks.

## Expected

- iPhone running iOS 26+ enumerates a BCI HID device (product string currently
  says `MobileAgent`; interface count is 1).
- `usb` reports `bci=1` after mounting.
- Absolute pointer moves on each `bci` command.
- `bci_click` triggers the BCI Select action; whether that clicks at the pointer
  or activates the focused accessibility item must be verified on-device.

If `usb` reports `bci=0`, the BCI interface did not mount: check the OTG data
cable/port and that the phone is unlocked. If the host does not enumerate the
device at all, check whether this iOS build accepts a BCI-only HID interface;
the next step is a BCI + boot-mouse composite build.

## Scope

- No touch, boot mouse, or keyboard interfaces in this build.
- No TinyUSB source modification; the BCI report descriptor is passed through
  `tud_hid_descriptor_report_cb`.
- Do not merge this config into the default firmware until the absolute mapping
  and Select semantics are validated on hardware.
