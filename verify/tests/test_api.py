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
    # 兼容旧请求：默认得到自动推荐，补充推荐踏步数与选用来源
    assert data["recommended_steps"] == 17
    assert data["selection_source"] == "auto"
    sol = data["solution"]
    # 3000/17≈176.47 距目标 175 最近
    assert sol["steps"] == 17
    assert sol["treads"] == 16
    # 3000 = 17*176 + 8：前 8 级 177，其余 176
    assert sol["riser_sequence_mm"] == [177] * 8 + [176] * 9
    assert sol["total_height_mm"] == 3000
    assert sol["max_riser_diff_mm"] <= 1
    assert sol["tread_display_mm"] == 300
    # 旧请求兼容：不带控制点时响应不出现受控字段，余数前置序列不变
    assert "control_points" not in data
    assert "controlled" not in sol
    # 候选覆盖 2..40，唯一选中，推荐项与选中一致，未选中者均有淘汰原因
    assert [c["steps"] for c in data["candidates"]] == list(range(2, 41))
    selected = [c for c in data["candidates"] if c["selected"]]
    assert len(selected) == 1 and selected[0]["steps"] == 17
    recommended = [c for c in data["candidates"] if c["recommended"]]
    assert len(recommended) == 1 and recommended[0]["steps"] == 17
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


def test_manual_selection_of_feasible_candidate():
    r = post({**BASE, "selected_steps": 18})
    assert r.status_code == 200
    data = r.json()
    # 推荐项保持原排序结果（17 级），放样按指定的 18 级生成
    assert data["recommended_steps"] == 17
    assert data["selection_source"] == "manual"
    sol = data["solution"]
    assert sol["steps"] == 18
    assert sol["treads"] == 17
    # 3000 = 18*166 + 12：前 12 级 167，其余 166
    assert sol["riser_sequence_mm"] == [167] * 12 + [166] * 6
    assert sol["total_height_mm"] == 3000
    assert sol["tread_display_mm"] == 282  # 4800/17 ≈ 282.35
    by_steps = {c["steps"]: c for c in data["candidates"]}
    assert by_steps[17]["recommended"] is True and by_steps[17]["selected"] is False
    assert by_steps[18]["recommended"] is False and by_steps[18]["selected"] is True


def test_selected_steps_equal_to_recommendation():
    data = post({**BASE, "selected_steps": 17}).json()
    assert data["recommended_steps"] == 17
    assert data["selection_source"] == "manual"
    assert data["solution"]["steps"] == 17


@pytest.mark.parametrize("bad_steps", [1, 41, 0, 100, 21, 15])
def test_infeasible_or_out_of_range_selection_returns_422(bad_steps):
    # 21 级被踏步高度下限淘汰（142.86 < 150）；15 级被上限淘汰（200 > 190）
    r = post({**BASE, "selected_steps": bad_steps})
    assert r.status_code == 422
    detail = r.json()["detail"]
    # 错误可定位到 selected_steps
    locs = [tuple(e["loc"]) for e in detail]
    assert ("body", "selected_steps") in locs
    assert any(str(bad_steps) in e["msg"] for e in detail)


def test_selected_steps_wrong_type_returns_422():
    assert post({**BASE, "selected_steps": "18"}).status_code == 422
    assert post({**BASE, "selected_steps": 18.5}).status_code == 422
    assert post({**BASE, "selected_steps": True}).status_code == 422


def test_selected_steps_with_no_solution_returns_422():
    payload = {**BASE, "riser_min_mm": 170, "riser_max_mm": 172, "selected_steps": 17}
    r = post(payload)
    assert r.status_code == 422
    locs = [tuple(e["loc"]) for e in r.json()["detail"]]
    assert ("body", "selected_steps") in locs


def test_no_solution_response_shape_unchanged():
    data = post({**BASE, "riser_min_mm": 170, "riser_max_mm": 172}).json()
    assert data["status"] == "no_solution"
    assert data["solution"] is None
    assert data["recommended_steps"] is None
    assert data["selection_source"] is None


# ---------------------------------------------------------------------------
# 中间标高控制点
# ---------------------------------------------------------------------------


def test_control_points_recommended_dual_points_hit_exactly():
    """推荐方案双控制点：精确命中、受控标识、推荐/选中标记保留。"""
    payload = {**BASE, "control_points": [
        {"step": 5, "height_mm": 875}, {"step": 8, "height_mm": 1400}]}
    r = post(payload)
    assert r.status_code == 200, r.text
    data = r.json()
    sol = data["solution"]
    assert sol["controlled"] is True
    assert data["recommended_steps"] == 17 and data["selection_source"] == "auto"
    # 控制点精确命中，末级仍精确等于层高
    assert sol["cumulative_height_mm"][4] == 875
    assert sol["cumulative_height_mm"][7] == 1400
    assert sol["cumulative_height_mm"][-1] == 3000
    assert sol["total_height_mm"] == 3000
    # 逐级高度仍在原高度闭区间
    assert all(150 <= h <= 190 for h in sol["riser_sequence_mm"])
    # 命中值与偏差报告
    assert data["control_points"] == [
        {"step": 5, "height_mm": 875, "hit_height_mm": 875, "deviation_mm": 0},
        {"step": 8, "height_mm": 1400, "hit_height_mm": 1400, "deviation_mm": 0},
    ]
    # 候选标记：推荐与选中仍为 17
    by_steps = {c["steps"]: c for c in data["candidates"]}
    assert by_steps[17]["recommended"] is True and by_steps[17]["selected"] is True


def test_control_points_half_mm_grid_within_half_mm_of_ideal_line():
    """单控制点产生半毫米级高：段内任一累计相对理想直线误差 ≤ 0.5mm。"""
    r = post({**BASE, "control_points": [{"step": 8, "height_mm": 1400}]})
    sol = r.json()["solution"]
    assert sol["cumulative_height_mm"][7] == 1400
    anchors = [(0, 0), (8, 1400), (17, 3000)]
    cum = sol["cumulative_height_mm"]
    for (a, ha), (b, hb) in zip(anchors, anchors[1:]):
        m, delta = b - a, hb - ha
        for j in range(1, m + 1):
            got = cum[a + j - 1] - ha
            ideal = j * delta / m
            assert abs(got - ideal) <= 0.5 + 1e-9, (a, b, j, got, ideal)


def test_control_points_manual_selection_hits_exactly():
    """人工方案应用控制点：精确命中，推荐项与人工标识同时保留。"""
    r = post({**BASE, "selected_steps": 18,
              "control_points": [{"step": 9, "height_mm": 1500}]})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["recommended_steps"] == 17 and data["selection_source"] == "manual"
    sol = data["solution"]
    assert sol["controlled"] is True and sol["steps"] == 18
    assert sol["cumulative_height_mm"][8] == 1500
    assert sol["cumulative_height_mm"][-1] == 3000
    assert all(150 <= h <= 190 for h in sol["riser_sequence_mm"])
    assert data["control_points"][0]["hit_height_mm"] == 1500
    by_steps = {c["steps"]: c for c in data["candidates"]}
    assert by_steps[17]["recommended"] is True and by_steps[17]["selected"] is False
    assert by_steps[18]["selected"] is True


def test_control_points_empty_list_matches_old_request():
    data = post({**BASE, "control_points": []}).json()
    assert "control_points" not in data
    assert "controlled" not in data["solution"]
    assert data["solution"]["riser_sequence_mm"] == [177] * 8 + [176] * 9


@pytest.mark.parametrize(
    "points,idx,field",
    [
        ([{"step": 0, "height_mm": 100}], 0, "step"),
        ([{"step": 17, "height_mm": 2900}], 0, "step"),
        ([{"step": 8, "height_mm": 3100}], 0, "height_mm"),
        ([{"step": 8, "height_mm": 0}], 0, "height_mm"),
        ([{"step": 8, "height_mm": 1400}, {"step": 5, "height_mm": 1500}], 1, "step"),
        ([{"step": 5, "height_mm": 1400}, {"step": 8, "height_mm": 1400}], 1, "height_mm"),
    ],
)
def test_invalid_control_points_422_located_to_point(points, idx, field):
    r = post({**BASE, "control_points": points})
    assert r.status_code == 422
    locs = [tuple(e["loc"]) for e in r.json()["detail"]]
    assert ("body", "control_points", idx, field) in locs


def test_control_point_riser_out_of_bounds_422_field_feedback():
    """控制点导致单级高度越界：422 定位到具体控制点并说明越界级与计算高度。"""
    r = post({**BASE, "control_points": [{"step": 1, "height_mm": 200}]})
    assert r.status_code == 422
    detail = r.json()["detail"]
    locs = [tuple(e["loc"]) for e in detail]
    assert ("body", "control_points", 0, "height_mm") in locs
    msg = "；".join(e["msg"] for e in detail)
    assert "第 1 级" in msg and "200" in msg and "高于上限" in msg and "190" in msg


def test_control_point_half_mm_riser_out_of_bounds_422():
    """半毫米计算高度越界时信息给出该计算高度（175 < 178）。"""
    r = post({**BASE, "riser_min_mm": 178,
              "control_points": [{"step": 8, "height_mm": 1400}]})
    assert r.status_code == 422
    locs = [tuple(e["loc"]) for e in r.json()["detail"]]
    assert ("body", "control_points", 0, "height_mm") in locs
    msg = "；".join(e["msg"] for e in r.json()["detail"])
    assert "175" in msg and "低于下限" in msg and "178" in msg


def test_control_point_second_segment_violation_locates_second_point():
    # 0→8 与 8→12 段合规，12→17 段平均 190.4 > 190：定位到第二个控制点
    points = [{"step": 8, "height_mm": 1400}, {"step": 12, "height_mm": 2048}]
    r = post({**BASE, "control_points": points})
    assert r.status_code == 422
    locs = [tuple(e["loc"]) for e in r.json()["detail"]]
    assert ("body", "control_points", 1, "height_mm") in locs


@pytest.mark.parametrize("bad", [
    {"step": "8", "height_mm": 1400},
    {"step": 8, "height_mm": 1400.5},
    {"step": True, "height_mm": 1},
    {"step": 8},
    "junk",
])
def test_control_points_wrong_type_returns_422(bad):
    assert post({**BASE, "control_points": [bad]}).status_code == 422


def test_control_points_with_no_solution_keep_no_solution():
    r = post({**BASE, "riser_min_mm": 170, "riser_max_mm": 172,
              "control_points": [{"step": 3, "height_mm": 500}]})
    assert r.status_code == 200
    assert r.json()["status"] == "no_solution"
