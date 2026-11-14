import os
from types import SimpleNamespace

import cover_only
import merge_only
import upload_only
from DMR.utils.prompts import ask_numbers, default_task


def answers(monkeypatch, values):
    sequence = iter(values)
    monkeypatch.setattr("builtins.input", lambda _: next(sequence))


def test_default_task_matches_rendered_folders(tmp_path):
    tasks = [("demo", str(tmp_path / "demo"), "DMR-demo.yml")]
    assert default_task(tasks, str(tmp_path / "demo（弹幕版）")) == 1
    assert default_task(tasks, str(tmp_path / "demo（转码后）")) == 1
    assert default_task(tasks, str(tmp_path / "other")) == 0


def test_numbers_keep_typed_order_and_reprompt(monkeypatch):
    answers(monkeypatch, ["", "9", "3 1 3"])
    assert ask_numbers("> ", 3, ()) == [2, 0]
    answers(monkeypatch, ["n"])
    assert ask_numbers("> ", 3, range(3)) == []


def test_merge_folder_lists_by_creation_and_allows_reorder(monkeypatch, tmp_path):
    for index, name in enumerate(("b.mp4", "a.mp4", "note.txt")):
        path = tmp_path / name
        path.touch()
        os.utime(path, (index, index))
    monkeypatch.setattr(os.path, "getctime", os.path.getmtime)
    answers(monkeypatch, [str(tmp_path), "2 1"])
    assert merge_only.ask_videos() == [str(tmp_path / "a.mp4"), str(tmp_path / "b.mp4")]


def test_merge_single_files_until_blank(monkeypatch, tmp_path):
    first, second = tmp_path / "1.mp4", tmp_path / "2.mp4"
    first.touch()
    second.touch()
    answers(monkeypatch, [f'"{first}"', "missing.mp4", str(second), ""])
    assert merge_only.ask_videos() == [str(first), str(second)]


def config_with_task(tmp_path, upload_args):
    task = tmp_path / "DMR-demo.yml"
    task.write_text(
        "upload_args:\n"
        + "".join(f"  {line}\n" for line in upload_args.splitlines()),
        encoding="utf-8",
    )
    return task, SimpleNamespace(
        global_config={
            "upload_args": {
                "bilibili": {"engine": "biliuprs", "cover": {"mode": "extract_frame", "ratio": "16/9"}},
                "acfun": {"engine": "acfun", "cover": {"mode": "extract_frame"}},
            }
        }
    )


def test_cover_config_merges_target_defaults_and_asks_only_when_accounts_differ(
    monkeypatch, tmp_path
):
    task, config = config_with_task(
        tmp_path,
        "dm_video:\n"
        "  - {account: a1, cover: {mode: render_text}}\n"
        "src_video:\n"
        "  - {account: a2, cover: {mode: render_text}}\n",
    )
    assert cover_only.cover_config(config, str(task)) == {
        "mode": "render_text",
        "ratio": "16/9",
    }
    assert cover_only.cover_config(config, None)["mode"] == "extract_frame"

    task.write_text(
        "upload_args:\n  dm_video:\n"
        "    - {account: a1}\n"
        "    - {account: a2, target: acfun, cover: {mode: render_template}}\n",
        encoding="utf-8",
    )
    answers(monkeypatch, ["2"])
    assert cover_only.cover_config(config, str(task))["mode"] == "render_template"


def test_upload_configs_follow_main_pipeline_merge(tmp_path):
    _, config = config_with_task(tmp_path, "")
    data = {
        "upload_args": {
            "dm_video+src_video": [{"account": "a1"}, {"account": "a2", "target": "acfun"}]
        }
    }
    accounts = upload_only.upload_configs(config, data, "src_video")
    assert [(a["account"], a["engine"]) for a in accounts] == [
        ("a1", "biliuprs"),
        ("a2", "acfun"),
    ]
    assert upload_only.upload_configs(config, data, "other") == []
