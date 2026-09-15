"""FastAPI 入口：所有响应均由输入实时计算，无任何固定响应。"""
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from .logic import (
    ControlPointError,
    InvalidSelectionError,
    LayoutParams,
    compute_layout,
)
from .schemas import LayoutRequest

app = FastAPI(title="混凝土楼梯支模放样 API", version="1.2.0")

# 生产环境经 nginx 同源代理，CORS 仅方便本地前后端分离开发
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/layout")
def layout(request: LayoutRequest) -> dict:
    params = LayoutParams(**request.model_dump(exclude={"selected_steps", "control_points"}))
    control_points = (
        [point.model_dump() for point in request.control_points]
        if request.control_points
        else None
    )
    try:
        return compute_layout(
            params,
            selected_steps=request.selected_steps,
            control_points=control_points,
        )
    except InvalidSelectionError as exc:
        # 与 Pydantic 校验错误同形的 422，错误可定位到 body.selected_steps
        raise RequestValidationError(
            [{"loc": ("body", "selected_steps"), "msg": exc.message, "type": "value_error"}]
        )
    except ControlPointError as exc:
        # 同样以 422 返回，定位到具体控制点（及字段），并说明越界级与计算高度
        loc = ("body", "control_points", exc.index, exc.loc_field)
        raise RequestValidationError(
            [{"loc": loc, "msg": exc.message, "type": "value_error"}]
        )
