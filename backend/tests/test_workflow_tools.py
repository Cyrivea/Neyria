"""Workflow 工具层产物覆盖自查（2026-10-11 真机事故防线）。

真机：用户明说「歌词 → 封面图」，GLM-flash 只建了 lyrics_only 单链还谎称
「已成功搭建」。工具返回里的确定性检查提醒，让模型在同一轮自我纠偏。
"""

from agents.tools.workflow_tools import _product_coverage_note


def test_flags_missing_image_when_user_asked_cover():
    note = _product_coverage_note("帮我把「歌词 → 封面图」的工作流搭好", {"lyrics"})
    assert "封面" in note or "图片" in note
    assert "检查提醒" in note


def test_silent_when_products_covered():
    assert _product_coverage_note("写首歌再配张封面", {"lyrics", "image"}) == ""


def test_silent_when_user_mentions_nothing():
    assert _product_coverage_note("随便来点好玩的", {"lyrics"}) == ""


def test_flags_missing_video_mv():
    note = _product_coverage_note("写个歌再做个 MV", {"lyrics"})
    assert "视频" in note


def test_flags_multiple_missing():
    note = _product_coverage_note("歌词、封面、MV 都要", {"lyrics"})
    assert "封面" in note and "视频" in note
