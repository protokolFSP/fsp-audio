// worker.js (NO SQLITE) — Durable Object Storage ile sayaç
// Endpoints:
//  GET  /health
//  POST /hit        { type:"play"|"download", id, title?, file_name? }
//  POST /counts      { type:"play"|"download"|"both", ids:[...] }
//  GET  /top?type=play|download&limit=10&cursor=0
//  POST /reset       (x-admin-token gerekli) { mode:"all" } veya { mode:"id", id:"..." }

const MAX_ID_LEN = 300;
const MAX_TITLE_LEN = 300;
const MAX_FILE_LEN = 260;

const MAX_IDS_POST = 600;
const TOP_LIMIT_MAX = 50;

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
  const t = (v || "").toString().toLowerCase();
  return t === "play" || t === "download" || t === "both" ? t : null;
}

function clampStr(s, maxLen) {
  if (typeof s !== "string") return "";
  return s.length <= maxLen ? s : s.slice(0, maxLen);
}

function sanitizeId(id) {
  const v = (id || "").toString().trim();
  if (!v || v.length > MAX_ID_LEN) return "";
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

async function readJsonBody(request) {
  try {
    const txt = await request.text();
    if (!txt) return null;
    return JSON.parse(txt);
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

export class CountersDO {
  constructor(state, env) {
    this.state = state;
    this.env = env;
    // NOTE: SQLite yok — sadece state.storage kullanıyoruz
  }

  async fetch(request) {
    const url = new URL(request.url);

    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders() });
    }

    if (url.pathname === "/" || url.pathname === "/health") {
      if (request.method !== "GET") return text("Method not allowed", 405);
      return text("OK", 200);
    }

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
  }

  async routeCounts(request) {
    const body = await readJsonBody(request);
    if (!body) return badRequest("JSON body bekleniyor");

    const type = normalizeType(body.type || "");
    if (!type) return badRequest('type "play" / "download" / "both" olmalı');

    const ids = sanitizeIds(Array.isArray(body.ids) ? body.ids : [], MAX_IDS_POST);

    if (!ids.length) {
      return json(
        {
          ok: true,
          type,
          counts: type === "both" ? { play: {}, download: {} } : {},
        },
        200
      );
    }

    // storage.get([keys]) -> Map
    let map;
    try {
      map = await this.state.storage.get(ids);
    } catch {
      // çok nadir: bazı runtime’larda array get farklı davranır
      map = new Map();
      for (const id of ids) {
        const v = await this.state.storage.get(id);
        if (v != null) map.set(id, v);
      }
    }

    const play = {};
    const download = {};

    for (const id of ids) {
      const rec = map instanceof Map ? map.get(id) : map?.[id];
      const pl = Number(rec?.pl) || 0;
      const dl = Number(rec?.dl) || 0;

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
    if (!body) return badRequest("JSON body bekleniyor");

    const type = normalizeType(body.type || "");
    if (type !== "play" && type !== "download") return badRequest('type "play" / "download" olmalı');

    const id = sanitizeId(typeof body.id === "string" ? body.id : "");
    if (!id) return badRequest("id gerekli");

    const title = clampStr(typeof body.title === "string" ? body.title : "", MAX_TITLE_LEN);
    const fileName = clampStr(typeof body.file_name === "string" ? body.file_name : "", MAX_FILE_LEN);

    const now = Date.now();

    const prev = (await this.state.storage.get(id)) || {};
    const pl0 = Number(prev.pl) || 0;
    const dl0 = Number(prev.dl) || 0;

    const pl = pl0 + (type === "play" ? 1 : 0);
    const dl = dl0 + (type === "download" ? 1 : 0);

    const next = {
      pl,
      dl,
      title: title || (prev.title || ""),
      file_name: fileName || (prev.file_name || ""),
      updatedAt: now,
    };

    await this.state.storage.put(id, next);

    return json(
      {
        ok: true,
        id,
        title: next.title || "",
        file_name: next.file_name || "",
        type,
        count: type === "play" ? pl : dl,
        updatedAt: now,
      },
      200
    );
  }

  async routeTop(url) {
    const type = normalizeType(url.searchParams.get("type") || "download") || "download";
    const limit = Math.min(TOP_LIMIT_MAX, Math.max(1, Number(url.searchParams.get("limit") || "10")));

    const cursorRaw = (url.searchParams.get("cursor") || "").trim();
    const offset = Number.isFinite(Number(cursorRaw)) ? Math.max(0, Number(cursorRaw)) : 0;

    // 255 kayıt gibi küçük listeler için bu yeterli (hepsini çekip sort)
    const all = await this.state.storage.list();
    const rows = [];

    for (const [id, rec] of all.entries()) {
      const pl = Number(rec?.pl) || 0;
      const dl = Number(rec?.dl) || 0;
      const updatedAt = Number(rec?.updatedAt) || 0;
      rows.push({
        id: String(id),
        pl,
        dl,
        title: rec?.title || "",
        file_name: rec?.file_name || "",
        updatedAt,
      });
    }

    rows.sort((a, b) => {
      const ca = type === "play" ? a.pl : a.dl;
      const cb = type === "play" ? b.pl : b.dl;
      if (cb !== ca) return cb - ca;
      if (b.updatedAt !== a.updatedAt) return b.updatedAt - a.updatedAt;
      return a.id.localeCompare(b.id);
    });

    const page = rows.slice(offset, offset + limit).map((r) => ({
      id: r.id,
      title: r.title,
      file_name: r.file_name,
      type: type === "play" ? "play" : "download",
      count: type === "play" ? r.pl : r.dl,
      updatedAt: r.updatedAt,
    }));

    const nextCursor = offset + limit < rows.length ? String(offset + limit) : null;

    return json(
      {
        ok: true,
        type: type === "play" ? "play" : "download",
        limit,
        cursor: nextCursor,
        rows: page,
      },
      200
    );
  }

  async routeReset(request) {
    if (!requireAdmin(request, this.env)) {
      const hasSecret = !!(this.env?.ADMIN_TOKEN || "").toString();
      return json({ ok: false, error: hasSecret ? "Unauthorized" : "ADMIN_TOKEN secret missing" }, 401);
    }

    const body = await readJsonBody(request);
    if (!body) return badRequest("JSON body bekleniyor");

    const mode = (body.mode || "").toString();

    if (mode === "id") {
      const id = sanitizeId(typeof body.id === "string" ? body.id : "");
      if (!id) return badRequest("id gerekli");
      await this.state.storage.delete(id);
      return json({ ok: true, mode: "id", id }, 200);
    }

    if (mode === "all") {
      await this.state.storage.deleteAll();
      return json({ ok: true, mode: "all" }, 200);
    }

    return badRequest('mode "all" veya "id" olmalı');
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders() });
    }

    if (url.pathname === "/" || url.pathname === "/health") {
      if (request.method !== "GET") return text("Method not allowed", 405);
      return text("OK", 200);
    }

    const stub = getGlobalStub(env);
    if (!stub) return json({ ok: false, error: "COUNTERS_DO binding missing (wrangler.toml / dashboard binding kontrol et)" }, 500);

    if (url.pathname === "/counts" || url.pathname === "/hit" || url.pathname === "/top" || url.pathname === "/reset") {
      return stub.fetch(request);
    }

    return text("Not found", 404);
  },
};
