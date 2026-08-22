# AInstagram

AI 관련 소식/지식을 카드뉴스 형태로 정리해 인스타그램에 자동 업로드하는 파이프라인.
서버 비용 없이 GitHub Actions cron + 무료 티어 서비스 조합으로 돌아가도록 설계했다.

## 전체 흐름

```
[매일 1회] 카테고리 선택(뉴스/지식 라운드로빈) -> 예비 게시물 3개 생성
              -> Telegram으로 썸네일 미리보기 전송 (채택 / 최우선 채택 / 폐기 버튼)
                                    |
                     [10분마다] 버튼 응답 폴링
                                    |
                채택 시: 5~7장 풀세트 이미지 생성 -> Cloudflare R2 업로드 -> 대기열 추가
                                    |
        [07 / 12 / 18 / 23시 KST] 대기열 다음 항목을 인스타그램에 캐러셀로 발행
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
- `posting.times`: 게시 시각 (바꾸면 `.github/workflows/publish_next.yml`의 cron도 같이 수정해야 함)
- `image.brand`: 피드 통일감을 위한 포인트 컬러/오버레이/캔버스 크기

## 필요한 자격증명 발급

### 1. OpenAI API 키
https://platform.openai.com/api-keys 에서 발급. `.env`의 `OPENAI_API_KEY`.

### 2. Instagram Graph API (본인 계정 전용, 앱 리뷰 불필요)
1. https://developers.facebook.com 에서 앱 생성 (유형: Business)
2. 앱에 "Instagram" 제품 추가
3. 인스타그램 계정을 Business/Creator 계정으로 전환하고, Facebook 페이지와 연결
4. 앱이 Development mode인 상태에서, 앱 대시보드 > 역할(Roles) > Instagram Testers에 본인 계정 추가 후, 인스타그램 앱에서 초대 수락
5. Graph API Explorer 등으로 장기 액세스 토큰 발급, 연결된 Instagram 비즈니스 계정 ID 확인
6. `.env`의 `IG_ACCESS_TOKEN`, `IG_BUSINESS_ACCOUNT_ID`에 채우기

> 본인 소유 계정에만 게시하는 용도라 Meta 앱 리뷰(수 주 소요)는 필요 없다. 다른 사람 계정까지 다루려는 경우에만 리뷰가 필요하다.

### 3. Telegram 봇
1. Telegram에서 `@BotFather`에게 `/newbot`으로 봇 생성 -> 토큰 발급
2. 만든 봇과 대화를 한 번 시작한 뒤, `https://api.telegram.org/bot<토큰>/getUpdates`로 접속해 `chat.id` 확인
3. `.env`의 `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`에 채우기

### 4. Cloudflare R2 (이미지 호스팅)
Instagram Graph API는 이미지를 공개 URL로 가져오기 때문에 어딘가에는 호스팅이 필요하다. R2는 무료 티어(10GB 저장, 이그레스 비용 없음)로 이 용도에 충분하다.
1. Cloudflare 대시보드 > R2에서 버킷 생성, 퍼블릭 액세스(R2.dev 서브도메인 또는 커스텀 도메인) 활성화
2. R2 API 토큰 발급 (계정 ID, Access Key ID, Secret Access Key)
3. `.env`의 `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME`, `R2_PUBLIC_BASE_URL`에 채우기

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
```
