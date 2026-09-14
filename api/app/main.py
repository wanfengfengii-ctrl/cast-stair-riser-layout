"""FastAPI 入口：所有响应均由输入实时计算，无任何固定响应。"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .logic import LayoutParams, compute_layout
from .schemas import LayoutRequest

app = FastAPI(title="混凝土楼梯支模放样 API", version="1.0.0")

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
    params = LayoutParams(**request.model_dump())
    return compute_layout(params)
