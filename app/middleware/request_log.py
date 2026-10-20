"""API 请求日志中间件：记录 /api/* 请求到 ApiLog 表（管理平台 API 管理数据源）"""

import asyncio
import gzip
import re
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.security import decode_token
from app.db.session import SessionLocal
from app.models.api_log import ApiLog

_BODY_LIMIT = 2000
_RESP_LIMIT = 1000

# 敏感路径：请求体不落库（登录密码等）
_SENSITIVE_PATHS = {"/api/v1/auth/login", "/api/v1/auth/register"}


def _decode_resp_body(body: bytes) -> str:
    """响应体转文本：检测 gzip 压缩（GZipMiddleware 对大响应压缩）并解压，避免日志记录乱码。"""
    if body[:2] == b"\x1f\x8b":  # gzip magic number
        try:
            body = gzip.decompress(body)
        except Exception:  # noqa: BLE001
            pass
    return body.decode("utf-8", errors="replace")


def _mask_sensitive(text: str) -> str:
    """脱敏敏感字段值（password / token / authorization），防止明文落库。"""
    text = re.sub(r'(?i)("(?:password|passwd|new_password)"\s*:\s*")[^"]*(")', r"\1***\2", text)
    text = re.sub(r'(?i)("(?:token|access_token|refresh_token)"\s*:\s*")[^"]*(")', r"\1***\2", text)
    text = re.sub(r'(?i)("authorization"\s*:\s*")[^"]*(")', r"\1***\2", text)
    return text


class RequestLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        path = request.url.path
        # 在下游消费请求流之前读取并缓存，BaseHTTPMiddleware 会重放缓存的 body。
        # 下游处理后再 request.body() 会触发 Stream consumed，导致整个 POST 日志丢失。
        req_body = None
        if (
            path.startswith("/api/")
            and path not in _SENSITIVE_PATHS
            and request.method in ("POST", "PUT", "DELETE")
            and request.headers.get("content-type", "").startswith("application/json")
        ):
            try:
                raw = await request.body()
                req_body = _mask_sensitive(raw.decode("utf-8", errors="replace")[:_BODY_LIMIT])
            except Exception:  # noqa: BLE001
                pass
        # 仅记录业务 API
        if not path.startswith("/api/"):
            return await call_next(request)

        # SSE 响应必须保持真正的流式传输。BaseHTTPMiddleware 已将响应体
        # 暴露为迭代器；包装它只在消费结束或取消时记录一次元数据。
        # 事件内容可能包含用户数据，因此不为 SSE 读取或记录响应预览。
        response = await call_next(request)
        if "text/event-stream" in response.headers.get("content-type", "").lower():
            body_iterator = response.body_iterator

            async def logged_stream():
                try:
                    async for chunk in body_iterator:
                        yield chunk
                finally:
                    write_task = asyncio.create_task(
                        asyncio.to_thread(
                            self._write_log,
                            request,
                            path,
                            response.status_code,
                            start,
                            req_body,
                            None,
                        )
                    )
                    try:
                        await asyncio.shield(write_task)
                    except asyncio.CancelledError:
                        await asyncio.shield(write_task)
                        raise

            response.body_iterator = logged_stream()
            return response

        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        try:
            # 响应预览（JSONResponse 可读 body；脱敏 token；gzip 压缩响应先解压）
            resp_preview = None
            try:
                body = b"".join([chunk async for chunk in response.body_iterator])
                resp_preview = _mask_sensitive(_decode_resp_body(body)[:_RESP_LIMIT])
                response.body_iterator = _iterate_bytes([body])
            except Exception:
                pass

            await asyncio.to_thread(
                self._write_log,
                request,
                path,
                response.status_code,
                start,
                req_body,
                resp_preview,
                duration_ms,
            )
        except Exception:
            pass

        return response

    @staticmethod
    def _write_log(request, path, status_code, start, req_body, resp_preview, duration_ms=None):
        """Persist log metadata off the event loop; failures never affect a response."""
        try:
            username = None
            auth = request.headers.get("authorization", "")
            if auth.startswith("Bearer "):
                payload = decode_token(auth[7:])
                if payload:
                    username = payload.get("sub")
            client_ip = request.client.host if request.client else None
            log = ApiLog(
                method=request.method,
                path=path,
                status_code=status_code,
                duration_ms=(
                    duration_ms if duration_ms is not None else round((time.perf_counter() - start) * 1000, 2)
                ),
                client_ip=client_ip,
                username=username,
                req_body=req_body,
                resp_preview=resp_preview,
            )
            db = SessionLocal()
            try:
                db.add(log)
                db.commit()
            finally:
                db.close()
        except Exception:
            pass


def _iterate_bytes(bodies):
    async def _agen():
        for b in bodies:
            yield b

    return _agen()
