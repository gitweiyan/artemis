# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Safety-net failure taxonomy shared by the Validator's precondition gates.

The categories are produced by the XML gate (``precondition_xml``) and the
pixel gate (``precondition_pixel``) and consumed by the execution loop when it
opens an :mod:`artemis.agents.validator.incidents` record for the Operator.
"""

from enum import StrEnum


class ValidationErrorCategory(StrEnum):
    NONE = "none"
    TARGET_DISAPPEARED = "target_disappeared"
    TARGET_SHIFTED = "target_shifted"
    TARGET_OCCUPIED = "target_occupied"
    PIXEL_TARGET_DISAPPEARED = "pixel_target_disappeared"
    PIXEL_BYPASSED = "pixel_bypassed"
    XML_BYPASSED = "xml_bypassed"
    #: A relative-pointer driver reported that no pointer was visible, so no click ran.
    POINTER_MISSING = "pointer_missing"
    #: The pointer was visible but outside the target box.
    POINTER_OFF_TARGET = "pointer_off_target"
    #: The pointer was inside the target but never within the click tolerance.
    POINTER_NOT_CENTERED = "pointer_not_centered"
    #: The harness replaced the screen while acting (connecting the pointer presses
    #: Cmd+H), so the planned target belongs to a screen that is no longer shown.
    SCREEN_CHANGED = "screen_changed"
    #: The click was dispatched on a verified target and the screen did not change.
    NO_EFFECT = "no_effect"
    #: The capture card delivered a frame with no picture (HDMI/HDCP, screen off).
    UVC_NO_SIGNAL = "uvc_no_signal"
    #: No frame arrived within the capture budget (no process output, device gone).
    CAPTURE_TIMEOUT = "capture_timeout"
    #: The verification frame was already older than the freshness budget.
    STALE_FRAME = "stale_frame"
    GENERAL = "general"
