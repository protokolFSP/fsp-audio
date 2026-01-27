// File: worker.js (Cloudflare Workers + Durable Object WITHOUT SQLite)
// ✅ COUNTERS ONLY (play/download) + CORS-safe
//
// Public:
//  - GET  /health
//  - POST /counts  body: { type: "play"|"download"|"both", ids: string[] }
//  - POST /hit     body: { type: "play"|"download", id: string, title?: string, file_name?: string }
//  - GET  /top?type=play|download&limit=10&cursor=0
//
// Admin:
//  - POST /reset   header: x-admin-token: <ADMIN_TOKEN>
//      body: { mode: "all" } OR { mode: "id", id: "<id>" }
//
// Bindings required:
//  - Durable Object binding name: COUNTERS_DO (class: CountersDO)
//  - No SQLite migration needed for this version
//  - Secret (optional): ADMIN_TOKEN

const MAX_ID_LEN = 800;
const MAX_TITLE_LEN = 300;
const MAX_FILE_LEN = 260;

const MAX_IDS_POST = 600;
const TOP_LIMIT_MAX = 50;

const MAX_BODY_BYTES = 32 * 1024;

const TOP_STORE_MAX = 500;
const LIST_PAGE_LIMIT = 512;

function corsHeaders() {
  return {
    "access-control-allow-origin": "*",
    "access-control-allow-methods": "GET,POST,OPTIONS",
    "access-control-allow-headers": "content-type,x-admin-token",
    "access-control-max-age": "86400",
    "cache-control": "no-store",
  };
}

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { ...corsHeaders(), "content-type": "application/json; charset=utf-8" },
  });
}

function text(msg, status = 200) {
  return new Response(msg, {
    status,
    headers: { ...corsHeaders(), "content-type": "text/plain; charset=utf-8" },
  });
}

function badRequest(message) {
  return json({ ok: false, error: message }, 400);
}

function normalizeType(v) {
  const t = (v || "").toLowerCase();
  return t === "play" || t === "download" || t === "both" ? t : null;
}

function normalizeTopType(v) {
  const t = (v || "").toLowerCase();
  if (t === "play") return "play";
  if (t === "download") return "download";
  return null;
}

function clampStr(s, maxLen) {
  if (typeof s !== "string") return "";
  return s.length <= maxLen ? s : s.slice(0, maxLen);
}

function sanitizeId(id) {
  const v = (id || "").trim();
  if (!v || v.length > MAX_ID_LEN) return "";
  if (v.includes("\u0000")) return "";
  return v;
}

function sanitizeIds(ids, maxCount) {
  const out = [];
  const seen = new Set();
  for (const raw of ids) {
    if (out.length >= maxCount) break;
    const id = sanitizeId(String(raw ?? ""));
    if (!id) continue;
    if (seen.has(id)) continue;
    seen.add(id);
    out.push(id);
  }
  return out;
}

function parseContentLength(request) {
  const h = request.headers.get("content-length");
  if (!h) return null;
  const n = Number(h);
  return Number.isFinite(n) ? n : null;
}

async function readJsonBody(request) {
  const cl = parseContentLength(request);
  if (cl != null && cl > MAX_BODY_BYTES) return { __tooLarge: true };

  try {
    const txt = await request.text();
    if (!txt) return null;
    if (txt.length > MAX_BODY_BYTES) return { __tooLarge: true };

    const parsed = JSON.parse(txt);
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch {
    return null;
  }
}

function requireAdmin(request, env) {
  const secret = (env?.ADMIN_TOKEN || "").toString();
  const token = (request.headers.get("x-admin-token") || "").toString();
  return !!secret && !!token && token === secret;
}

function getGlobalStub(env) {
  if (!env?.COUNTERS_DO) return null;
  const id = env.COUNTERS_DO.idFromName("global");
  return env.COUNTERS_DO.get(id);
}

function errToString(e) {
  try {
    if (!e) return "Unknown error";
    if (typeof e === "string") return e;
    return e.message ? String(e.message) : JSON.stringify(e);
  } catch {
    return "Unknown error";
  }
}

function counterKey(id) {
  return `c:${id}`;
}
function topKey(type) {
  return `top:${type}`;
}

function sortTopRows(rows) {
  rows.sort((a, b) => {
    const ca = Number(a.count) || 0;
    const cb = Number(b.count) || 0;
    if (ca !== cb) return cb - ca;
    const ua = Number(a.updatedAt) || 0;
    const ub = Number(b.updatedAt) || 0;
    if (ua !== ub) return ub - ua;
    return String(a.id).localeCompare(String(b.id));
  });
  return rows;
}

async function getMapValues(storage, keys) {
  const res = await storage.get(keys);
  if (res instanceof Map) return res;
  const m = new Map();
  for (const k of keys) m.set(k, res?.[k]);
  return m;
}

async function listKeysByPrefix(storage, prefix) {
  const out = [];
  let start = prefix;
  const end = prefix + "\uffff";

  while (true) {
    const page = await storage.list({ start, end, limit: LIST_PAGE_LIMIT });
    const keys = [...page.keys()];
    if (!keys.length) break;
    out.push(keys);
    const last = keys[keys.length - 1];
    start = last + "\u0000";
  }

  return out;
}

export class CountersDO {
  constructor(state, env) {
    this.state = state;
    this.env = env;
    this._init = null;
  }

  async init() {
    if (this._init) return this._init;
    this._init = this.state.blockConcurrencyWhile(async () => {});
    return this._init;
  }

  async fetch(request) {
    try {
      const url = new URL(request.url);

      if (request.method === "OPTIONS") {
        return new Response(null, { headers: corsHeaders() });
      }

      if (url.pathname === "/" || url.pathname === "/health") {
        if (request.method !== "GET") return text("Method not allowed", 405);
        return text("OK", 200);
      }

      await this.init();

      if (url.pathname === "/counts") {
        if (request.method !== "POST") return text("Method not allowed", 405);
        return this.routeCounts(request);
      }

      if (url.pathname === "/hit") {
        if (request.method !== "POST") return text("Method not allowed", 405);
        return this.routeHit(request);
      }

      if (url.pathname === "/top") {
        if (request.method !== "GET") return text("Method not allowed", 405);
        return this.routeTop(url);
      }

      if (url.pathname === "/reset") {
        if (request.method !== "POST") return text("Method not allowed", 405);
        return this.routeReset(request);
      }

      return text("Not found", 404);
    } catch (e) {
      return json({ ok: false, error: "DO exception", detail: errToString(e) }, 500);
    }
  }

  async routeCounts(request) {
    const body = await readJsonBody(request);
    if (body && body.__tooLarge) return json({ ok: false, error: "Body too large" }, 413);
    if (!body) return badRequest("JSON body bekleniyor");

    const type = normalizeType(body.type || "");
    if (!type) return badRequest("type play/download/both olmalı");

    const ids = sanitizeIds(Array.isArray(body.ids) ? body.ids : [], MAX_IDS_POST);
    if (!ids.length) {
      return json({ ok: true, type, counts: type === "both" ? { play: {}, download: {} } : {} }, 200);
    }

    const keys = ids.map(counterKey);
    const map = await getMapValues(this.state.storage, keys);

    const play = {};
    const download = {};

    for (let i = 0; i < ids.length; i++) {
      const id = ids[i];
      const v = map.get(keys[i]) || null;
      const pl = Number(v?.pl) || 0;
      const dl = Number(v?.dl) || 0;

      if (type === "play") play[id] = pl;
      else if (type === "download") download[id] = dl;
      else {
        play[id] = pl;
        download[id] = dl;
      }
    }

    if (type === "play") return json({ ok: true, type, counts: play }, 200);
    if (type === "download") return json({ ok: true, type, counts: download }, 200);
    return json({ ok: true, type, counts: { play, download } }, 200);
  }

  async routeHit(request) {
    const body = await readJsonBody(request);
    if (body && body.__tooLarge) return json({ ok: false, error: "Body too large" }, 413);
    if (!body) return badRequest("JSON body bekleniyor");

    const type = normalizeType(body.type || "");
    if (type !== "play" && type !== "download") return badRequest("type play/download olmalı");

    const id = sanitizeId(typeof body.id === "string" ? body.id : "");
    if (!id) return badRequest("id gerekli (veya çok uzun)");

    const title = clampStr(typeof body.title === "string" ? body.title : "", MAX_TITLE_LEN);
    const fileName = clampStr(typeof body.file_name === "string" ? body.file_name : "", MAX_FILE_LEN);

    const now = Date.now();
    const key = counterKey(id);
    const tKey = topKey(type);

    let outRow = null;

    await this.state.storage.transaction(async (txn) => {
      const cur = (await txn.get(key)) || null;

      const next = {
        pl: (Number(cur?.pl) || 0) + (type === "play" ? 1 : 0),
        dl: (Number(cur?.dl) || 0) + (type === "download" ? 1 : 0),
        title: title || (cur?.title || ""),
        file_name: fileName || (cur?.file_name || ""),
        updatedAt: now,
      };

      await txn.put(key, next);

      const topArr = (await txn.get(tKey)) || [];
      const clean = Array.isArray(topArr) ? topArr.filter((x) => x && x.id && x.id !== id) : [];

      const count = type === "play" ? next.pl : next.dl;
      clean.push({
        id,
        title: next.title || "",
        file_name: next.file_name || "",
        count,
        updatedAt: next.updatedAt,
      });

      sortTopRows(clean);
      if (clean.length > TOP_STORE_MAX) clean.length = TOP_STORE_MAX;

      await txn.put(tKey, clean);

      outRow = {
        ok: true,
        id,
        title: next.title || "",
        file_name: next.file_name || "",
        type,
        count,
        updatedAt: next.updatedAt,
      };
    });

    return json(outRow || { ok: true, id, type, count: 0, updatedAt: now }, 200);
  }

  async routeTop(url) {
    const type = normalizeTopType(url.searchParams.get("type")) || "download";
    const limit = Math.min(TOP_LIMIT_MAX, Math.max(1, Number(url.searchParams.get("limit") || "10")));

    const cursorRaw = (url.searchParams.get("cursor") || "").trim();
    const offset = Number.isFinite(Number(cursorRaw)) ? Math.max(0, Number(cursorRaw)) : 0;

    const arr = (await this.state.storage.get(topKey(type))) || [];
    const rows = Array.isArray(arr) ? arr : [];

    const page = rows.slice(offset, offset + limit).map((r) => ({
      id: String(r.id || ""),
      title: r.title || "",
      file_name: r.file_name || "",
      type,
      count: Number(r.count) || 0,
      updatedAt: Number(r.updatedAt) || 0,
    }));

    const nextCursor = offset + limit < rows.length ? String(offset + limit) : null;
    return json({ ok: true, type, limit, cursor: nextCursor, rows: page }, 200);
  }

  async routeReset(request) {
    if (!requireAdmin(request, this.env)) {
      const hasSecret = !!(this.env?.ADMIN_TOKEN || "").toString();
      return json({ ok: false, error: hasSecret ? "Unauthorized" : "ADMIN_TOKEN secret missing" }, 401);
    }

    const body = await readJsonBody(request);
    if (body && body.__tooLarge) return json({ ok: false, error: "Body too large" }, 413);
    if (!body) return badRequest("JSON body bekleniyor");

    const mode = (body.mode || "").toString();

    if (mode === "id") {
      const id = sanitizeId(typeof body.id === "string" ? body.id : "");
      if (!id) return badRequest("id gerekli");

      const key = counterKey(id);

      await this.state.storage.transaction(async (txn) => {
        await txn.delete(key);

        for (const t of ["play", "download"]) {
          const k = topKey(t);
          const arr = (await txn.get(k)) || [];
          const next = Array.isArray(arr) ? arr.filter((x) => x && x.id && x.id !== id) : [];
          await txn.put(k, next);
        }
      });

      return json({ ok: true, mode: "id", deleted: 1, id }, 200);
    }

    if (mode === "all") {
      let deleted = 0;

      const pages = await listKeysByPrefix(this.state.storage, "c:");
      for (const keys of pages) {
        if (!keys.length) continue;
        await this.state.storage.delete(keys);
        deleted += keys.length;
      }

      await this.state.storage.delete([topKey("play"), topKey("download")]);

      return json({ ok: true, mode: "all", deleted }, 200);
    }

    return badRequest('mode "all" veya "id" olmalı');
  }
}

export default {
  async fetch(request, env) {
    try {
      const url = new URL(request.url);

      if (request.method === "OPTIONS") {
        return new Response(null, { headers: corsHeaders() });
      }

      if (url.pathname === "/" || url.pathname === "/health") {
        if (request.method !== "GET") return text("Method not allowed", 405);
        return text("OK", 200);
      }

      const stub = getGlobalStub(env);
      if (!stub) {
        return json({ ok: false, error: "COUNTERS_DO binding missing (DO binding kontrol et)" }, 500);
      }

      if (url.pathname === "/counts" || url.pathname === "/hit" || url.pathname === "/top" || url.pathname === "/reset") {
        return stub.fetch(request);
      }

      return text("Not found", 404);
    } catch (e) {
      return json({ ok: false, error: "Worker exception", detail: errToString(e) }, 500);
    }
  },
};
