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

import asyncio
import pytest
from artemis.context import ArtemisContext, DeviceContext, DevicePlatform


@pytest.mark.asyncio
async def test_artemis_context_waits_for_background_tasks():
    device = DeviceContext(
        host_platform="LINUX",
        mobile_platform=DevicePlatform.ANDROID,
        device_id="dummy",
        device_width=1080,
        device_height=2400,
    )

    context = ArtemisContext(device=device)

    task_run = False

    async def dummy_task():
        nonlocal task_run
        await asyncio.sleep(0.1)
        task_run = True

    async with context:
        task = asyncio.create_task(dummy_task())
        context.background_tasks.append(task)

    # After exiting the context, the task should have run to completion
    assert task_run is True


@pytest.mark.asyncio
async def test_artemis_context_cancels_background_tasks_after_grace_period():
    device = DeviceContext(
        host_platform="LINUX",
        mobile_platform=DevicePlatform.ANDROID,
        device_id="dummy",
        device_width=1080,
        device_height=2400,
    )
    context = ArtemisContext(
        device=device,
        background_task_grace_period_seconds=0.01,
    )

    task_cancelled = False

    async def stuck_summary():
        nonlocal task_cancelled
        try:
            await asyncio.Event().wait()
        finally:
            task_cancelled = True

    async with context:
        task = asyncio.create_task(stuck_summary(), name="stuck_summary")
        context.background_tasks.append(task)

    assert task.done()
    assert task.cancelled()
    assert task_cancelled is True
    assert context.background_tasks == []


def test_context_carries_the_read_only_device_descriptor():
    """PR-1: the factory records identity data on the context; it is not a handle."""
    from artemis.devices.descriptors import build_mock_descriptor

    descriptor = build_mock_descriptor(device_id="mock-device", screen_size=(1080, 2400))
    context = ArtemisContext(
        device=DeviceContext(mobile_platform=DevicePlatform.ANDROID, device_id="mock-device"),
        device_descriptor=descriptor,
    )

    assert context.device_descriptor is descriptor
    assert ArtemisContext(device=DeviceContext()).device_descriptor is None


@pytest.mark.asyncio
async def test_context_closes_the_device_session_on_exit():
    class Session:
        closed = False

        async def close(self):
            self.closed = True

    session = Session()
    context = ArtemisContext(device=DeviceContext(device_id="dummy"), device_session=session)

    await context.__aexit__(None, None, None)

    assert session.closed is True
    assert context.device_session is None
