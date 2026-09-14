"""端到端验收：真实浏览器（Chromium）经 Web 代理访问 API，覆盖主链路。

默认基址 http://web（compose 网络内由 nginx 反代到 API），可用 WEB_BASE_URL 覆盖。
"""
import os

import pytest
from playwright.sync_api import expect, sync_playwright

WEB = os.environ.get("WEB_BASE_URL", "http://web")


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture()
def page(browser):
    pg = browser.new_page()
    yield pg
    pg.close()


def fill_form(page, **fields):
    for testid, value in fields.items():
        page.get_by_test_id(testid).fill(str(value))


def riser_cell(page, row):
    return page.get_by_test_id(f"riser-row-{row}").locator("td").nth(1)


def test_main_flow_through_proxy(page):
    """主链路：浏览器表单 → Web 代理 → API → 页面展示唯一方案与候选淘汰原因。"""
    page.goto(WEB)
    fill_form(
        page,
        **{
            "floor-height": 3000,
            "run-length": 4800,
            "riser-min": 150,
            "riser-max": 190,
            "tread-min": 250,
            "tread-max": 320,
            "target-riser": 175,
        },
    )
    # 唯一踏步数
    expect(page.get_by_test_id("step-count")).to_have_text("17")
    # 逐级高度：3000 = 17*176 + 8，前 8 级 177，其余 176
    for i in range(1, 9):
        expect(riser_cell(page, i)).to_have_text("177")
    for i in range(9, 18):
        expect(riser_cell(page, i)).to_have_text("176")
    # 高度合计校验 = 层高
    expect(page.get_by_test_id("solution")).to_contain_text("3000 mm")
    # 踏面放样取值（4800/16 = 300）
    expect(page.get_by_test_id("tread-display")).to_have_text("300 mm")
    # 候选表 39 行且唯一选中
    expect(page.locator('[data-testid^="candidate-row-"]')).to_have_count(39)
    expect(page.get_by_test_id("candidate-selected")).to_have_count(1)
    # 淘汰原因示例：2 级时精确踏步高度 1500mm 高于上限；40 级时 75mm 低于下限
    expect(page.get_by_test_id("candidate-row-2")).to_contain_text("高于上限")
    expect(page.get_by_test_id("candidate-row-40")).to_contain_text("低于下限")
    # 可行但未选中：18 级给出偏差原因
    expect(page.get_by_test_id("candidate-row-18")).to_contain_text("偏差")


def test_invalid_input_clears_result_immediately(page):
    page.goto(WEB)
    expect(page.get_by_test_id("solution")).to_be_visible()  # 默认值合法，自动出结果
    page.get_by_test_id("floor-height").fill("0")
    expect(page.get_by_test_id("floor-height-error")).to_be_visible()
    expect(page.get_by_test_id("solution")).to_have_count(0)
    # 非整数同样非法
    page.get_by_test_id("floor-height").fill("3000.5")
    expect(page.get_by_test_id("floor-height-error")).to_be_visible()
    expect(page.get_by_test_id("solution")).to_have_count(0)
    # 恢复合法后结果重新出现
    page.get_by_test_id("floor-height").fill("3000")
    expect(page.get_by_test_id("solution")).to_be_visible()


def test_interval_inversion_clears_result(page):
    page.goto(WEB)
    expect(page.get_by_test_id("solution")).to_be_visible()
    page.get_by_test_id("riser-min").fill("200")  # 下限 200 > 上限 190
    expect(page.get_by_test_id("riser-max-error")).to_have_text("区间下限不得大于上限")
    expect(page.get_by_test_id("solution")).to_have_count(0)


def test_oversized_integer_explicitly_rejected(page):
    """9007199254740993（2^53+1）超出浏览器安全整数范围：必须明确拒绝，
    不得静默按 9007199254740992 计算。"""
    page.goto(WEB)
    expect(page.get_by_test_id("solution")).to_be_visible()
    page.get_by_test_id("floor-height").fill("9007199254740993")
    expect(page.get_by_test_id("floor-height-error")).to_contain_text("9007199254740991")
    expect(page.get_by_test_id("solution")).to_have_count(0)
    # 边界值 2^53-1 仍合法：不报字段错误（物理上无候选 → 无法放样结论）
    page.get_by_test_id("floor-height").fill("9007199254740991")
    expect(page.get_by_test_id("floor-height-error")).to_have_count(0)
    expect(page.get_by_test_id("no-solution")).to_be_visible()


def test_no_solution_shows_only_conclusion(page):
    page.goto(WEB)
    page.get_by_test_id("riser-min").fill("170")
    page.get_by_test_id("riser-max").fill("172")
    expect(page.get_by_test_id("no-solution")).to_be_visible()
    expect(page.get_by_test_id("no-solution")).to_contain_text("无法放样")
    # 只给出结论：不展示方案与候选表
    expect(page.get_by_test_id("solution")).to_have_count(0)
    expect(page.get_by_test_id("candidate-table")).to_have_count(0)


def test_tie_break_prefers_fewer_steps(page):
    page.goto(WEB)
    fill_form(
        page,
        **{
            "floor-height": 2520,
            "run-length": 5000,
            "riser-min": 200,
            "riser-max": 300,
            "tread-min": 100,
            "tread-max": 1000,
            "target-riser": 266,
        },
    )
    # 2520/9=280 与 2520/10=252 距 266 偏差同为 14mm，取踏步数较小者
    expect(page.get_by_test_id("step-count")).to_have_text("9")
    expect(page.get_by_test_id("candidate-row-10")).to_contain_text("踏步数多于")


def test_boundary_and_rounding_display(page):
    page.goto(WEB)
    fill_form(
        page,
        **{
            "floor-height": 3000,
            "run-length": 4808,
            "riser-min": 150,
            "riser-max": 150,
            "tread-min": 250,
            "tread-max": 320,
            "target-riser": 150,
        },
    )
    # 边界值有效：3000/20 = 150 恰好等于上下限
    expect(page.get_by_test_id("step-count")).to_have_text("20")
    # 4808/19 ≈ 253.05 → 四舍五入显示 253
    expect(page.get_by_test_id("tread-display")).to_have_text("253 mm")
