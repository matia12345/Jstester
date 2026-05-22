#!/usr/bin/env python3
import asyncio
import aiohttp
from aiohttp import web
import logging

logging.basicConfig(level=logging.INFO)
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
    # اگر پروتکل داشت
    if host_header.startswith(('http://', 'https://')):
        return f"{host_header}{path}{query}"
    
    # تشخیص HTTPS
    use_https = True
    if ':' in host_header:
        port = host_header.split(':')[-1]
        if port in ['80', '8080', '8880']:
            use_https = False
    
    protocol = "https://" if use_https else "http://"
    return f"{protocol}{host_header}{path}{query}"

def sieve_headers(headers):
    """فیلتر و پاکسازی header ها"""
    out_headers = {}
    client_ip = None
    
    for name, value in headers.items():
        name_lower = name.lower()
        
        if should_drop_header(name):
            continue
            
        if name_lower == "x-real-ip":
            client_ip = value
            continue
            
        if name_lower == "x-forwarded-for":
            if not client_ip:
                client_ip = value.split(',')[0].strip()
            continue
        
        out_headers[name] = value
    
    return out_headers, client_ip

async def handle_request(request):
    """هندلر اصلی درخواست‌ها"""
    hint = request.headers.get('x-host') or request.headers.get('X-Host')
    path = request.path
    query = f"?{request.query_string}" if request.query_string else ""
    
    # صفحه landing
    if path == "/" and not hint:
        # چک کردن WebSocket
        upgrade = request.headers.get('Upgrade', '').lower()
        if upgrade == 'websocket':
            return web.Response(text="WebSocket upgrade not allowed on landing", status=400)
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(LANDING_DOC, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    html = await resp.text()
                    return web.Response(
                        text=html,
                        content_type='text/html; charset=UTF-8'
                    )
        except Exception as e:
            logger.error(f"Error fetching landing page: {e}")
            return web.Response(text="Error loading landing page", status=500)
    
    # بررسی وجود x-host
    if not hint:
        return web.Response(
            text="Invalid Request: Missing target host.",
            status=400
        )
    
    # ساخت URL مقصد
    target_url = weave_url(hint, path, query)
    logger.info(f"{request.method} {path} -> {target_url}")
    
    # آماده‌سازی headers
    forward_headers, client_ip = sieve_headers(request.headers)
    if client_ip:
        forward_headers['X-Forwarded-For'] = client_ip
    
    try:
        # خواندن body
        body = None
        if request.method not in ('GET', 'HEAD'):
            body = await request.read()
        
        # ارسال درخواست به upstream
        timeout = aiohttp.ClientTimeout(total=300)  # 5 دقیقه timeout
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(
                method=request.method,
                url=target_url,
                headers=forward_headers,
                data=body,
                allow_redirects=False,
                ssl=False  # برای اجازه دادن به self-signed certificates
            ) as upstream_resp:
                
                # آماده‌سازی response headers
                resp_headers = {}
                for name, value in upstream_resp.headers.items():
                    if name.lower() != 'transfer-encoding':
                        resp_headers[name] = value
                
                # خواندن response body
                resp_body = await upstream_resp.read()
                
                return web.Response(
                    body=resp_body,
                    status=upstream_resp.status,
                    headers=resp_headers
                )
                
    except asyncio.TimeoutError:
        logger.error(f"Timeout connecting to {target_url}")
        return web.Response(text="Gateway Timeout", status=504)
    except Exception as e:
        logger.error(f"Proxy error for {target_url}: {e}")
        return web.Response(text="Bad Gateway", status=502)

async def init_app():
    """ایجاد application"""
    app = web.Application(client_max_size=50*1024*1024)  # 50MB max
    app.router.add_route('*', '/{path:.*}', handle_request)
    return app

if __name__ == '__main__':
    logger.info(f"🚀 Starting proxy server on port {PORT}")
    app = asyncio.run(init_app())
    web.run_app(app, host='0.0.0.0', port=PORT, access_log=logger)