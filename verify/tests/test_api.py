"""针对运行中 API 的验收测试（默认 http://api:8000，可用 API_BASE_URL 覆盖）。"""
import os

import pytest
import requests

API = os.environ.get("API_BASE_URL", "http://api:8000")

BASE = dict(
    floor_height_mm=3000,
    run_length_mm=4800,
    riser_min_mm=150,
    riser_max_mm=190,
    tread_min_mm=250,
    tread_max_mm=320,
    target_riser_mm=175,
)


def post(payload):
    return requests.post(f"{API}/api/layout", json=payload, timeout=10)


def test_health():
    r = requests.get(f"{API}/api/health", timeout=10)
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_main_flow():
    r = post(BASE)
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    sol = data["solution"]
    # 3000/17≈176.47 距目标 175 最近
    assert sol["steps"] == 17
    assert sol["treads"] == 16
    # 3000 = 17*176 + 8：前 8 级 177，其余 176
    assert sol["riser_sequence_mm"] == [177] * 8 + [176] * 9
    assert sol["total_height_mm"] == 3000
    assert sol["max_riser_diff_mm"] <= 1
    assert sol["tread_display_mm"] == 300
    # 候选覆盖 2..40，唯一选中，未选中者均有淘汰原因
    assert [c["steps"] for c in data["candidates"]] == list(range(2, 41))
    selected = [c for c in data["candidates"] if c["selected"]]
    assert len(selected) == 1 and selected[0]["steps"] == 17
    assert all(c["reasons"] for c in data["candidates"] if not c["selected"])


def test_boundary_values_valid():
    # 闭区间边界：3000/20 = 150 恰好等于上下限
    payload = {**BASE, "riser_min_mm": 150, "riser_max_mm": 150, "target_riser_mm": 150}
    data = post(payload).json()
    assert data["status"] == "ok"
    assert data["solution"]["steps"] == 20
    assert data["solution"]["riser_sequence_mm"] == [150] * 20


def test_tie_break_prefers_fewer_steps():
    # 2520/9=280 与 2520/10=252 距目标 266 偏差同为 14mm，取踏步数较小的 9 级
    payload = dict(
        floor_height_mm=2520, run_length_mm=5000,
        riser_min_mm=200, riser_max_mm=300,
        tread_min_mm=100, tread_max_mm=1000,
        target_riser_mm=266,
    )
    data = post(payload).json()
    assert data["status"] == "ok"
    assert data["solution"]["steps"] == 9


def test_tread_rounding_half_up_display_only():
    # 4808/16 = 300.5 → 显示 301
    data = post({**BASE, "run_length_mm": 4808}).json()
    sol = data["solution"]
    assert abs(sol["exact_tread_mm"] - 300.5) < 1e-9
    assert sol["tread_display_mm"] == 301
    # 可行性使用未舍入值：3994/16 = 249.625 < 250，17 级候选必须不可行
    data2 = post({**BASE, "run_length_mm": 3994}).json()
    c17 = next(c for c in data2["candidates"] if c["steps"] == 17)
    assert c17["feasible"] is False


def test_no_solution():
    data = post({**BASE, "riser_min_mm": 170, "riser_max_mm": 172}).json()
    assert data["status"] == "no_solution"
    assert data["solution"] is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("floor_height_mm", 0),
        ("floor_height_mm", -3000),
        ("floor_height_mm", 3000.5),
        ("floor_height_mm", "3000"),
        ("floor_height_mm", True),
        ("run_length_mm", 0),
        ("riser_min_mm", -1),
        ("target_riser_mm", 1.5),
    ],
)
def test_invalid_values_rejected(field, value):
    assert post({**BASE, field: value}).status_code == 422


def test_interval_lower_greater_than_upper_rejected():
    assert post({**BASE, "riser_min_mm": 200, "riser_max_mm": 190}).status_code == 422
    assert post({**BASE, "tread_min_mm": 330, "tread_max_mm": 320}).status_code == 422


def test_missing_field_rejected():
    payload = {k: v for k, v in BASE.items() if k != "run_length_mm"}
    assert post(payload).status_code == 422


def test_huge_integer_preserved_exactly():
    # 2^53 + 1：API 必须按原值精确计算（保持原值），不得静默舍入为 2^53
    huge = 9007199254740993
    payload = dict(
        floor_height_mm=huge, run_length_mm=4800,
        riser_min_mm=1, riser_max_mm=10**18,
        tread_min_mm=1, tread_max_mm=10**9,
        target_riser_mm=1,
    )
    r = post(payload)
    assert r.status_code == 200
    sol = r.json()["solution"]
    assert sol["steps"] == 40
    # JSON 整数往返后序列总和仍精确等于原值
    assert sum(sol["riser_sequence_mm"]) == huge
    assert sol["total_height_mm"] == huge
