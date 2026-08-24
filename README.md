# AInstagram

AI 관련 소식/지식을 카드뉴스 형태로 정리해 인스타그램에 자동 업로드하는 파이프라인.
서버 비용 없이 GitHub Actions cron + 무료 티어 서비스 조합으로 돌아가도록 설계했다.

## 전체 흐름

```
[매일 1회] 카테고리 선택(뉴스/지식 라운드로빈) -> 예비 게시물 3개 생성
              -> Telegram으로 썸네일 미리보기 전송 (채택 / 최우선 채택 / 폐기 버튼)
                                    |
        [10분마다 폴링, 또는 Cloudflare Worker 웹훅으로 즉시] 버튼 응답 처리
                                    |
                채택 시: 5~7장 풀세트 이미지 생성 -> Cloudflare R2 업로드 -> 대기열 추가
                                    |
        [07 / 12 / 18 / 23시 미국 동부(Eastern)] 대기열 다음 항목을 인스타그램에 캐러셀로 발행
                                    |
                     게시 이력 저장 (다음 주제 생성 시 중복 방지/난이도 참고)
```

GitHub Actions는 실행마다 새 환경이라, 각 워크플로우는 실행 후 `data/ainstagram.db`
변경사항을 저장소에 커밋해서 상태를 영속시킨다.

## 로컬 개발 환경

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env   # 아래 자격증명 채우기
pytest tests/ -q
```

## config.yaml 커스터마이징

인스타 계정명, 세부 주제, 게시 시간, 브랜드 색상 등은 전부 [config/config.yaml](config/config.yaml)에서 관리한다.
아직 계정명/세부 주제가 안 정해졌다면 `TBD`로 둔 채로 나머지 기능부터 써보고, 나중에 이 파일만 고치면 된다.

- `instagram.account_name`, `instagram.business_account_id`: 계정 확정되면 채우기
- `content.topics`: 비어있으면 LLM이 카테고리 안에서 자유롭게 주제를 고름
- `content.news_feeds`: 참고할 RSS 목록, 자유롭게 추가/삭제 가능
- `content.language`: 게시물 본문 언어 (현재 영어 - 더 넓은 시장을 노려서)
- `content.fixed_hashtags`: 매 게시물에 고정으로 붙는 해시태그. 주제별 동적 해시태그는 LLM이 생성해서 앞에 붙고, 그 뒤에 이 목록이 붙는다.
- `posting.times`: 게시 시각 (바꾸면 `.github/workflows/publish_next.yml`의 cron도 같이 수정해야 함)
- `image.brand`: 피드 통일감을 위한 포인트 컬러/오버레이/캔버스 크기

## 필요한 자격증명 발급

### 1. OpenAI API 키
https://platform.openai.com/api-keys 에서 발급. `.env`의 `OPENAI_API_KEY`.

### 2. Instagram API (Instagram Login 방식, 본인 계정 전용 - 앱 리뷰/페이지 연결 불필요)
2024년 7월부터 생긴 방식으로, Facebook 페이지 연결 없이 Instagram 계정으로 바로 로그인해서 토큰을 받을 수 있다.
1. 인스타그램 계정을 Business 또는 Creator 계정으로 전환 (페이지 연결은 필요 없음)
2. https://developers.facebook.com 에서 앱 생성 (유형: Business)
3. 앱 대시보드에서 "Instagram" 제품 추가 -> 왼쪽 메뉴에서 Instagram 펼쳐서 **"API setup with Instagram Login"** 선택
4. 해당 페이지의 **"Generate access tokens"** 섹션에서 **"Add an Instagram Account"** 클릭 -> 본인 계정으로 로그인/권한 승인
5. 승인하면 대시보드에 액세스 토큰이 바로 표시됨 (한 번만 보여주므로 즉시 복사)
6. 같은 페이지 혹은 `https://graph.instagram.com/me?fields=id,username&access_token=<토큰>` 호출로 Instagram 계정 ID 확인
7. `.env`의 `IG_ACCESS_TOKEN`, `IG_BUSINESS_ACCOUNT_ID`에 채우기

> 본인 소유 계정에만 게시하는 용도라 Meta 앱 리뷰(수 주 소요)는 필요 없다. 다른 사람 계정까지 다루려는 경우에만 리뷰가 필요하다.
> 장기 액세스 토큰은 60일마다 만료되므로, 만료 전에 갱신이 필요하다 (갱신 자동화는 별도 이슈로 추후 진행).

### 3. Telegram 봇
1. Telegram에서 `@BotFather`에게 `/newbot`으로 봇 생성 -> 토큰 발급
2. 만든 봇과 대화를 한 번 시작한 뒤, `https://api.telegram.org/bot<토큰>/getUpdates`로 접속해 `chat.id` 확인
3. `.env`의 `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`에 채우기

### 4. Cloudflare R2 (이미지 호스팅)
Instagram Graph API는 이미지를 공개 URL로 가져오기 때문에 어딘가에는 호스팅이 필요하다. R2는 무료 티어(10GB 저장, 이그레스 비용 없음)로 이 용도에 충분하다.
1. Cloudflare 대시보드 > R2에서 버킷 생성, 퍼블릭 액세스(R2.dev 서브도메인 또는 커스텀 도메인) 활성화
2. R2 API 토큰 발급 (계정 ID, Access Key ID, Secret Access Key)
3. `.env`의 `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME`, `R2_PUBLIC_BASE_URL`에 채우기

### 5. Telegram 웹훅용 Cloudflare Worker (선택 - 즉시 응답)
기본값(스케줄 폴링)은 버튼을 눌러도 최대 10분까지 반응이 늦을 수 있다. 이 Worker를 배포하면 클릭 즉시(수 초~수십 초) 처리된다. 안 하셔도 스케줄 폴링만으로 계속 동작한다.

**1) GitHub PAT 발급** (Worker가 검수 폴링 워크플로우를 즉시 실행시키는 데 필요)
1. https://github.com/settings/tokens?type=beta 에서 Fine-grained token 생성
2. Repository access를 이 저장소(AInstagram) 하나로 제한
3. Permissions > Actions: **Read and write**
4. 생성된 토큰 복사 (한 번만 보여줌)

**2) Worker 배포**
```bash
cd worker
npx wrangler login          # 브라우저로 Cloudflare 계정 인증
npx wrangler secret put GITHUB_TOKEN               # 위에서 발급한 PAT 붙여넣기
npx wrangler secret put TELEGRAM_WEBHOOK_SECRET     # 아무 랜덤 문자열이나 직접 정해서 입력 (예: openssl rand -hex 20)
npx wrangler secret put TELEGRAM_BOT_TOKEN          # .env의 TELEGRAM_BOT_TOKEN과 동일한 값 (버튼 클릭 즉시 응답용)
npx wrangler deploy
```
배포가 끝나면 `https://ainstagram-webhook.<계정서브도메인>.workers.dev` 같은 URL이 나온다.

**3) Telegram에 웹훅 등록** (아래 값 채워서 실행)
```bash
curl "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook" \
  -d "url=<위에서 나온 Worker URL>" \
  -d "secret_token=<2번에서 정한 TELEGRAM_WEBHOOK_SECRET>"
```
`{"ok":true,...}` 응답이 오면 완료. 이제 버튼을 누르면 몇 초 안에 처리된다.

**되돌리기(웹훅 끄고 다시 폴링만 쓰기)**
```bash
curl "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/deleteWebhook"
```
코드를 건드릴 필요 없이 이 호출 한 번이면 기존 10분 스케줄 폴링이 다시 정상 동작한다.

## GitHub Secrets 등록

로컬 `.env`에 채운 값을 그대로 저장소 Secrets에도 등록해야 Actions 워크플로우가 동작한다.

```bash
gh secret set OPENAI_API_KEY
gh secret set IG_BUSINESS_ACCOUNT_ID
gh secret set IG_ACCESS_TOKEN
gh secret set TELEGRAM_BOT_TOKEN
gh secret set TELEGRAM_CHAT_ID
gh secret set R2_ACCOUNT_ID
gh secret set R2_ACCESS_KEY_ID
gh secret set R2_SECRET_ACCESS_KEY
gh secret set R2_BUCKET_NAME
gh secret set R2_PUBLIC_BASE_URL
```

등록 후에는 `.github/workflows`의 세 워크플로우(초안 생성 / 검수 폴링 / 게시)가 각자 정해진 주기로 자동 실행된다. Actions 탭에서 `workflow_dispatch`로 수동 실행해서 먼저 확인해보는 걸 추천한다.

## 프로젝트 구조

```
src/ainstagram/
  config.py           # config.yaml + .env 로더
  db.py, repository.py, models.py   # drafts/queue/post_history 저장소
  content/            # 주제 생성 (RSS 수집, LLM, 중복 체크)
  images/             # 카드뉴스 이미지 생성 (템플릿 + AI 배경 + R2 업로드)
  review/             # Telegram 검수 봇
  publish/            # Instagram Graph API 발행
scripts/              # GitHub Actions에서 호출하는 진입점
.github/workflows/    # cron 스케줄
worker/               # Telegram 웹훅 수신용 Cloudflare Worker (선택, 즉시 응답용)
```
