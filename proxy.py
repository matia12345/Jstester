#!/usr/bin/env python3
import asyncio
from aiohttp import web, ClientSession, TCPConnector, ClientTimeout
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

PORT = 8080

async def handle(request):
    logger.info("=" * 60)
    logger.info(f"NEW REQUEST: {request.method} {request.path}")
    logger.info(f"Remote: {request.remote}")
    
    # نمایش تمام هدرها
    logger.info("=== All Headers ===")
    for k, v in request.headers.items():
        logger.info(f"  {k}: {v}")
    logger.info("===================")
    
    # جستجوی x-host
    hint = request.headers.get('x-host') or request.headers.get('X-Host')
    logger.info(f"x-host found: {hint}")
    
    if not hint:
        logger.warning("❌ Missing x-host header!")
        return web.Response(text="Missing x-host", status=400)

    # ساخت URL مقصد
    if not hint.startswith(('http://', 'https://')):
        hint = 'https://' + hint
    
    target_url = hint + request.path_qs
    logger.info(f"→ Forwarding to: {target_url}")

    try:
        body = await request.read() if request.method not in ('GET', 'HEAD') else None
        
        async with ClientSession(connector=TCPConnector(ssl=False)) as session:
            async with session.request(
                method=request.method,
                url=target_url,
                headers={k: v for k, v in request.headers.items() if k.lower() != 'host'},
                data=body,
                allow_redirects=False
            ) as resp:
                resp_body = await resp.read()
                logger.info(f"✅ Response: {resp.status}")
                return web.Response(body=resp_body, status=resp.status)
                
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        return web.Response(text=str(e), status=502)

app = web.Application()
app.router.add_route('*', '/{path:.*}', handle)

if __name__ == '__main__':
    logger.info("🚀 Debug Proxy started")
    web.run_app(app, host='0.0.0.0', port=PORT)