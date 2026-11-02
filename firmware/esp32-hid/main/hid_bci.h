#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "sdkconfig.h"

#if CONFIG_ARTEMIS_HID_BCI_ONLY
//: 1 when the BCI HID interface is mounted and ready for a report.
bool hid_bci_ready(void);

//: Send an absolute pointer report. x/y are normalized 0..1000 (screen grid);
//: z is always -128 (2D report, z axis absent). Returns false if USB is not ready.
bool hid_bci_pointer(int x_norm, int y_norm);

//: Press and release BCI button 0 ("Select") for 30 ms.
bool hid_bci_select(void);
#endif
