// worker.js — Cloudflare Workers + Durable Objects (SQLite-backed)
// Endpoints:
//   GET  /health
//   POST /counts   { type:"play"|"download"|"both", ids:[...] }
//   POST /hit      { type:"play"|"download", id, title?, file_name? }
//   GET  /top?type=play|download&limit=10&cursor=0
//   POST /reset    { mode:"all" }  or  { mode:"id", id:"..." }   (requires x-admin-token == env.ADMIN_TOKEN)

const MAX_ID_LEN = 300;
const MAX_TITLE_LEN = 300;
const MAX_FILE_LEN = 260;

const MAX_IDS_POST = 600;      // client POST /counts ids limit
const TOP_LIMIT_MAX = 50;      // /top limit upper bound
const SQL_IN_CHUNK = 400;      // chunk size for IN (...) queries (safe)

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
  if (!v) return "";
  if (v.length > MAX_ID_LEN) return "";
  return v;
}

function sanitizeIds(ids, maxCount) {
  const out = [];
  const seen = new Set();
  if (!Array.isArray(ids)) return out;

  for (const raw of ids) {
    if (out.length >= maxCount) break;
    const id = sanitizeId(raw);
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

    // SQLite-backed Durable Object storage:
    this.sql = state.storage.sql;

    this._inited = false;
  }

  init() {
    if (this._inited) return;
    this._inited = true;

    // sql.exec supports multiple statements separated by semicolons.
    this.sql.exec(`
      CREATE TABLE IF NOT EXISTS counters (
        id        TEXT PRIMARY KEY,
        pl        INTEGER NOT NULL DEFAULT 0,
        dl        INTEGER NOT NULL DEFAULT 0,
        title     TEXT NOT NULL DEFAULT '',
        file_name TEXT NOT NULL DEFAULT '',
        updatedAt INTEGER NOT NULL DEFAULT 0
      );

      CREATE INDEX IF NOT EXISTS idx_counters_pl      ON counters(pl DESC);
      CREATE INDEX IF NOT EXISTS idx_counters_dl      ON counters(dl DESC);
      CREATE INDEX IF NOT EXISTS idx_counters_updated ON counters(updatedAt DESC);
    `);
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

    this.init();

    try {
      if (url.pathname === "/counts") {
        if (request.method !== "POST") return text("Method not allowed", 405);
        return await this.routeCounts(request);
      }

      if (url.pathname === "/hit") {
        if (request.method !== "POST") return text("Method not allowed", 405);
        return await this.routeHit(request);
      }

      if (url.pathname === "/top") {
        if (request.method !== "GET") return text("Method not allowed", 405);
        return await this.routeTop(url);
      }

      if (url.pathname === "/reset") {
        if (request.method !== "POST") return text("Method not allowed", 405);
        return await this.routeReset(request);
      }

      return text("Not found", 404);
    } catch (err) {
      // Never throw; always respond JSON with CORS.
      return json({ ok: false, error: "Internal error", detail: String(err?.message || err) }, 500);
    }
  }

  async routeCounts(request) {
    const body = await readJsonBody(request);
    if (!body) return badRequest("JSON body bekleniyor");

    const type = normalizeType(body.type);
    if (!type) return badRequest('type "play", "download" veya "both" olmalı');

    const ids = sanitizeIds(body.ids, MAX_IDS_POST);
    if (!ids.length) {
      return json({ ok: true, type, counts: type === "both" ? { play: {}, download: {} } : {} }, 200);
    }

    const play = {};
    const download = {};

    for (let i = 0; i < ids.length; i += SQL_IN_CHUNK) {
      const chunk = ids.slice(i, i + SQL_IN_CHUNK);
      const placeholders = chunk.map(() => "?").join(",");

      // Returns array of row objects: [{id, pl, dl}, ...]
      const rows = this.sql
        .exec(`SELECT id, pl, dl FROM counters WHERE id IN (${placeholders});`, ...chunk)
        .toArray();

      const found = new Map();
      for (const r of rows) {
        found.set(String(r.id), { pl: Number(r.pl) || 0, dl: Number(r.dl) || 0 });
      }

      for (const id of chunk) {
        const v = found.get(id) || { pl: 0, dl: 0 };
        if (type === "play") play[id] = v.pl;
        else if (type === "download") download[id] = v.dl;
        else {
          play[id] = v.pl;
          download[id] = v.dl;
        }
      }
    }

    if (type === "play") return json({ ok: true, type, counts: play }, 200);
    if (type === "download") return json({ ok: true, type, counts: download }, 200);
    return json({ ok: true, type, counts: { play, download } }, 200);
  }

  async routeHit(request) {
    const body = await readJsonBody(request);
    if (!body) return badRequest("JSON body bekleniyor");

    const type = normalizeType(body.type);
    if (type !== "play" && type !== "download") return badRequest('type "play" veya "download" olmalı');

    const id = sanitizeId(body.id);
    if (!id) return badRequest("id gerekli");

    const title = clampStr(body.title, MAX_TITLE_LEN);
    const fileName = clampStr(body.file_name, MAX_FILE_LEN);

    const dPl = type === "play" ? 1 : 0;
    const dDl = type === "download" ? 1 : 0;
    const now = Date.now();

    // Upsert
    this.sql.exec(
      `
      INSERT INTO counters (id, pl, dl, title, file_name, updatedAt)
      VALUES (?, ?, ?, ?, ?, ?)
      ON CONFLICT(id) DO UPDATE SET
        pl = pl + excluded.pl,
        dl = dl + excluded.dl,
        title = CASE WHEN excluded.title != '' THEN excluded.title ELSE title END,
        file_name = CASE WHEN excluded.file_name != '' THEN excluded.file_name ELSE file_name END,
        updatedAt = excluded.updatedAt;
      `,
      id,
      dPl,
      dDl,
      title,
      fileName,
      now
    );

    let row = null;
    try {
      row = this.sql
        .exec(`SELECT pl, dl, title, file_name, updatedAt FROM counters WHERE id = ?;`, id)
        .one();
    } catch {
      row = null;
    }

    const pl = Number(row?.pl) || 0;
    const dl = Number(row?.dl) || 0;

    return json(
      {
        ok: true,
        id,
        title: row?.title || "",
        file_name: row?.file_name || "",
        type,
        count: type === "play" ? pl : dl,
        updatedAt: Number(row?.updatedAt) || now,
      },
      200
    );
  }

  async routeTop(url) {
    const type = normalizeType(url.searchParams.get("type") || "download") || "download";
    const limit = Math.min(TOP_LIMIT_MAX, Math.max(1, Number(url.searchParams.get("limit") || "10")));

    const cursorRaw = (url.searchParams.get("cursor") || "").trim();
    const offset = Number.isFinite(Number(cursorRaw)) ? Math.max(0, Number(cursorRaw)) : 0;

    const orderCol = type === "play" ? "pl" : "dl";

    const rows = this.sql
      .exec(
        `
        SELECT id, pl, dl, title, file_name, updatedAt
        FROM counters
        ORDER BY ${orderCol} DESC, updatedAt DESC, id ASC
        LIMIT ? OFFSET ?;
        `,
        limit + 1,
        offset
      )
      .toArray();

    const page = rows.slice(0, limit).map((r) => ({
      id: String(r.id),
      title: r.title || "",
      file_name: r.file_name || "",
      type: type === "play" ? "play" : "download",
      count: type === "play" ? Number(r.pl) || 0 : Number(r.dl) || 0,
      updatedAt: Number(r.updatedAt) || 0,
    }));

    const nextCursor = rows.length > limit ? String(offset + limit) : null;

    return json({ ok: true, type: type === "play" ? "play" : "download", limit, cursor: nextCursor, rows: page }, 200);
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
      const id = sanitizeId(body.id);
      if (!id) return badRequest("id gerekli");

      this.sql.exec(`DELETE FROM counters WHERE id = ?;`, id);
      return json({ ok: true, mode: "id", deleted: 1, id }, 200);
    }

    if (mode === "all") {
      const before = this.sql.exec(`SELECT COUNT(*) AS n FROM counters;`).toArray();
      const n = Number(before?.[0]?.n) || 0;

      this.sql.exec(`DELETE FROM counters;`);
      return json({ ok: true, mode: "all", deleted: n }, 200);
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
    if (!stub) return json({ ok: false, error: "COUNTERS_DO binding missing (wrangler.toml kontrol et)" }, 500);

    if (url.pathname === "/counts" || url.pathname === "/hit" || url.pathname === "/top" || url.pathname === "/reset") {
      return stub.fetch(request);
    }

    return text("Not found", 404);
  },
};
