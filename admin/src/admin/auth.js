// 管理画面 API のログイン確認（Cloudflare Access）。
//
// Cloudflare Access が管理画面の Worker（stock-simulator-admin）全体を保護していれば、ログイン済みのリクエストには
// Cf-Access-Jwt-Assertion ヘッダー（署名付きトークン）が付く。Access の設定漏れに備えて、
// ここでも署名・発行元・宛先（AUD）・有効期限を確かめ、確認できなければ断る。
//
// 必要なシークレット（wrangler secret put）:
//   ACCESS_TEAM_DOMAIN  例: yourteam.cloudflareaccess.com
//   ACCESS_AUD          Access アプリケーションの「アプリケーション Audience (AUD) タグ」
// ローカル確認用（.dev.vars のみ）:
//   ALLOW_LOCAL_NO_AUTH=1  localhost / 127.0.0.1 からのアクセスに限りログインを省く

const LOCAL_HOSTS = ["localhost", "127.0.0.1"];
let jwksCache = { team: null, keys: [], fetchedAt: 0 };

// ログインしているユーザー（メールアドレス）を返す。確認できなければ例外を投げる
export async function authenticate(request, env) {
  const host = new URL(request.url).hostname;
  if (env.ALLOW_LOCAL_NO_AUTH === "1" && LOCAL_HOSTS.includes(host)) return "local";
  return verifyAccessJwt(request.headers.get("Cf-Access-Jwt-Assertion"), env);
}

async function verifyAccessJwt(token, env) {
  if (!env.ACCESS_TEAM_DOMAIN || !env.ACCESS_AUD) throw new Error("Access の設定（ACCESS_TEAM_DOMAIN / ACCESS_AUD）がありません");
  if (!token) throw new Error("ログインしていません");
  const [h, p, s] = token.split(".");
  const header = JSON.parse(b64urlText(h));
  const payload = JSON.parse(b64urlText(p));

  const key = await accessKey(env.ACCESS_TEAM_DOMAIN, header.kid);
  const ok = await crypto.subtle.verify("RSASSA-PKCS1-v1_5", key, b64urlBytes(s), new TextEncoder().encode(`${h}.${p}`));
  if (!ok) throw new Error("署名が正しくありません");

  const aud = Array.isArray(payload.aud) ? payload.aud : [payload.aud];
  if (!aud.includes(env.ACCESS_AUD)) throw new Error("AUD が一致しません");
  if (payload.iss !== `https://${env.ACCESS_TEAM_DOMAIN}`) throw new Error("発行元が一致しません");
  if (!payload.exp || payload.exp * 1000 < Date.now()) throw new Error("ログインの有効期限が切れています");
  return payload.email || payload.sub;
}

// Access の公開鍵（1 時間キャッシュ。鍵の入れ替えに備えて kid が見つからなければ取り直す）
async function accessKey(team, kid) {
  const stale = jwksCache.team !== team || Date.now() - jwksCache.fetchedAt > 3600_000;
  if (stale || !jwksCache.keys.some(k => k.kid === kid)) {
    const res = await fetch(`https://${team}/cdn-cgi/access/certs`);
    if (!res.ok) throw new Error(`Access の公開鍵を取得できません（${res.status}）`);
    jwksCache = { team, keys: (await res.json()).keys || [], fetchedAt: Date.now() };
  }
  const jwk = jwksCache.keys.find(k => k.kid === kid);
  if (!jwk) throw new Error("署名の鍵が見つかりません");
  return crypto.subtle.importKey("jwk", jwk, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"]);
}

function b64urlBytes(s) {
  const bin = atob(s.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(s.length / 4) * 4, "="));
  return Uint8Array.from(bin, c => c.charCodeAt(0));
}

function b64urlText(s) {
  return new TextDecoder().decode(b64urlBytes(s));
}
