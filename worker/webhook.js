/**
 * Telegram 웹훅을 받아서 GitHub Actions의 검수 폴링(poll_reviews) 워크플로우를
 * 즉시 트리거하는 Cloudflare Worker.
 *
 * 이 Worker는 업데이트 내용을 직접 처리하지 않는다 - 처리 로직은 전부 Python
 * (scripts/poll_reviews.py)에 그대로 두고, 여기서는 "지금 막 들어온 업데이트"를
 * workflow_dispatch의 input으로 그대로 전달해서 즉시 실행시키는 역할만 한다.
 *
 * 필요한 환경변수/시크릿 (wrangler secret put 으로 등록):
 * - TELEGRAM_WEBHOOK_SECRET: Telegram setWebhook 등록 시 넣은 secret_token과 동일한 값
 * - GITHUB_TOKEN: 이 저장소의 Actions에 워크플로우 실행 권한이 있는 GitHub PAT
 *
 * 일반 변수 (wrangler.toml [vars]):
 * - GITHUB_OWNER, GITHUB_REPO, GITHUB_WORKFLOW_FILE, GITHUB_REF
 */

export default {
  async fetch(request, env, ctx) {
    if (request.method !== "POST") {
      return new Response("OK");
    }

    const secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token");
    if (!env.TELEGRAM_WEBHOOK_SECRET || secret !== env.TELEGRAM_WEBHOOK_SECRET) {
      return new Response("Forbidden", { status: 403 });
    }

    let updateText;
    try {
      const update = await request.json();
      updateText = JSON.stringify(update);
    } catch (err) {
      // 파싱이 안 되는 요청은 그냥 무시하고 200으로 응답 (Telegram이 재시도하지 않도록)
      return new Response("OK");
    }

    // Telegram은 빠른 응답을 기대하므로, GitHub 호출은 응답 이후에도 계속 진행되게 한다.
    ctx.waitUntil(triggerWorkflow(env, updateText));

    return new Response("OK");
  },
};

async function triggerWorkflow(env, updateText) {
  const url = `https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/actions/workflows/${env.GITHUB_WORKFLOW_FILE}/dispatches`;

  const response = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "ainstagram-webhook-worker",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      ref: env.GITHUB_REF || "main",
      inputs: { telegram_update: updateText },
    }),
  });

  if (!response.ok) {
    console.error("workflow_dispatch failed", response.status, await response.text());
  }
}
