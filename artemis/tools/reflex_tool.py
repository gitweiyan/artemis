"""``play_reflex``: hand the device to a deterministic skill for a bounded time.

The agent's own loop decides in seconds and guards every action; a reflex skill
decides in milliseconds with no model and no safety net. This tool is the single,
generic bridge between the two regimes — the framework's whole side of the deal.
It knows nothing about any particular game or skill: the skill's own module
decides what a frame means and what to do about it.

One session is one step: the agent asks for N seconds, gets a structured report
(frames, fps, capture/action latency percentiles, stop reason, skill counters) and
decides what to do next. Frames are not written to the database.
"""

from __future__ import annotations

from typing import Any

from artemis.core.tool_declaration import ToolDeclaration
from artemis.reflex import registry
from artemis.reflex.capture import default_capture
from artemis.reflex.control import AdbInputControl, Esp32HidControl, HelperControl, esp32_port
from artemis.reflex.runtime import DEFAULT_FPS_CAP, ReflexRuntime
from artemis.tools.base import ArtemisTool
from artemis.tools.tool_wrapper import ToolWrapper
from third_party.mobile_use.utils.logger import get_logger
from pydantic import BaseModel, Field

logger = get_logger(__name__)

PLAY_REFLEX_TOOL_NAME = "play_reflex"

PLAY_REFLEX_DESCRIPTION = (
    "[REFLEX] Hand the device to a deterministic reflex skill for a bounded number of"
    " seconds: a tight capture→decide→act loop with no model in it, for anything that"
    " moves faster than this agent can think (a game, a fast scroll, a timed dialog)."
    " Use it for the fast part, then take the device back to verify, report or recover."
    " The result carries frames, fps, capture/action latency percentiles, the stop"
    " reason and whatever counters the skill reports (lines cleared, score, …)."
    " Call `list_skills` first when unsure which skill exists or how it is calibrated."
)

LIST_SKILLS_TOOL_NAME = "list_skills"


class PlayReflexArgs(BaseModel):
    """Arguments of ``play_reflex``."""

    skill: str = Field(description="Skill name, e.g. 'tetris'. See list_skills.")
    seconds: float = Field(
        default=30.0,
        ge=1.0,
        le=600.0,
        description="Wall-clock budget for the session. The skill may finish earlier.",
    )
    fps_cap: float = Field(
        default=DEFAULT_FPS_CAP,
        ge=1.0,
        le=30.0,
        description="Upper bound on loop rate. The capture path is the real limit.",
    )
    capture: str = Field(
        default="helper",
        description="'helper' (helper loopback, lowest latency) or 'screencap' (always available).",
    )


class ListSkillsArgs(BaseModel):
    """Arguments of ``list_skills``."""

    verbose: bool = Field(default=False, description="Include each skill's plan hint.")


async def _resolve_serial(ctx: Any, driver: Any = None) -> str:
    """The device a reflex session should use."""
    for candidate in (
        getattr(getattr(ctx, "device", None), "device_id", None),
        getattr(driver, "device_id", None),
        getattr(getattr(ctx, "device", None), "serial", None),
    ):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    raise RuntimeError("play_reflex needs a device serial on the context (ctx.device.device_id)")


class PlayReflexTool(ArtemisTool):
    """``play_reflex`` as an :class:`ArtemisTool`."""

    def __init__(self) -> None:
        super().__init__(
            name=PLAY_REFLEX_TOOL_NAME,
            description=PLAY_REFLEX_DESCRIPTION,
            args_schema=PlayReflexArgs,
            category="custom",
        )

    def is_available(self, ctx: Any = None) -> bool:
        """Available when at least one skill can be loaded."""
        try:
            return any(entry.get("ok") for entry in registry.available_skills().values())
        except (OSError, ValueError, RuntimeError, registry.SkillError) as exc:
            logger.debug(f"play_reflex availability check failed: {exc}")
            return False

    # pylint: disable=too-many-arguments
    async def execute(
        self,
        driver: Any = None,
        ctx: Any = None,
        skill: str = "",
        seconds: float = 30.0,
        fps_cap: float = DEFAULT_FPS_CAP,
        capture: str = "helper",
        **kwargs: Any,
    ) -> Any:
        name = (skill or kwargs.get("Skill") or "").strip()
        if not name:
            available = ", ".join(sorted(registry.available_skills())) or "none"
            return f"Error: play_reflex needs a skill name. Available: {available}"
        try:
            handle = registry.load_skill(name)
        except registry.SkillError as exc:
            return f"Error: {exc}"

        try:
            serial = await _resolve_serial(ctx, driver)
        except RuntimeError as exc:
            return f"Error: {exc}"

        try:
            frame_source = default_capture(serial, prefer=capture)
            # An ESP32-S3 HID board, when one is named, replaces both assisted paths: the
            # board is the touchscreen, so there is no helper and no adb input.
            esp = esp32_port()
            if esp:
                actuator = Esp32HidControl(esp)
            elif capture == "helper":
                actuator = HelperControl(serial)
            else:
                actuator = AdbInputControl(serial)
        except (OSError, ValueError, RuntimeError, KeyError, AttributeError) as exc:
            return (
                f"Error: the {capture} capture/control path could not be built on {serial}: {exc}"
            )

        def should_stop() -> bool:
            engine = getattr(ctx, "data_engine", None)
            session_id = getattr(engine, "current_session_id", None)
            if session_id is None:
                return False
            from artemis.runtime.cancel_requests import is_cancel_requested

            try:
                return bool(is_cancel_requested(session_id=str(session_id)))
            except (OSError, ValueError, RuntimeError):
                return False

        session = ReflexRuntime(capture=frame_source, control=actuator, fps_cap=fps_cap)
        try:
            result = session.run(handle, seconds=seconds, should_stop=should_stop)
        except Exception as exc:  # noqa: BLE001 - a third-party skill may raise anything
            logger.error(f"play_reflex('{name}') aborted: {exc}", exc_info=True)
            return f"Error: reflex skill '{name}' aborted: {exc}"
        logger.info(result.summary_line())
        return _render_result(result)


def _render_result(result: Any) -> str:
    """The tool answer: one line, the counters, and the last state the skill saw."""
    lines = [result.summary_line()]
    if result.counters:
        lines.append("counters: " + ", ".join(f"{k}={v}" for k, v in result.counters.items()))
    state = result.final_state
    if isinstance(state, dict):
        try:
            import json

            rendered = json.dumps(state, ensure_ascii=False)
        except (TypeError, ValueError):
            rendered = str(state)
        if len(rendered) > 600:
            rendered = rendered[:600] + "…"
        lines.append(f"final state: {rendered}")
    if result.stop_reason.value == "lost":
        lines.append(
            "The screen never became readable, so nothing was attempted: check that the app"
            " is in the foreground, or recalibrate the skill."
        )
    elif result.stop_reason.value == "control_lost":
        lines.append(
            "The device stopped accepting gestures (twice in a row): a dialog, an ad or a"
            " permission prompt is probably blocking the screen."
        )
    return "\n".join(lines)


class ListSkillsTool(ArtemisTool):
    """``list_skills``: which reflex skills exist, and whether they load."""

    def __init__(self) -> None:
        super().__init__(
            name=LIST_SKILLS_TOOL_NAME,
            description=(
                "[REFLEX] List the reflex skills available to play_reflex, whether each one"
                " loads (a skill that cannot load reports why), and its one-line hint."
            ),
            args_schema=ListSkillsArgs,
            category="custom",
        )

    async def execute(
        self, driver: Any = None, ctx: Any = None, verbose: bool = False, **kwargs: Any
    ) -> Any:
        found = registry.available_skills()
        if not found:
            return f"No reflex skills found under {registry.skills_dir()} (set ARTEMIS_SKILLS_DIR to add one)."
        rows = [f"Reflex skills in {registry.skills_dir()}:"]
        for name, entry in sorted(found.items()):
            if not entry.get("ok"):
                rows.append(f"  {name}: BROKEN — {entry.get('error')}")
                continue
            detail = f"  {name} v{entry.get('version')}"
            if verbose and entry.get("plan_hint"):
                detail += f" — {entry['plan_hint']}"
            rows.append(detail)
        return "\n".join(rows)


play_reflex = PlayReflexTool()
list_skills = ListSkillsTool()

PLAY_REFLEX_TOOL: ToolDeclaration = play_reflex.to_tool_declaration()
LIST_SKILLS_TOOL: ToolDeclaration = list_skills.to_tool_declaration()


def get_play_reflex_tool(ctx: Any) -> Any:
    """Exports ``play_reflex`` as a traced LangChain tool bound to ``ctx``."""
    from artemis.data_engine.trace import trace_langchain_tool

    return trace_langchain_tool(play_reflex.to_langchain_tool(ctx), ctx)


def get_list_skills_tool(ctx: Any) -> Any:
    """Exports ``list_skills`` as a traced LangChain tool bound to ``ctx``."""
    from artemis.data_engine.trace import trace_langchain_tool

    return trace_langchain_tool(list_skills.to_langchain_tool(ctx), ctx)


def reflex_tool_declarations(ctx: Any = None) -> list[ToolDeclaration]:
    """Declarations available for ``ctx`` (the Flash runner's projection)."""
    out: list[ToolDeclaration] = []
    if play_reflex.is_available(ctx):
        out.append(PLAY_REFLEX_TOOL)
    if list_skills.is_available(ctx):
        out.append(LIST_SKILLS_TOOL)
    return out


play_reflex_wrapper = ToolWrapper(
    tool_fn_getter=get_play_reflex_tool,
    on_success_fn=lambda output: output,
    on_failure_fn=lambda error: f"play_reflex failed: {error}",
)

list_skills_wrapper = ToolWrapper(
    tool_fn_getter=get_list_skills_tool,
    on_success_fn=lambda output: output,
    on_failure_fn=lambda error: f"list_skills failed: {error}",
)


__all__ = [
    "LIST_SKILLS_TOOL",
    "LIST_SKILLS_TOOL_NAME",
    "PLAY_REFLEX_TOOL",
    "PLAY_REFLEX_TOOL_NAME",
    "PlayReflexTool",
    "ListSkillsTool",
    "play_reflex",
    "play_reflex_wrapper",
    "list_skills",
    "list_skills_wrapper",
    "get_list_skills_tool",
    "get_play_reflex_tool",
    "reflex_tool_declarations",
]
