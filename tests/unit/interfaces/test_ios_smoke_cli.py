"""End-to-end CLI plumbing for the iOS UVC port hint."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from artemis.core.identity import IdentitySource
from artemis.drivers.ios.config import IosRuntimeConfig
from artemis.drivers.ios.uvc_identity import UVCIdentity
from artemis.interfaces.cli.main import app


def test_ios_smoke_cli_passes_the_port_hint_to_the_resolver():
    config = IosRuntimeConfig(esp32_port="/dev/fake")
    identity = UVCIdentity("USB-Video", IdentitySource.CONFIG_HINT)

    class Session:
        capability_registry = None

        async def close(self):
            return None

    class Leases:
        resource_ids = ()

        def acquire(self, *_args, **_kwargs):
            return self

        def release(self):
            return None

    with (
        patch(
            "artemis.interfaces.cli.commands.ios_smoke.IosRuntimeConfig.from_env",
            return_value=config,
        ),
        patch(
            "artemis.interfaces.cli.commands.ios_smoke.resolve_uvc_identity",
            return_value=identity,
        ) as resolver,
        patch("artemis.interfaces.cli.commands.ios_smoke.UvcCapture"),
        patch("artemis.interfaces.cli.commands.ios_smoke.Esp32BridgeControl"),
        patch(
            "artemis.interfaces.cli.commands.ios_smoke.build_ios_uvc_session",
            return_value=Session(),
        ),
        patch("artemis.interfaces.cli.commands.ios_smoke.IOSUvcHidDriver"),
        patch("artemis.interfaces.cli.commands.ios_smoke.LeaseSet", return_value=Leases()),
        patch("artemis.interfaces.cli.commands.ios_smoke._smoke_or_exit"),
        patch.dict(
            "os.environ",
            {"ARTEMIS_CAPABILITY_EVIDENCE": "0", "ARTEMIS_RESOURCE_LEASE": "0"},
            clear=False,
        ),
    ):
        result = CliRunner().invoke(app, ["ios-smoke", "--uvc-port-hint", "1-2.4"])

    assert result.exit_code == 0, result.output
    assert resolver.call_args.kwargs["port_hint"] == "1-2.4"
