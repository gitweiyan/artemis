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

"""An interrupted run must still be reviewable: rebuild its archive from the database."""

from __future__ import annotations

import json
import sqlite3

import pytest

from artemis.runtime.session_export import export_session_from_db, resolve_session

SCHEMA = """
CREATE TABLE sessions (session_id TEXT PRIMARY KEY, initial_goal TEXT, start_time REAL,
                       end_time REAL, status TEXT, device_info TEXT, video_filepath TEXT, pid INTEGER);
CREATE TABLE steps (step_id TEXT PRIMARY KEY, session_id TEXT, step_number INTEGER, timestamp REAL,
                    pre_image_name TEXT, post_image_name TEXT, summary TEXT, action_taken TEXT,
                    operator_raw_thinking TEXT, operator_native_thinking TEXT,
                    last_execution_result TEXT, extra_metadata TEXT);
CREATE TABLE traces (trace_id TEXT PRIMARY KEY, session_id TEXT, step_id TEXT, parent_trace_id TEXT,
                     type TEXT, name TEXT, timestamp REAL, duration REAL, status TEXT, payload TEXT);
CREATE TABLE images (image_name TEXT PRIMARY KEY, timestamp REAL, ocr_result TEXT, ui_tree TEXT,
                     extra_metadata TEXT);
"""


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "data_engine.db"
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    connection.execute(
        "INSERT INTO sessions VALUES ('s1','go 淘宝',1000.0,NULL,'running',NULL,NULL,42)"
    )
    connection.execute(
        "INSERT INTO steps VALUES ('st1','s1',1,1005.0,'pre1','post1','opened home',"
        '\'[{"action":"tap","normalized_coordinates":[500,500]}]\',NULL,NULL,'
        '\'{"status":"failed","error":"pointer_missing"}\',NULL)'
    )
    connection.execute(
        "INSERT INTO traces VALUES ('t1','s1','st1',NULL,'action','click',1006.0,1.0,'failed',"
        '\'{"incident":"pointer_missing","action":{"action":"tap"}}\')'
    )
    connection.commit()
    connection.close()
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "pre1.jpg").write_bytes(b"x")
    (tmp_path / "images" / "post1.jpg").write_bytes(b"y")
    return path


def test_an_interrupted_session_is_exported_with_its_images_and_timeline(db, tmp_path):
    export = export_session_from_db(db, "s1", tmp_path / "out")

    assert export.session_id == "s1"
    assert export.status == "running"
    assert export.steps == 1
    assert export.images == 2
    assert export.actions == 1
    assert export.incidents == 1
    assert "interrupted before finalize" in " ".join(export.notes)

    rows = json.loads((export.directory / "steps.json").read_text(encoding="utf-8"))
    assert rows[0]["step"] == 1
    assert rows[0]["action"].startswith("Tapped") or "500" in rows[0]["action"]

    evidence = (export.directory / "evidence.md").read_text(encoding="utf-8")
    assert "pointer_missing" in evidence
    assert "未正常结束" in evidence


def test_the_newest_session_can_be_exported_by_name(db, tmp_path):
    assert resolve_session(sqlite3.connect(db), "latest") == "s1"


def test_a_prefix_is_enough_and_an_ambiguous_one_is_refused(db, tmp_path):
    connection = sqlite3.connect(db)
    connection.execute(
        "INSERT INTO sessions VALUES ('s2','go',2000.0,2001.0,'completed',NULL,NULL,7)"
    )
    connection.execute(
        "INSERT INTO sessions VALUES ('s11','go',3000.0,3001.0,'completed',NULL,NULL,8)"
    )
    connection.commit()

    assert resolve_session(connection, "s11") == "s11"
    with pytest.raises(ValueError, match="matches 3 sessions"):
        resolve_session(connection, "s")
    with pytest.raises(ValueError, match="no session"):
        resolve_session(connection, "nope")


def test_a_missing_database_is_reported(tmp_path):
    with pytest.raises(ValueError, match="no trace database"):
        export_session_from_db(tmp_path / "absent.db", "latest", tmp_path / "out")
