#!/usr/bin/env python3
import asyncio
import logging
from aiohttp import web, ClientSession, TCPConnector, ClientTimeout
from urllib.parse import urlparse

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("advanced-relay")

PORT = 8080
LANDING_DOC = "https://arena.ai/"

DROP_HEADERS = {
    "host", "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade", "forwarded",
    "x-forwarded-host", "x-forwarded-proto", "x-forwarded-port"
}

INP_HEADERS = {"x-host"}
PREFIX_SKIP = ["x-nf-", "x-netlify-"]


def weave_url(hint: str, path: str, query: str) -> str:
    if hint.startswith(("http://", "https://")):
        return f"{hint}{path}{query}"

    use_https = True
    if ":" in hint:
        port = hint.rsplit(":", 1)[-1]
        if port.isdigit() and int(port) in (80, 8080, 8880):
            use_https = False
    proto = "https://" if use_https else "http://"
    return f"{proto}{hint}{path}{query}"


def filter_headers(headers: dict) -> tuple[dict, str | None]:
    out = {}
    client_ip = None

    for k, v in headers.items():
        k_lower = k.lower()

        if k_lower in DROP_HEADERS or k_lower in INP_HEADERS:
            continue
        if any(k_lower.startswith(p) for p in PREFIX_SKIP):
            continue

        if k_lower == "x-real-ip":
            client_ip = v
            continue
        if k_lower == "x-forwarded-for" and not client_ip:
            client_ip = v.split(",")[0].strip()
            continue

        out[k] = v

    return out, client_ip


async def handle_request(request):
    hint = request.headers.get("x-host") or request.headers.get("X-Host")
    path = request.path
    query = f"?{request.query_string}" if request.query_string else ""

    # Landing page
    if path == "/" and not hint:
        try:
            async with ClientSession() as session:
                async with session.get(LANDING_DOC, timeout=ClientTimeout(total=8)) as resp:
                    return web.Response(text=await resp.text(), content_type="text/html")
        except:
            return web.Response(text="Landing Error", status=500)

    if not hint:
        logger.warning("Missing x-host header")
        return web.Response(text="Invalid Request: Missing x-host", status=400)

    target_url = weave_url(hint, path, query)
    logger.info(f"→ Target: {target_url}")

    fwd_headers, client_ip = filter_headers(dict(request.headers))
    if client_ip:
        fwd_headers["X-Forwarded-For"] = client_ip

    try:
        body = await request.read() if request.method not in ("GET", "HEAD") else None

        async with ClientSession(
            connector=TCPConnector(ssl=False, limit=100),
            timeout=ClientTimeout(total=300)
        ) as session:
            async with session.request(
                method=request.method,
                url=target_url,
                headers=fwd_headers,
                data=body,
                allow_redirects=False
            ) as resp:
                resp_headers = {k: v for k, v in resp.headers.items()
                               if k.lower() != "transfer-encoding"}
                resp_body = await resp.read()
                return web.Response(body=resp_body, status=resp.status, headers=resp_headers)

    except Exception as e:
        logger.error(f"Forwarding error: {e}")
        return web.Response(text=f"Bad Gateway: {str(e)}", status=502)


app = web.Application(client_max_size=200 * 1024 * 1024)
app.router.add_route("*", "/{path:.*}", handle_request)

if __name__ == "__main__":
    logger.info(f"🚀 Advanced Relay Proxy started on port {PORT}")
    web.run_app(app, host="0.0.0.0", port=PORT)