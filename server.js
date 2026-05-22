import http from 'http';
import https from 'https';
import { URL } from 'url';

const PORT = 8080;
const LANDING_DOC = "https://arena.ai/";
const j = (...x) => x.join("-");

const DROP = new Set([
  "host", "connection", "keep-alive", "proxy-authenticate",
  "proxy-authorization", "te", "trailer", j("transfer","encoding"),
  "upgrade", "forwarded", j("x","forwarded","host"), 
  j("x","forwarded","proto"), j("x","forwarded","port")
]);

const INP = new Set(["x-host"]);
const PX = ["x-nf-", "x-netlify-"];
const FF = j("x","forwarded","for");

const weave = (h, u) => {
  const full = /^https?:\/\//i.test(h);
  const p = u.pathname, q = u.search || '';
  if (full) return `${h}${p}${q}`;
  const httpsy = (!h.includes(":")) || h.includes(":443") || 
                 h.includes(":444") || h.includes(":2096") ||
                 /^s\d+\./.test(h);
  return `${httpsy ? "https://" : "http://"}${h}${p}${q}`;
};

const sieve = (headers) => {
  const out = {};
  let ip = null;

  for (const [k0, v] of Object.entries(headers)) {
    const k = k0.toLowerCase();
    if (INP.has(k)) continue;
    if (DROP.has(k)) continue;
    if (PX.some(p => k.startsWith(p))) continue;

    if (k === "x-real-ip") { ip = v; continue; }
    if (k === FF) { 
      if (!ip) ip = v.split(',')[0].trim(); 
      continue; 
    }

    out[k] = v;
  }
  return { headers: out, ip };
};

const server = http.createServer(async (req, res) => {
  const reqUrl = new URL(req.url, `http://${req.headers.host}`);
  const hint = req.headers['x-host'];

  // Landing page
  if (reqUrl.pathname === '/' && !hint) {
    try {
      const response = await fetch(LANDING_DOC);
      const html = await response.text();
      res.writeHead(200, { 'content-type': 'text/html; charset=UTF-8' });
      return res.end(html);
    } catch (err) {
      res.writeHead(500);
      return res.end('Error');
    }
  }

  if (!hint) {
    res.writeHead(400);
    return res.end('Invalid Request: Missing target host.');
  }

  const endpoint = weave(hint, reqUrl);
  const { headers: fwd, ip } = sieve(req.headers);
  if (ip) fwd[FF] = ip;

  const targetUrl = new URL(endpoint);
  const isHttps = targetUrl.protocol === 'https:';
  const client = isHttps ? https : http;

  const options = {
    hostname: targetUrl.hostname,
    port: targetUrl.port || (isHttps ? 443 : 80),
    path: targetUrl.pathname + targetUrl.search,
    method: req.method,
    headers: fwd,
  };

  const proxyReq = client.request(options, (proxyRes) => {
    // Filter response headers
    const resHeaders = {};
    for (const [k, v] of Object.entries(proxyRes.headers)) {
      if (k.toLowerCase() !== j("transfer","encoding")) {
        resHeaders[k] = v;
      }
    }

    res.writeHead(proxyRes.statusCode, resHeaders);
    proxyRes.pipe(res);
  });

  proxyReq.on('error', (err) => {
    console.error('Proxy error:', err);
    if (!res.headersSent) {
      res.writeHead(502);
      res.end('Bad Gateway');
    }
  });

  req.pipe(proxyReq);
});

server.listen(PORT, () => {
  console.log(`🚀 Server running on http://localhost:${PORT}`);
});