"""混凝土楼梯支模放样核心计算。

设计原则：
- 可行性判断与方案选择全部使用 Fraction 精确分数运算，不做任何舍入；
- 仅展示层的踏面深度按四舍五入（ROUND_HALF_UP）取整到 1mm；
- 放样序列以层高整除踏步数的商为基础，余数从第一级起每级 +1mm，
  保证总和等于层高且任意两级高度差不超过 1mm；
- 推荐项始终由目标偏差排序得出；现场可在可行候选中人工改选，
  改选不改变推荐项本身（响应同时保留推荐踏步数与选用来源）；
- 现场复测得到中间级已知累计标高时，可携带若干控制点：起点（0 级 0 标高）、
  层高终点与控制点按级号分段，段内第 j 级累计增量取 j×段高差÷段级数并按
  半毫米向上取整，控制点精确命中，逐级高度仍须落入原踏步高度闭区间。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction

MIN_STEPS = 2
MAX_STEPS = 40

SOURCE_AUTO = "auto"
SOURCE_MANUAL = "manual"


class InvalidSelectionError(ValueError):
    """人工指定的 selected_steps 不属于当前输入的可行候选（越界或已被约束淘汰）。"""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class ControlPointError(ValueError):
    """中间标高控制点非法（级号/标高不满足约束），或受控后某级高度越界。

    index 为该控制点在请求 control_points 列表中的下标（用于 422 定位），
    loc_field 指明定位于该控制点的哪个字段；越界场景另外携带越界级号与
    以半毫米为单位的计算高度，供错误信息说明。
    """

    def __init__(self, message: str, index: int | None = None,
                 loc_field: str = "height_mm", control_step: int | None = None,
                 control_height: int | None = None,
                 riser_step: int | None = None, riser_half: int | None = None):
        self.message = message
        self.index = index
        self.loc_field = loc_field
        self.control_step = control_step
        self.control_height = control_height
        self.riser_step = riser_step
        self.riser_half = riser_half
        super().__init__(message)


@dataclass(frozen=True)
class LayoutParams:
    """放样输入，所有尺寸均为正整数毫米，区间为闭区间。"""

    floor_height_mm: int   # 层高
    run_length_mm: int     # 水平可用长度
    riser_min_mm: int      # 踏步高度下限
    riser_max_mm: int      # 踏步高度上限
    tread_min_mm: int      # 踏面深度下限
    tread_max_mm: int      # 踏面深度上限
    target_riser_mm: int   # 目标踏步高度


def _fmt(value: Fraction) -> str:
    """分数的简短文本：整数不带小数点，否则保留两位小数。"""
    if value.denominator == 1:
        return str(value.numerator)
    return f"{float(value):.2f}"


def _round_half_up_mm(value: Fraction) -> int:
    """四舍五入到 1mm（仅用于踏面深度的展示取值）。"""
    decimal_value = Decimal(value.numerator) / Decimal(value.denominator)
    return int(decimal_value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _half_to_mm(value: int):
    """半毫米网格值转毫米展示：偶数为整数毫米，奇数保留 0.5mm。"""
    return value // 2 if value % 2 == 0 else value / 2


def _half_fmt(value: int) -> str:
    """半毫米网格值的毫米文本（错误信息用）。"""
    return f"{value // 2}" if value % 2 == 0 else f"{value / 2:.1f}"


def _controlled_sequence(steps: int, total_height: int,
                         control_points: list[tuple[int, int]]) -> tuple[list[int], list[int]]:
    """按中间标高控制点生成长度为 steps 的逐级序列（半毫米网格）。

    起点（0 级，0 标高）、层高终点（steps 级，total_height）与控制点按级号
    分段。对每段 [a, b]，记段高差 Δ（半毫米）、段级数 m = b−a，第 j 级
    （j = 1..m）相对段起点的累计增量为 ceil(j·Δ/m)。这样各控制点（含层高
    终点）的累计标高精确命中，段内任一累计值相对该段理想直线的偏差不超过
    0.5mm，且结果只由级号唯一确定。

    返回 (逐级高度, 累计标高)，均以半毫米为单位：控制点与首末标高为整数
    毫米（偶数格），段内逐级高度/累计可以是半毫米（奇数格）。
    """
    # (级号, 半毫米累计标高)；起点与层高终点补齐端点
    anchors = [(0, 0)]
    anchors.extend((step, 2 * height) for step, height in control_points)
    anchors.append((steps, 2 * total_height))

    cumulative_half: dict[int, int] = {0: 0}
    for (start_step, start_h), (end_step, end_h) in zip(anchors, anchors[1:]):
        span_steps = end_step - start_step
        span_height = end_h - start_h  # 已校验严格递增，必为正
        for j in range(1, span_steps + 1):
            inc = -((-j * span_height) // span_steps)  # ceil(j·Δ/m)
            cumulative_half[start_step + j] = start_h + inc

    sequence_half = [cumulative_half[i] - cumulative_half[i - 1] for i in range(1, steps + 1)]
    cumulative_list = [cumulative_half[i] for i in range(1, steps + 1)]
    return sequence_half, cumulative_list


def _build_solution(params: LayoutParams, steps: int, riser: Fraction,
                    deviation: Fraction, run: Fraction,
                    control_points: list[tuple[int, int]] | None = None) -> dict:
    """按指定踏步数生成逐级高度、累计标高与踏面取值。

    无控制点时使用商余序列（余数从第一级起每级 +1mm）；携带控制点时按
    _controlled_sequence 分段生成（半毫米网格，逐级高度可能为 0.5mm 的倍数），
    使各控制点累计标高精确命中。
    """
    controlled = bool(control_points)
    if controlled:
        sequence_half, cumulative_half = _controlled_sequence(
            steps, params.floor_height_mm, control_points
        )
        sequence = [_half_to_mm(h) for h in sequence_half]
        cumulative = [_half_to_mm(h) for h in cumulative_half]
    else:
        # 放样序列：商为基础，余数从第一级起每级 +1mm
        quotient, remainder = divmod(params.floor_height_mm, steps)
        sequence = [quotient + 1 if i < remainder else quotient for i in range(steps)]
        cumulative = []
        running = 0
        for step_height in sequence:
            running += step_height
            cumulative.append(running)

    exact_tread = run / (steps - 1)
    if controlled:
        max_diff = _half_to_mm(max(sequence_half) - min(sequence_half))
    else:
        max_diff = max(sequence) - min(sequence)
    solution = {
        "steps": steps,
        "treads": steps - 1,
        "exact_riser_mm": float(riser),
        "target_riser_mm": params.target_riser_mm,
        "deviation_mm": float(deviation),
        "riser_sequence_mm": sequence,
        "cumulative_height_mm": cumulative,
        "total_height_mm": cumulative_half[-1] // 2 if controlled else cumulative[-1],
        "max_riser_diff_mm": max_diff,
        "exact_tread_mm": float(exact_tread),
        "tread_display_mm": _round_half_up_mm(exact_tread),
        "run_length_mm": params.run_length_mm,
    }
    # 受控标识仅在携带控制点时出现，旧响应结构完全不变
    if controlled:
        solution["controlled"] = True
    return solution


def compute_layout(params: LayoutParams, selected_steps: int | None = None,
                   control_points: list[dict] | list[tuple[int, int]] | None = None) -> dict:
    """计算放样方案。

    返回 {"status": "ok"|"no_solution", "solution": ..., "candidates": [...],
    "recommended_steps": int|None, "selection_source": "auto"|"manual"|None}。
    candidates 覆盖 2..40 全部踏步数，含每个候选的可行性结论、推荐/选中标记与淘汰原因。

    始终先按目标偏差排序得出原推荐项；若传入 selected_steps，再校验其确属当前
    输入的可行候选，并以该踏步数生成放样序列。越界或不可行时抛 InvalidSelectionError。

    若传入非空 control_points（每项含级号与累计标高），则在选定踏步数之上按
    控制点分段生成逐级序列：级号与标高均须严格递增且位于首末级之间；生成的
    每级高度还须落入原踏步高度闭区间，否则抛 ControlPointError（可定位到具体
    控制点，并说明越界级号与计算高度）。不带控制点时，响应与商余序列完全不变。
    """
    height = Fraction(params.floor_height_mm)
    run = Fraction(params.run_length_mm)
    target = Fraction(params.target_riser_mm)

    candidates: list[dict] = []
    feasible: list[tuple[int, Fraction, dict]] = []
    for steps in range(MIN_STEPS, MAX_STEPS + 1):
        treads = steps - 1  # 踏面数固定为踏步数减一
        riser = height / steps
        tread = run / treads
        reasons: list[str] = []
        # 闭区间：边界值有效
        if riser < params.riser_min_mm:
            reasons.append(f"精确踏步高度 {_fmt(riser)}mm 低于下限 {params.riser_min_mm}mm")
        if riser > params.riser_max_mm:
            reasons.append(f"精确踏步高度 {_fmt(riser)}mm 高于上限 {params.riser_max_mm}mm")
        if tread < params.tread_min_mm:
            reasons.append(f"精确踏面深度 {_fmt(tread)}mm 低于下限 {params.tread_min_mm}mm")
        if tread > params.tread_max_mm:
            reasons.append(f"精确踏面深度 {_fmt(tread)}mm 高于上限 {params.tread_max_mm}mm")
        entry = {
            "steps": steps,
            "treads": treads,
            "exact_riser_mm": float(riser),
            "exact_tread_mm": float(tread),
            "deviation_mm": float(abs(riser - target)),
            "feasible": not reasons,
            "recommended": False,
            "selected": False,
            "reasons": reasons,
        }
        candidates.append(entry)
        if not reasons:
            feasible.append((steps, riser, entry))

    if not feasible:
        if selected_steps is not None:
            raise InvalidSelectionError(
                f"指定的 {selected_steps} 级踏步不可采用：当前输入在 "
                f"{MIN_STEPS}–{MAX_STEPS} 级范围内无可行候选"
            )
        return {
            "status": "no_solution",
            "solution": None,
            "candidates": candidates,
            "recommended_steps": None,
            "selection_source": None,
        }

    # 推荐项不变：先按 |精确踏步高度 - 目标值| 最小选择，平局取踏步数较小者（精确分数比较）
    best_steps, best_riser, best_entry = min(
        feasible, key=lambda item: (abs(item[1] - target), item[0])
    )
    best_deviation = abs(best_riser - target)
    best_entry["recommended"] = True

    # 可行但未获推荐者，给出相对推荐项的排序说明（与是否人工改选无关）
    for steps, riser, entry in feasible:
        if steps == best_steps:
            continue
        deviation = abs(riser - target)
        if deviation > best_deviation:
            entry["reasons"] = [
                f"可行，但与目标偏差 {_fmt(deviation)}mm 大于推荐的 "
                f"{best_steps} 级方案（偏差 {_fmt(best_deviation)}mm）"
            ]
        else:
            entry["reasons"] = [
                f"与目标偏差相同（{_fmt(deviation)}mm），但踏步数多于推荐的 {best_steps} 级方案"
            ]

    # 推荐项先得出后，再校验人工选用值
    if selected_steps is None:
        chosen_steps = best_steps
        source = SOURCE_AUTO
    else:
        chosen_steps = _validate_selected(selected_steps, candidates)
        source = SOURCE_MANUAL

    chosen_entry = next(entry for _, _, entry in feasible if entry["steps"] == chosen_steps)
    chosen_entry["selected"] = True
    chosen_riser = next(riser for steps, riser, _ in feasible if steps == chosen_steps)
    chosen_deviation = abs(chosen_riser - target)

    normalized_points = _normalize_control_points(control_points)
    if normalized_points:
        _validate_control_points(normalized_points, chosen_steps, params.floor_height_mm)
    solution = _build_solution(params, chosen_steps, chosen_riser, chosen_deviation, run,
                               control_points=normalized_points)
    if normalized_points:
        # 受控后逐级高度仍须落入原踏步高度闭区间，否则定位到相关控制点并说明越界级
        _check_controlled_heights(normalized_points, chosen_steps, params)

    result = {
        "status": "ok",
        "solution": solution,
        "candidates": candidates,
        "recommended_steps": best_steps,
        "selection_source": source,
    }
    # 控制点报告仅在携带控制点时出现，旧响应结构完全不变
    if normalized_points:
        result["control_points"] = _control_point_report(solution, normalized_points)
    return result


def _normalize_control_points(control_points) -> list[tuple[int, int]]:
    """规整控制点为 [(级号, 累计标高)]：空输入（None 或空列表）一律按无控制点处理。"""
    if not control_points:
        return []
    normalized: list[tuple[int, int]] = []
    for point in control_points:
        if isinstance(point, dict):
            normalized.append((int(point["step"]), int(point["height_mm"])))
        else:
            normalized.append((int(point[0]), int(point[1])))
    return normalized


def _control_point_report(solution: dict,
                          control_points: list[tuple[int, int]]) -> list[dict] | None:
    """受控标识与各控制点命中值；无控制点时为 None，旧响应不出现该字段。"""
    if not control_points:
        return None
    cumulative = solution["cumulative_height_mm"]
    return [
        {
            "step": step,
            "height_mm": height,
            "hit_height_mm": cumulative[step - 1],
            "deviation_mm": cumulative[step - 1] - height,
        }
        for step, height in control_points
    ]


def _fail_control(index: int, message: str, *, loc_field: str = "height_mm",
                  control_step: int | None = None, control_height: int | None = None,
                  riser_step: int | None = None, riser_half: int | None = None):
    raise ControlPointError(
        message, index=index, loc_field=loc_field,
        control_step=control_step, control_height=control_height,
        riser_step=riser_step, riser_half=riser_half,
    )


def _validate_control_points(control_points: list[tuple[int, int]],
                             steps: int, total_height: int) -> None:
    """校验控制点本身：位于首末级之间（级号 1..steps−1），级号与标高严格递增，
    且标高位于 (0, 层高) 之内。任一条件不满足即抛可定位到具体控制点的错误。"""
    prev_step = 0
    prev_height = 0
    for index, (step, point_height) in enumerate(control_points):
        label = f"第 {index + 1} 个控制点（{step} 级，{point_height}mm）"
        if not (1 <= step <= steps - 1):
            _fail_control(
                index, f"{label}：级号必须位于首末级之间（1–{steps - 1} 级），"
                f"不得取起点 0 级或末级 {steps} 级",
                loc_field="step", control_step=step, control_height=point_height,
            )
        if step <= prev_step:
            _fail_control(
                index, f"{label}：控制点级号必须严格递增，上一控制点为 {prev_step} 级",
                loc_field="step", control_step=step, control_height=point_height,
            )
        if not (0 < point_height < total_height):
            _fail_control(
                index, f"{label}：累计标高必须位于起点 0 与层高 {total_height}mm 之间（均不含）",
                control_step=step, control_height=point_height,
            )
        if point_height <= prev_height:
            _fail_control(
                index, f"{label}：控制点累计标高必须严格递增，上一控制点为 {prev_height}mm",
                control_step=step, control_height=point_height,
            )
        prev_step, prev_height = step, point_height


def _check_controlled_heights(control_points: list[tuple[int, int]],
                              steps: int, params: LayoutParams) -> None:
    """校验受控逐级高度落入 [riser_min, riser_max] 闭区间。

    逐级高度按半毫米网格生成，可能为 0.5mm 的倍数，故比较在半毫米整数网格上
    精确进行。越界级归属于以该控制点为右端点的分段，422 因此定位到该控制点
    （起点段越界时定位到第一个控制点），信息中说明越界级号与计算高度。
    """
    height = params.floor_height_mm
    anchors = [(0, 0), *control_points, (steps, height)]
    segment_ends = [step for step, _ in anchors[1:]]
    sequence_half, _ = _controlled_sequence(steps, height, control_points)
    min_half, max_half = 2 * params.riser_min_mm, 2 * params.riser_max_mm
    for i, value_half in enumerate(sequence_half):
        step_no = i + 1
        if min_half <= value_half <= max_half:
            continue
        end_index = next(k for k, end_step in enumerate(segment_ends) if step_no <= end_step)
        point_index = end_index if end_index < len(control_points) else len(control_points) - 1
        end_step, end_height = anchors[end_index + 1]
        start_step, start_height = anchors[end_index]
        if value_half < min_half:
            relation, bound = "低于下限", params.riser_min_mm
        else:
            relation, bound = "高于上限", params.riser_max_mm
        _fail_control(
            point_index,
            f"控制点 {start_step} 级（{start_height}mm）→ {end_step} 级（{end_height}mm）"
            f"分段内第 {step_no} 级计算高度为 {_half_fmt(value_half)}mm，{relation} {bound}mm，"
            f"超出踏步高度闭区间 [{params.riser_min_mm}, {params.riser_max_mm}]mm",
            control_step=control_points[point_index][0],
            control_height=control_points[point_index][1],
            riser_step=step_no, riser_half=value_half,
        )


def _validate_selected(selected_steps: int, candidates: list[dict]) -> int:
    """校验人工踏步数：必须是 2..40 内的整数且属于当前输入的可行候选。"""
    if not isinstance(selected_steps, int) or isinstance(selected_steps, bool):
        raise InvalidSelectionError(
            f"踏步数必须为 {MIN_STEPS}–{MAX_STEPS} 之间的整数"
        )
    if not (MIN_STEPS <= selected_steps <= MAX_STEPS):
        raise InvalidSelectionError(
            f"踏步数 {selected_steps} 超出允许范围 {MIN_STEPS}–{MAX_STEPS} 级"
        )
    entry = next(c for c in candidates if c["steps"] == selected_steps)
    if not entry["feasible"]:
        reason_text = "；".join(entry["reasons"])
        suffix = f"：{reason_text}" if reason_text else ""
        raise InvalidSelectionError(
            f"{selected_steps} 级踏步不是当前输入的可行候选{suffix}"
        )
    return selected_steps
