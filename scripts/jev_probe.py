#!/usr/bin/env python3
"""Build a Jev input by hand and see whether the answer is reasonable.

You give it a board and a piece; it shows you three things, in this order:

1. **the input** — the exact message that would be sent, rendered as a picture plus the
   field list, so a wrong input is obvious before anything is spent. `--dry-run` (the
   default) stops here;
2. **the answer** — `--ask` sends it: every candidate's value, the winner, the margin, the
   runner-up. `noul` gives independent probabilities that do not sum to one, `choice` gives a
   normalised distribution;
3. **the judge** — the same candidates ranked by the simulator plus the search policy's
   weights, with the chosen move's rank and regret, and the best few with their scores. The
   judge is a strong heuristic, not ground truth: it answers "how close is this to what a
   heuristic prefers", which is the cheapest way to spot an answer that is plainly off.
   Nothing of it goes into the input — the message carries no score, no holes, no height.

It also translates the chosen move into the clicks it would cause (rotate N times, then M
taps on the left/right button), which is what "reasonable" has to mean for a policy.

Usage:
    # free: what would be sent, and what the judge thinks of every candidate
    .venv/bin/python scripts/jev_probe.py --preset demo --piece T --position 2,4

    # ask Jev the same thing (one paid call), with the resulting boards included
    .venv/bin/python scripts/jev_probe.py --preset demo --piece T --position 2,4 --ask --results

    # your own board: 20 rows of 10 characters, "1" or "." , rows joined by "/"
    .venv/bin/python scripts/jev_probe.py --board "1........./11......../111......." --piece-mask "11/11"

    # save a case (board, piece, candidates, answers) to re-run later
    .venv/bin/python scripts/jev_probe.py --preset demo --piece T --ask --save case.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SKILLS = PROJECT_ROOT / "skills"
SKILL = "tetris"
ROWS, COLUMNS = 20, 10

PRESETS = {
    "empty": ["0" * COLUMNS] * ROWS,
    # A small irregular stack: flat on the right, a well on the left, one overhang.
    "demo": ["0" * COLUMNS] * 13
    + [
        "0000000000",
        "0000000000",
        "0000000000",
        "1110000000",
        "1100000000",
        "1100100000",
        "1000110000",
        "1001111000",
    ],
    # Nearly full: the only useful moves are the ones that clear something.
    "almost_full": ["0" * COLUMNS] * 14
    + ["1111111111", "1111111101", "1111111101", "1111111111", "1111110111", "1111111111"],
}


def wrong_interpreter() -> str | None:
    venv_python = PROJECT_ROOT / ".venv" / "bin" / "python"
    if not venv_python.exists():
        return None
    if Path(sys.executable).resolve() == venv_python.resolve():
        return None
    return str(venv_python)


def modules():
    from artemis.reflex.registry import load_skill

    load_skill(SKILL, directory=SKILLS)
    return {
        "policy_jev": importlib.import_module(f"artemis_skills_{SKILL}.policy_jev"),
        "jev_state": importlib.import_module(f"artemis_skills_{SKILL}.jev_state"),
        "control": importlib.import_module(f"artemis_skills_{SKILL}.control"),
        # The judge lives in `tetris_jev_modes.py` and needs the search policy's weights.
        "policy": importlib.import_module(f"artemis_skills_{SKILL}.policy"),
        "sim": importlib.import_module(f"artemis_skills_{SKILL}.simulator"),
        "bench": load_bench(),
    }


def load_bench():
    """The judge from ``tetris_jev_modes.py``, loaded by path so there is only one copy."""
    path = Path(__file__).with_name("tetris_jev_modes.py")
    spec = importlib.util.spec_from_file_location("tetris_jev_modes", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- #
# The input, built by hand
# --------------------------------------------------------------------------- #


def parse_board(text: str, preset: str | None) -> list[str]:
    if preset:
        if preset not in PRESETS:
            raise SystemExit(f"unknown preset {preset!r}; try: {', '.join(PRESETS)}")
        return list(PRESETS[preset])
    rows = [row.strip() for row in text.replace(",", "/").split("/") if row.strip()]
    rows = ["".join("1" if c in "1#X" else "0" for c in row) for row in rows]
    if len(rows) != ROWS:
        raise SystemExit(f"a board is {ROWS} rows of {COLUMNS}; got {len(rows)} rows")
    if any(len(row) != COLUMNS for row in rows):
        raise SystemExit(f"every row must be {COLUMNS} characters")
    return rows


def letter_mask(letter: str, sim) -> list[list[int]]:
    cells = sim.orientations(letter.upper())[0]
    width = max(x for x, _ in cells) + 1
    height = max(y for _, y in cells) + 1
    mask = [[0] * width for _ in range(height)]
    for x, y in cells:
        mask[y][x] = 1
    return mask


def show_board(board: list[str]) -> str:
    width = len(board[0])
    lines = ["    +" + "-" * width + "+"]
    for index, row in enumerate(board):
        lines.append(f"{index:>3} |" + "".join("#" if c == "1" else "." for c in row) + "|")
    lines.append("    +" + "-" * width + "+")
    return "\n".join(lines)


def build_case(args, mods, calibration: dict) -> dict:
    sim, policy_jev, jev_state = mods["sim"], mods["policy_jev"], mods["jev_state"]
    board = parse_board(args.board or "", args.preset)
    if args.piece:
        mask = letter_mask(args.piece, sim)
    elif args.piece_mask:
        mask = [[1 if c in "1#X" else 0 for c in row] for row in args.piece_mask.split("/") if row]
        if not any(any(line) for line in mask):
            raise SystemExit("the piece mask is empty")
    else:
        raise SystemExit("give --piece (a letter) or --piece-mask (e.g. 11/11)")
    row, column = (int(v) for v in (args.position or "0,0").split(","))
    positions = [
        [row + r, column + c] for r, line in enumerate(mask) for c, cell in enumerate(line) if cell
    ]
    state = {
        "board_matrix": board,
        "current_piece_mask": mask,
        "current_piece_positions": positions,
        "motion_vector": [int(v) for v in (args.motion or "1,0").split(",")],
        "next_preview": None,
    }
    gravity = jev_state.gravity_direction(state["motion_vector"], "down")
    sim_board = policy_jev._board(state)
    candidates = policy_jev._candidates(sim_board, mask, gravity, calibration)
    if args.candidates:
        candidates = candidates[: int(args.candidates)]
    message = jev_state.build_state(
        board_matrix=board,
        pieces=state,
        mechanics=policy_jev._mechanics(calibration),
        next_preview=None,
        candidates=candidates,
        resulting_boards=args.results,
    )
    return {
        "board": board,
        "mask": mask,
        "state": state,
        "gravity": gravity,
        "sim_board": sim_board,
        "candidates": candidates,
        "message": message,
    }


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #


def describe_input(case: dict, jev_state) -> str:
    message = case["message"]
    size = len(json.dumps(message, ensure_ascii=False))
    fields = ", ".join(sorted(message))
    moves = message["candidate_moves"]
    has_results = any("resulting_board" in move for move in moves)
    return (
        f"{show_board(case['board'])}\n"
        f"piece: {len(case['mask'])}x{len(case['mask'][0])} cells at column {case['state']['current_piece_positions'][0][1]}"
        f"  gravity={case['gravity']}\n"
        f"orientations offered: {len(message['piece_orientations'])}"
        f"   candidates: {len(moves)}"
        f"   resulting boards: {'yes' if has_results else 'no'}\n"
        f"message: {size} bytes, fields = {fields}\n"
        f"candidate ids: {', '.join(move['id'] for move in moves[:8])}"
        f"{' …' if len(moves) > 8 else ''}"
    )


def describe_judge(case: dict, mods, calibration: dict, limit: int = 5) -> tuple[str, dict]:
    ordered, ranks, score_of = mods["bench"].judge(
        mods, calibration, case["sim_board"], case["candidates"]
    )
    if not ordered:
        return "judge: no legal placement to rank", {
            "ordered": [],
            "ranks": {},
            "score_of": score_of,
        }
    best = ordered[0][1]
    lines = [f"judge (search weights, top {limit}):"]
    for key, score in ordered[:limit]:
        lines.append(f"   r{key[0]}c{key[1]}  score {score:8.2f}")
    lines.append(f"   best score {best:.2f}; all {len(ordered)} legal candidates ranked")
    return "\n".join(lines), {"ordered": ordered, "ranks": ranks, "score_of": score_of}


def describe_answer(case: dict, mods, calibration: dict, args) -> tuple[str, dict]:
    policy_jev = mods["policy_jev"]
    mode = args.mode or str(calibration["jev"].get("mode") or "noul")
    arm_cal = {**calibration, "jev": {**calibration["jev"], "mode": mode}}
    import os
    import tempfile

    dump = Path(tempfile.mkdtemp()) / "jev.jsonl"
    previous = os.environ.get("ARTEMIS_JEV_DUMP")
    os.environ["ARTEMIS_JEV_DUMP"] = str(dump)
    started = time.monotonic()
    try:
        placement, confidence = policy_jev._ask(
            mode, case["message"], case["candidates"], arm_cal, {}
        )
    finally:
        if previous is None:
            os.environ.pop("ARTEMIS_JEV_DUMP", None)
        else:
            os.environ["ARTEMIS_JEV_DUMP"] = previous
    seconds = time.monotonic() - started
    raw = None
    if dump.exists() and dump.read_text(encoding="utf-8").strip():
        raw = json.loads(dump.read_text(encoding="utf-8").strip().splitlines()[-1]).get("answers")
    if placement is None:
        return f"Jev ({mode}): no usable answer ({seconds:.2f}s)", {"mode": mode, "chosen": None}
    key = (int(placement.rotation), int(placement.column))
    lines = [f"Jev ({mode}): chose r{key[0]}c{key[1]}  confidence {confidence}  in {seconds:.2f}s"]
    if isinstance(raw, dict) and raw:
        values = []
        for name, answer in raw.items():
            try:
                values.append(
                    (
                        float(answer.get("noul", answer))
                        if isinstance(answer, dict)
                        else float(answer),
                        name,
                    )
                )
            except (TypeError, ValueError):
                continue
        values.sort(reverse=True)
        lines.append(
            "   all answers, best first: "
            + ", ".join(f"{name}={value:.3f}" for value, name in values[:8])
        )
    return "\n".join(lines), {
        "mode": mode,
        "chosen": {"rotation": key[0], "column": key[1]},
        "confidence": confidence,
        "seconds": round(seconds, 3),
        "answers": raw,
    }


def describe_clicks(case: dict, mods, calibration: dict, chosen: dict | None) -> str:
    if not chosen:
        return "clicks: nothing chosen"
    control = mods["control"]
    from artemis.reflex.contract import Action

    action = Action(
        state={
            "placement": chosen,
            "current_column": case["state"]["current_piece_positions"][0][1],
            "calibration": calibration,
        }
    )
    gestures = control.to_gestures(action)
    if not gestures:
        return "clicks: none (the piece would be left where it is)"
    return "clicks: " + " then ".join(
        f"{gesture.description} at ({int(gesture.points[0][0])},{int(gesture.points[0][1])})"
        for gesture in gestures
    )


def self_check(mods, calibration: dict) -> int:
    """The harness itself: build a case, rank it, fake an answer, translate it. No network."""
    from artemis.services import systemone

    failures: list[str] = []
    real_ask, real_choice = systemone.ask, systemone.choice
    systemone.ask = lambda state, questions, **kw: {
        n: {"type": "noul", "noul": 0.5} for n in questions
    }
    systemone.choice = lambda state, instructions, criteria, **kw: (
        list(criteria)[0],
        {name: 1.0 / len(criteria) for name in criteria},
    )
    try:
        args = argparse.Namespace(
            board=None,
            preset="demo",
            piece="T",
            piece_mask=None,
            position="2,4",
            motion="1,0",
            candidates=None,
            results=True,
            mode="choice",
            ask=True,
        )
        case = build_case(args, mods, calibration)
        if not case["candidates"]:
            failures.append("no legal candidates for the demo board")
        text, judge = describe_judge(case, mods, calibration)
        if not judge["ordered"]:
            failures.append("the judge ranked nothing")
        _answer_text, answer = describe_answer(case, mods, calibration, args)
        if not answer.get("chosen"):
            failures.append("the fake answer produced no choice")
        clicks = describe_clicks(case, mods, calibration, answer.get("chosen"))
        if "clicks:" not in clicks:
            failures.append("no click translation")
        describe_input(case, mods["jev_state"])
    finally:
        systemone.ask, systemone.choice = real_ask, real_choice
    print(json.dumps({"self_check_failures": failures}, ensure_ascii=False, indent=2))
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--preset", choices=sorted(PRESETS), help="a ready-made board")
    parser.add_argument("--board", help='20 rows of 10 chars joined by "/" ("1" or ".")')
    parser.add_argument("--piece", help="a standard letter: I O T S Z J L")
    parser.add_argument("--piece-mask", help='a shape, e.g. "111/010"')
    parser.add_argument("--position", default="0,0", help="where the piece is: row,column")
    parser.add_argument("--motion", default="1,0", help="[drow,dcol] of the last two frames")
    parser.add_argument("--candidates", type=int, help="offer only the first N candidates")
    parser.add_argument(
        "--results", action="store_true", help="include each candidate's resulting board"
    )
    parser.add_argument(
        "--mode", choices=("noul", "choice"), help="question type (default: calibration)"
    )
    parser.add_argument(
        "--ask", action="store_true", help="actually call Jev (paid); default is a dry run"
    )
    parser.add_argument("--save", help="write the case and the answers here")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)

    interpreter = wrong_interpreter()
    if interpreter:
        print(f"wrong interpreter: run me with {interpreter}", file=sys.stderr)
        return 2

    mods = modules()
    calibration = json.loads((SKILLS / SKILL / "calibration.json").read_text(encoding="utf-8"))
    if args.self_check:
        return self_check(mods, calibration)

    case = build_case(args, mods, calibration)
    print("=== 输入 ===")
    print(describe_input(case, mods["jev_state"]))
    judge_text, judge = describe_judge(case, mods, calibration)
    print("\n=== 判别器（search 策略的评分，不是 ground truth）===")
    print(judge_text)

    answer: dict = {"chosen": None}
    if args.ask:
        print("\n=== Jev 的回答 ===")
        answer_text, answer = describe_answer(case, mods, calibration, args)
        print(answer_text)
        chosen = answer.get("chosen")
        if chosen:
            rank = judge["ranks"].get((chosen["rotation"], chosen["column"]))
            score = judge["score_of"](type("P", (), chosen)())
            best = judge["ordered"][0][1]
            print(
                f"   判别器排名 {rank}/{len(judge['ordered'])}  score {score:.2f}"
                f"  regret {best - score:.2f}" + ("   ← 与启发式最优一致" if rank == 1 else "")
            )
        print(describe_clicks(case, mods, calibration, chosen))
    else:
        print("\n（未调用 Jev：加 --ask 才会真的发问）")
        print(describe_clicks(case, mods, calibration, None))

    if args.save:
        payload = {
            "board": case["board"],
            "piece_mask": case["mask"],
            "position": case["state"]["current_piece_positions"][0],
            "motion_vector": case["state"]["motion_vector"],
            "mode": answer.get("mode"),
            "candidates": [move["id"] for move in case["message"]["candidate_moves"]],
            "message": case["message"],
            "answer": answer,
        }
        Path(args.save).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n已保存 {args.save}")
    if args.json:
        print(
            json.dumps({"message": case["message"], "answer": answer}, ensure_ascii=False, indent=2)
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
