---
name: imagen
description: Art-direct images for 성진 with Codex's built-in gpt-image — agree on intent, show different drafts side by side on a live contact sheet, inspect and revise, and deliver a final PNG; text-led pieces are laid out in HTML and rendered instead.
disable-model-invocation: true
allowed-tools: Bash(uv run "${CLAUDE_SKILL_DIR}/scripts/cli.py" *)
---

# imagen

일은 이미지를 만들어 주는 게 아니라 성진 마음에 드는 이미지에 도달하는 것이다. 판정은 성진의 눈이 한다. 성진은 결과를 보면 정확히 판정하지만 디자인 어휘가 없다 — 그래서 방향은 말보다 이미지로 묻고, Claude의 판정과 숫자는 그 눈이 빨리, 근거를 갖고 결정하게 돕는 데 쓴다. Claude의 미적 취향은 성진과 다르다: Claude는 예쁘고 은유적인 쪽에 끌리고, 성진은 메시지가 한눈에 읽히는 쪽을 고른다. 그래서 Claude의 추천은 가설로 내고, 성진의 반응이 오면 그쪽으로 고친다.

## 성진과 합의하기

취향은 브리프가 아니라 이미지를 본 뒤에 드러난다. 그래서 생성 전에는 이미지로 확인할 수 없는 사실만 묻는다: 용도와 이미지의 운명(그대로 완성인지, 포토샵에서 로고·본문·사진을 얹을 판인지, 다시 만들 설계도인지), 고정 문구, 비율, 비워 둘 자리, 반드시 들어갈 대상. 미적 방향은 이미지로 묻는다.

성진이 방향을 가져왔다면(레퍼런스, 구체적 아이디어) 그 방향도 사실이다. 이해한 바를 글로 한 번 확인하고 그 방향으로 뽑는다. 방향이 없으면 서로 다른 가설 몇 개를 한 줄씩 보이고 곧바로 뽑아 이미지로 고르게 한다. 가설이 다르다는 건 목표에 가까워지는 이유가 다르다는 뜻이다 — 같은 이유를 매체만 바꿔 되풀이하면 한 방향이다.

장수는 기본값일 뿐이다. 한 라운드는 동시 생성 상한 안에서 한 번에 끝나야 성진이 기다리지 않고 나란히 비교한다. 성진이 더 원하면 따른다.

구도의 뼈대(구역 배분, 요소가 들고 남)가 바뀌는 라운드만 생성 전에 배치도로 합의한다 — 띠마다 무엇이 오고 높이의 몇 %인지. 사람은 완성 그림보다 빈 배치도에서 "여긴 이래야 한다"를 쉽게 말한다. 같은 뼈대 안의 수정은 바로 뽑는다.

생성·렌더 결과는 시트로 성진 앞에 있다(첫 생성·렌더 때 열린 탭이 스스로 갱신된다). Claude의 판정을 먼저 내고 성진의 자유 서술을 받는다. 판정에는 성진이 다음 결정을 내리는 데 필요한 것만 쓴다 — 무엇이 됐고 무엇이 걸리는지, 어느 쪽을 권하는지. 장마다 채점한 목록은 결정할 거리를 묻어 버린다. 생성 직후에 "어느 걸로 할까요 / 다음은?" 같은 선택지를 띄우지 않는다 — 성진은 이미지를 보고 서술로 방향을 준다. 선택지는 답이 진짜 갈림길일 때만 쓴다.

성진의 말을 새 미적 제약으로 옮길 때(불만을 금지나 요구로 바꿀 때)는 그 해석을 한 줄로 확인받는다. 불만은 증상이고 처방은 추론이라, 확인 없이 적은 금지는 성진이 고른 목표 이미지조차 어기고 있을 수 있다. 이미 합의된 의도 안의 실행 수정(제목을 한 줄로, 배경을 밝게)은 확인 없이 고친다.

목표 이미지와 참조는 역할이 다르다. 목표(`--target`)는 결과를 대어 볼 기준이고, 참조(`add --ref`)는 생성 조건으로 모델에 들어간다. 같은 파일이 둘 다일 수 있다.

장부 메모는 이번 작업에만 맞는 사실(이 포스터의 예약 구역, 이번 문구)과 다음 작업에도 맞는 선호를 갈라 적는다. 뒤의 것만 `--reusable`로 적는다 — 다음 작업이 `init --like`로 물려받는 것은 그것뿐이다.

## 좋은 이미지의 원리

빛·색·구도는 장면이나 브리프 안에 출처가 있어야 한다. 시험은 하나다: 그 선택의 출처를 가리킬 수 있는가. 금지 목록이 아니다 — 노을 진 골목 축제의 전구 불빛은 장면 안에 출처가 있어 따뜻하고, 법안 포스터의 금빛·시안 발광은 가리킬 출처가 없어 AI 티가 난다. 밤·기술·축제·고급처럼 분위기를 부르는 주제에서 모델은 남색 밤, 금빛·시안 발광, 보케와 떠다니는 입자, 노을과 도시 야경으로 끌려간다. 프롬프트를 쓰는 Claude도 같은 쪽으로 끌려가 남색과 금빛을 스스로 적는다. 그래서 Claude 자신의 초안도 같은 시험에 건다.

평범함은 모델이 카테고리의 관습을 정확히 아는 데서 나온다. 서로 다른 방향이 필요하면 관습의 출처를 옮긴다: 다른 업종의 관습(안전 표지판의 언어로 짠 타이포 포스터), 다른 분야가 이미지를 기술해 온 언어, 글자 밖의 구조. 반대 조건이 있다: 관습이 곧 목적인 작업(피드에서 그 의원실 것으로 알아봐야 하는 기념일 시리즈)은 익숙한 배치를 지키고 위계와 절제로 품질을 낸다. 옮긴 관습이 늘 이기지도 않는다 — 브리프의 정서를 지우면 밋밋해진다.

이미지가 의도로 읽히는 건 몇 가지 결정이 화면 전체에 일관되게 적용될 때다(팔레트, 글자의 위계, 그림체, 가장자리를 쓰는 방식). 의도된 결함이나 질감을 덧붙이는 것으로 평범함을 풀려 하지 않는다. 일관성은 깨는 것이 의미를 만들 때만 깬다(한 요소만 다른 색으로 시선을 모을 때).

결정이 개선인지 대체인지는 이번 작업의 기준선 — 장식 없이 한 줄로 넘겼을 때 모델이 낼 것 — 으로 판단한다. 기준선이 이미 브리프를 충족하면 새로움을 밀어붙이는 건 대체다. 애매하면 소박한 대조안을 같은 라운드에 같이 뽑아 나란히 본다 — 대조안에도 같은 효과가 나오면 그건 추가한 지시가 만든 것이 아니니 지시의 공으로 치지 않는다.

결정을 고른 뒤에는 그 결정이 브리프가 지키라고 한 것을 밀어냈는지 되묻는다. 밀어냈으면 먼저 그 결정을 필수 요소와 부딪히지 않는 축으로 옮겨 본다(크롭 때문에 주인공이 작아졌으면 크롭을 배경에 건다) — 결정을 줄이기만 하면 결정 전의 평범함으로 돌아간다. 옮길 축이 없으면 줄이거나 버린다.

성진의 취향(개인 선호): 그래픽 디자이너가 일러스트로 정성스럽게 만든 느낌. 홍보물은 개념 장치보다 익숙한 배치를 잘 짠 쪽. 기념일 홍보물은 한눈에 주제로 읽히는 쪽 — 그 날의 글자나 누구나 아는 상징이 주인공이고, 한 번 풀어야 주제에 닿는 은유는 덜 직관적이다. 실사가 필요하면 생성한 가짜 실사가 아니라 실제 사진.

프롬프트는 영어 산문으로 쓰고, 이미지에 들어갈 한글 문구는 따옴표 안에 정확히 적는다. 아래 두 프롬프트는 성진이 고른 결과를 낸 밀도의 기준선이다. 고르는 메뉴가 아니다 — 눈금은 분량이 아니라 무엇을 고정하고 무엇을 열어 뒀는가의 대비다.

```
A vertical 3:4 portrait poster (taller than wide, aspect ratio 3:4) for a neighborhood autumn street festival in Mangwon-dong, Seoul. It should look like a professional Korean graphic designer illustrated it by hand with care: a warm, cozy, contemporary editorial illustration style with flat shapes, subtle risograph-like grain and paper texture, slightly imperfect hand-drawn linework, and a limited autumn palette of persimmon orange, mustard yellow, brick red, deep forest green and warm cream, with small touches of navy.

Illustration: a charming narrow Mangwon market alley seen in gentle perspective and rising toward the upper middle of the poster. It has low Korean shopfronts with striped awnings, strings of warm glowing bulbs and small paper lanterns hung across the alley, and a ginkgo tree with golden leaves drifting down. Food stalls sell tteokbokki, hotteok, roasted chestnuts and sweet potatoes, with soft steam rising. There are flea-market tables with vintage goods, and a small busker playing acoustic guitar on a tiny corner stage. Small, friendly, stylized neighbors stroll through, including families, a couple, and someone walking a dog. Late-afternoon golden light. The scene feels busy but uncluttered, with clear negative space kept for the typography.

Typography: the hierarchy should be clean, legible and well designed, and every piece of text must be rendered exactly as written. Do not add any other text, fake letters or gibberish signage. Keep shop signs blank or as simple shapes.
- Top area, large and bold: the main title in Korean, "망원동 가을 골목 축제", set in a chunky, friendly, custom hand-lettered Korean display typeface in deep brick red or forest green on the cream background. It can sit on two lines, with "망원동" on the first line and "가을 골목 축제" on the second. Every Hangul character must be accurate and complete.
- Bottom info band (a cream or deep green panel), in neat smaller type:
  "10.17 SAT – 10.18 SUN" (prominent, with an en dash between the dates)
  "망원시장 일대"
  "먹거리 · 플리마켓 · 골목 공연" (with middle dots as separators)

Composition: balanced margins and a grid-based layout like a printed silkscreen festival poster, with a few small decorative ginkgo leaves and maple leaves as accents. It should look polished and print-ready, with no watermark, no border mockup and no photo-realism.
```

고정한 것: 문구 전부, 제목과 정보 띠의 대략적 자리, 가을 팔레트와 손으로 그린 리소 질감, 글자 자리로 남길 빈 곳, 그 밖의 글자 금지. 열어 둔 것: 제목 두 줄 배치와 색의 선택지, 골목·사람·노점의 세부 구성, 글자 모양.

```
Portrait 3:4 aspect ratio, taller than it is wide. A Korean typographic poster made by a graphic designer in the language of a safety sign: one flat ground of signal yellow and a single ink of near-black. The words "오늘도 무사히 퇴근" are set in three stacked lines, "오늘도" / "무사히" / "퇴근", in an enormous custom heavy Hangul display lettering that fills the poster edge to edge, with tight spacing and letters nearly touching. The ㅇ of "오" is drawn as a round wall clock face with its two hands pointing to six o'clock, and the final ㄴ of "근" stretches into a long flat bar that runs off the right edge like a path out of the building. Flat color only, no gradients, no glow, no texture, no other text. Output the final image in a 3:4 portrait aspect ratio.
```

고정한 것: 문구와 세 줄 나눔, 잉크 둘(신호 노랑 지면 + 거의 검정), 빌려 온 관습(안전 표지판의 언어), 핵심 장치 둘(ㅇ이 시계, ㄴ이 출구로 뻗는 막대), 질감·발광 금지. 열어 둔 것: 거의 맞닿는 간격과 두 장치 안에서의 글자 형태, 여섯 시를 가리키는 시계의 세부 표현.

## 그림으로 만들까, 문서로 만들까

내용이 어디 실리는지로 가른다. 그림이 내용이면 이미지 모델이 완성하고, 글자가 내용이면(발언 템플릿, 법안 요점, 기념일 본문) Claude가 HTML로 짜서 `render`하고 GPT는 그 안에 놓일 그림만 만든다. 이미지 모델은 긴 문구를 지어내고 깨뜨리며 정확한 자리를 못 지키고, 템플릿은 회차마다 문구만 바뀌기 때문이다. 글자가 그림과 얽히면(획이 사물이 되는 제목) 그 글자는 모델이 그린다.

따로 배치할 요소는 하나에 생성 하나씩 만든다 — 묶어 뽑으면 낱개로 가르기 어렵다. 글꼴은 `doctor`가 주는 이름으로 고르고, HTML 템플릿은 작업 폴더에 남겨 다음 회차에 문구만 바꿔 다시 렌더한다. 글자가 실릴 홍보물은 방향을 고르는 라운드부터 유력한 시안에 실제 문구와 서명을 얹어 보여 준다 — 그림만 보이면 일러스트로 판정된다.

## 이 모델의 버릇

이 경로(Codex의 image_gen)에서 관측된 범위의 경험칙이다. 법칙이 아니니 결과가 다르면 결과를 믿는다.

- 문장 앞쪽에 쓴 대상이 화면을 더 차지하고 첫 시선을 받는다. 주인공을 문장 맨 앞에 둔다.
- 비율은 첫 문장에 독립된 문장으로 밝히면 맞는다("Portrait 4:5 aspect ratio, taller than it is wide."). 픽셀 치수를 덧붙여도 이득이 없다.
- 매체를 여럿 나열하면 일부만 남는다. 섞고 싶으면 주 매체 하나에 다른 매체의 성질을 붙인다("an oil painting with the flatness of a screenprint").
- 시키지 않은 구체값을 지어낸다 — 기관명·도메인·날짜·장소·숫자·슬로건을, 장마다 다르게, 그럴듯하게. 들어갈 글자는 전부 따옴표로 주고 "no other text"로 닫는다. 출처·인용·숫자는 문구를 주거나 자리만 잡고, 검수에서 지어낸 글자를 찾는다.
- 읽히지 않는 글자를 부탁해도 읽힐 듯한 가짜 글자(그럴듯한 한글 음절)를 쓴다. 글자가 없어야 하는 면은 "blank"로 비운다.
- 짧은 한글 문구는 대체로 정확하지만, 획을 사물로 바꾸는 식의 실험적 글자 구성에서는 글자가 깨진다. 글자는 늘 글자 단위로 확인한다.
- 참조 이미지는 팔레트와 서체를 강하게 옮기고, 스타일 참조로만 쓰라고 해도 배치와 정보 띠, 날짜 형식까지 베낀다. 새 구성을 만들려고 스타일만 빌렸는데 배치까지 따라왔으면 그 참조를 빼고 원하는 성질을 말로 옮긴다. 승인본을 참조로 붙인 수정에서 구성이 유지되는 건 의도한 것이다.
- 평면 지면의 색은 hex로 주면 거의 그대로 나온다. 색 이름과 비교 서술은 같은 색을 두고 더 멀리 간다.
- 물려받은 옛 메모에 "크기는 분수 대신 위치로", "크롭은 주제가 아니라 지면에", "hex는 안 먹는다"가 있어도 이 작업의 제약으로 옮기지 않는다 — 이 모델에서는 지켜지지 않는다.
- 정답이 있는 형태(국기의 괘, 상징)는 이름·개수 대신 구조로 서술했을 때 맞았다.
- 거부는 확률적이고 무엇이 막히는지는 미리 알 수 없다. 막힐 법한 대상이라도 안 된다고 단정하지 말고, 막힐 수 있다고 말한 뒤 대안과 함께 성진이 걸지 정하게 한다. 막혔을 때의 처방은 `generate`의 실패 줄이 말한다.

## 본다 · 고친다

모든 장을 본다 — 한 프롬프트의 장들도 서로 다르다. 글자·상징·손·얼굴·지어낸 글자는 `zoom`으로 원본 해상도에서 본다. 작은 결함(괘 하나의 어긋남, 받침 하나)은 한 번 훑어서는 놓친다.

측정은 눈이 체계적으로 틀리는 넷에만 쓴다: 여러 장 사이의 면적 비교(`zones`), 지면과 잉크의 명도 대비(`contrast` — 눈은 채도 차이를 명도 차이로 읽는다), "허전하다"의 원인이 가장자리에 닿는 잉크인지 잉크의 양인지(`edges`와 `zones`), 팔레트를 지켰는지(`colors`). 숫자는 성진이 결정하는 근거로 내놓는다 — "답답하다"만으로는 무엇을 고칠지 정하기 어렵지만 원인을 가리키는 숫자는 바로 결정을 낳는다. 숫자는 원인을 좁히는 데 쓰지 성진의 불만을 반박하는 데 쓰지 않고, 수치가 나아진 것을 미적으로 나아진 것으로 치지 않는다.

수용 여부를 바꿀 약점이 보이면 근거와 함께 먼저 말한다. 성진은 불만을 말로 하기 전에 느끼고, 그걸 먼저 짚는 게 아트 디렉터의 일이다. 그런 약점이 없으면 충족한 기준과 남은 불확실성을 말한다. 없는 결함을 지어내지 않는다.

고치는 길은 셋이고, 무엇이 틀렸는지로 고른다.
- 구성이 틀렸으면 프롬프트를 고친 새 버전으로 다시 뽑는다.
- 구도는 맞고 한 요소만 바꾸면 참조 수정이다: 승인본을 `--ref`로 붙인 `--method edit` 버전에, 바꿀 것 하나와 지킬 것을 낱낱이 적는다("나머지는 그대로"는 지시가 아니다). 참조 수정은 인페인팅이 아니라 전체 재생성이라 픽셀 보존을 약속하지 않는다.
- 작은 결함이면 참조 수정본에서 그 영역만 승인본에 `patch`한다. 합성 경계가 자연스럽게 이어지는지 확대해 본다.

결함이 컨셉인 작업(낙서, 거친 종이, 아마추어 필적)은 수정을 쌓지 말고 원본 프롬프트를 고쳐 다시 뽑는다. 참조 수정을 거듭하면 의도된 거칠기가 "더 잘 그린" 쪽으로 침식된다.

## 도구

`uv run "${CLAUDE_SKILL_DIR}/scripts/cli.py" --help`가 지도이고, 각 `<command> --help`가 인자·출력·실패를 혼자 다 말한다. 호출은 늘 그 모양 그대로 한 줄에 명령 하나로 쓴다 — `cd`, `&&`, `;`, 상대 경로, 셸 변수를 덧붙이면 허용 규칙과 어긋나 권한 창이 뜬다. 프롬프트는 `add`의 stdin으로 넣는다. 실패 줄이 분류·사유·다음 행동을 말한다. 일은 성진이 받아들인 결과를 합의한 크기와 위치로 납품했을 때 끝난다(`record --final --export`).
