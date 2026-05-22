#!/usr/bin/env python3
import asyncio
from aiohttp import web, ClientSession, TCPConnector, ClientTimeout
from urllib.parse import urlparse
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

LANDING_DOC = "https://arena.ai/"
PORT = 8080

DROP_HEADERS = {"host", "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
                "te", "trailer", "transfer-encoding", "upgrade", "forwarded",
                "x-forwarded-host", "x-forwarded-proto", "x-forwarded-port"}

INP_HEADERS = {"x-host"}

def weave_url(hint, path, query):
    if hint.startswith(('http://', 'https://')):
        return f"{hint}{path}{query}"
    use_https = True
    if ':' in hint:
        port = hint.split(':')[-1]
        if port in ['80', '8080']:
            use_https = False
    proto = "https://" if use_https else "http://"
    return f"{proto}{hint}{path}{query}"

async def handle_request(request):
    hint = request.headers.get('x-host') or request.headers.get('X-Host')
    path = request.path
    query = f"?{request.query_string}" if request.query_string else ""

    # Landing page
    if path == "/" and not hint:
        try:
            async with ClientSession() as session:
                async with session.get(LANDING_DOC, timeout=ClientTimeout(total=10)) as resp:
                    return web.Response(text=await resp.text(), content_type='text/html')
        except:
            return web.Response(text="Landing page error", status=500)

    if not hint:
        return web.Response(text="Invalid Request: Missing x-host", status=400)

    target_url = weave_url(hint, path, query)
    logger.info(f"→ Forwarding to: {target_url}")

    # Filter headers
    forward_headers = {}
    for name, value in request.headers.items():
        name_lower = name.lower()
        if name_lower in DROP_HEADERS or name_lower in INP_HEADERS:
            continue
        if any(name_lower.startswith(p) for p in ["x-nf-", "x-netlify-"]):
            continue
        forward_headers[name] = value

    try:
        body = await request.read() if request.method not in ('GET', 'HEAD') else None

        async with ClientSession(
            connector=TCPConnector(ssl=False),
            timeout=ClientTimeout(total=300)
        ) as session:
            async with session.request(
                method=request.method,
                url=target_url,
                headers=forward_headers,
                data=body,
                allow_redirects=False
            ) as resp:
                resp_headers = {k: v for k, v in resp.headers.items() 
                               if k.lower() != 'transfer-encoding'}
                resp_body = await resp.read()
                return web.Response(body=resp_body, status=resp.status, headers=resp_headers)

    except Exception as e:
        logger.error(f"Error: {e}")
        return web.Response(text=f"Bad Gateway: {str(e)}", status=502)

app = web.Application(client_max_size=100*1024*1024)
app.router.add_route('*', '/{path:.*}', handle_request)

if __name__ == '__main__':
    logger.info(f"🚀 Starting Relay Proxy on port {PORT}")
    web.run_app(app, host='0.0.0.0', port=PORT)