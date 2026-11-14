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


@pytest.mark.asyncio
async def test_context_registers_incomplete_session_in_the_host_registry():
    from types import SimpleNamespace

    class Registry:
        def __init__(self):
            self.registered = []

        def register(self, session):
            self.registered.append(session)

    class Session:
        async def close(self):
            return SimpleNamespace(cleanup_complete=False)

    session = Session()
    registry = Registry()
    context = ArtemisContext(
        device=DeviceContext(device_id="dummy"),
        device_session=session,
        cleanup_registry=registry,
    )

    await context.__aexit__(None, None, None)

    assert registry.registered == [session]
    assert context.device_session is None


@pytest.mark.asyncio
async def test_context_registers_a_session_when_close_raises_an_unexpected_type_error():
    class Registry:
        def __init__(self):
            self.registered = []

        def register(self, session):
            self.registered.append(session)

    class Session:
        async def close(self):
            raise TypeError("unexpected cleanup owner error")

    session = Session()
    registry = Registry()
    context = ArtemisContext(
        device=DeviceContext(device_id="dummy"),
        device_session=session,
        cleanup_registry=registry,
    )

    await context.__aexit__(None, None, None)

    assert registry.registered == [session]
    assert context.device_session is None


@pytest.mark.asyncio
async def test_real_session_cleanup_failure_registry_retry_closes_the_loop():
    from artemis.devices.geometry import ScreenGeometry
    from artemis.devices.session import DeviceSession
    from artemis.runtime.session_cleanup import SessionCleanupRegistry

    class Sink:
        capabilities = __import__(
            "artemis.devices.capabilities", fromlist=["CapabilitySet"]
        ).CapabilitySet(supports_absolute_tap=True)

        def __init__(self):
            self.fail = True

        def close(self):
            if self.fail:
                raise ValueError("hardware still open")

    sink = Sink()
    session = DeviceSession(
        [],
        [sink],
        ScreenGeometry((10, 20), (10, 20)),
    )
    registry = SessionCleanupRegistry()
    context = ArtemisContext(
        device=DeviceContext(device_id="dummy"),
        device_session=session,
        cleanup_registry=registry,
    )

    await context.__aexit__(None, None, None)

    assert context.device_session is None
    assert len(registry.pending()) == 1
    assert registry.pending()[0].cleanup_complete is False

    sink.fail = False
    reports = await registry.retry_pending(max_attempts=2)

    assert reports and reports[-1].cleanup_complete is True
    assert session.cleanup_state.value == "complete"
    assert registry.pending() == []
