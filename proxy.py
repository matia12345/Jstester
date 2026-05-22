#!/usr/bin/env python3
import asyncio
import aiohttp
from aiohttp import web
import logging
import sys

# لاگ کامل
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

LANDING_DOC = "https://arena.ai/"
PORT = 8080

DROP_HEADERS = {
    "host", "connection", "keep-alive", "proxy-authenticate",
    "proxy-authorization", "te", "trailer", "transfer-encoding",
    "upgrade", "forwarded", "x-forwarded-host", 
    "x-forwarded-proto", "x-forwarded-port"
}

INP_HEADERS = {"x-host"}
PREFIX_SKIP = ["x-nf-", "x-netlify-"]

def should_drop_header(name):
    name_lower = name.lower()
    if name_lower in DROP_HEADERS or name_lower in INP_HEADERS:
        return True
    for prefix in PREFIX_SKIP:
        if name_lower.startswith(prefix):
            return True
    return False

def weave_url(host_header, path, query):
    """بازسازی URL مقصد"""
    if host_header.startswith(('http://', 'https://')):
        url = f"{host_header}{path}{query}"
        logger.info(f"🔗 URL with protocol: {url}")
        return url
    
    # تشخیص HTTPS
    use_https = True
    if ':' in host_header:
        port = host_header.split(':')[-1]
        if port in ['80', '8080', '8880']:
            use_https = False
        logger.info(f"🔍 Detected port: {port}, Using HTTPS: {use_https}")
    
    protocol = "https://" if use_https else "http://"
    url = f"{protocol}{host_header}{path}{query}"
    logger.info(f"🔗 Built URL: {url}")
    return url

def sieve_headers(headers):
    """فیلتر و پاکسازی header ها"""
    out_headers = {}
    client_ip = None
    
    for name, value in headers.items():
        name_lower = name.lower()
        
        if should_drop_header(name):
            logger.debug(f"🗑️  Dropped header: {name}")
            continue
            
        if name_lower == "x-real-ip":
            client_ip = value
            logger.debug(f"📍 Client IP from X-Real-IP: {client_ip}")
            continue
            
        if name_lower == "x-forwarded-for":
            if not client_ip:
                client_ip = value.split(',')[0].strip()
                logger.debug(f"📍 Client IP from X-Forwarded-For: {client_ip}")
            continue
        
        out_headers[name] = value
    
    logger.debug(f"✅ Forwarding {len(out_headers)} headers")
    return out_headers, client_ip

async def handle_request(request):
    """هندلر اصلی درخواست‌ها"""
    hint = request.headers.get('x-host') or request.headers.get('X-Host')
    path = request.path
    query = f"?{request.query_string}" if request.query_string else ""
    
    logger.info("="*60)
    logger.info(f"📨 NEW REQUEST")
    logger.info(f"   Method: {request.method}")
    logger.info(f"   Path: {path}")
    logger.info(f"   Query: {query}")
    logger.info(f"   X-Host: {hint}")
    logger.info(f"   Client: {request.remote}")
    logger.info("="*60)
    
    # صفحه landing
    if path == "/" and not hint:
        upgrade = request.headers.get('Upgrade', '').lower()
        if upgrade == 'websocket':
            logger.warning("⚠️  WebSocket upgrade on landing page")
            return web.Response(text="WebSocket upgrade not allowed on landing", status=400)
        
        try:
            logger.info("🏠 Fetching landing page...")
            async with aiohttp.ClientSession() as session:
                async with session.get(LANDING_DOC, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    html = await resp.text()
                    logger.info("✅ Landing page served")
                    return web.Response(
                        text=html,
                        content_type='text/html; charset=UTF-8'
                    )
        except Exception as e:
            logger.error(f"❌ Landing page error: {e}")
            return web.Response(text="Error loading landing page", status=500)
    
    # بررسی وجود x-host
    if not hint:
        logger.warning("⚠️  Missing x-host header")
        return web.Response(
            text="Invalid Request: Missing target host.",
            status=400
        )
    
    # ساخت URL مقصد
    target_url = weave_url(hint, path, query)
    logger.info(f"🎯 Target: {target_url}")
    
    # آماده‌سازی headers
    forward_headers, client_ip = sieve_headers(request.headers)
    if client_ip:
        forward_headers['X-Forwarded-For'] = client_ip
        logger.info(f"📍 Added X-Forwarded-For: {client_ip}")
    
    # نمایش headers که forward میشن
    logger.debug("📤 Forwarding headers:")
    for k, v in forward_headers.items():
        logger.debug(f"   {k}: {v[:100] if len(str(v)) > 100 else v}")
    
    try:
        # خواندن body
        body = None
        if request.method not in ('GET', 'HEAD'):
            body = await request.read()
            logger.info(f"📦 Body size: {len(body)} bytes")
        
        # ارسال درخواست به upstream
        timeout = aiohttp.ClientTimeout(total=300)
        connector = aiohttp.TCPConnector(ssl=False)  # 🔓 غیرفعال کردن SSL verify
        
        logger.info(f"🚀 Sending request to upstream...")
        
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            try:
                async with session.request(
                    method=request.method,
                    url=target_url,
                    headers=forward_headers,
                    data=body,
                    allow_redirects=False,
                ) as upstream_resp:
                    
                    logger.info(f"✅ Upstream response: {upstream_resp.status}")
                    
                    # آماده‌سازی response headers
                    resp_headers = {}
                    for name, value in upstream_resp.headers.items():
                        if name.lower() != 'transfer-encoding':
                            resp_headers[name] = value
                    
                    logger.debug(f"📥 Response headers: {len(resp_headers)}")
                    
                    # خواندن response body
                    resp_body = await upstream_resp.read()
                    logger.info(f"📦 Response body: {len(resp_body)} bytes")
                    
                    return web.Response(
                        body=resp_body,
                        status=upstream_resp.status,
                        headers=resp_headers
                    )
                    
            except aiohttp.ClientConnectorError as e:
                logger.error(f"❌ Connection error to {target_url}")
                logger.error(f"   Error: {e}")
                logger.error(f"   Type: {type(e).__name__}")
                return web.Response(
                    text=f"Cannot connect to upstream server: {hint}\nError: {str(e)}",
                    status=502
                )
            except aiohttp.ClientSSLError as e:
                logger.error(f"❌ SSL error to {target_url}")
                logger.error(f"   Error: {e}")
                return web.Response(
                    text=f"SSL error connecting to upstream: {hint}\nError: {str(e)}",
                    status=502
                )
                
    except asyncio.TimeoutError:
        logger.error(f"⏱️  Timeout connecting to {target_url}")
        return web.Response(text="Gateway Timeout", status=504)
    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}")
        logger.exception("Full traceback:")
        return web.Response(
            text=f"Bad Gateway: {str(e)}",
            status=502
        )

async def init_app():
    """ایجاد application"""
    app = web.Application(client_max_size=50*1024*1024)
    app.router.add_route('*', '/{path:.*}', handle_request)
    return app

if __name__ == '__main__':
    logger.info("="*60)
    logger.info(f"🚀 Starting VLESS Proxy Server")
    logger.info(f"   Port: {PORT}")
    logger.info(f"   Landing: {LANDING_DOC}")
    logger.info("="*60)
    
    app = asyncio.run(init_app())
    web.run_app(app, host='0.0.0.0', port=PORT, access_log=logger)