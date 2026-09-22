# 🎨 Visual & UX Design Specification & Vibe Coding Philosophy

> **"코드가 존재한다는 사실조차 잊고 바이브에 모든 것을 맡긴다."** — Andrej Karpathy  
> 본 프로젝트의 모든 프론트엔드 UI/UX는 **Apple 공식 웹사이트(Apple.com)의 압도적인 바이브**와 **Pinterest Aesthetic Design System**을 결합한 5대 핵심 철학을 준수합니다.

---

## 🏛️ 1. Apple.com 분석 기반: 압도적 바이브의 5대 핵심 요소

1. **극단적 미니멀리즘과 여백(Negative Space)의 미학**
   * 불필요한 테두리선(Border), 화려한 장식, 빽빽한 배경을 철저히 배제.
   * 광활한 여백(Negative Space)으로 사용자의 시선을 오직 '핵심 제품'과 '헤드라인 카피'에 강제 집중.
2. **기술이 아닌 '경험' 중심의 스토리텔링**
   * 딱딱한 기술 스펙 대신 감성적이고 직관적인 사용자 경험 중심의 대형 타이포그래피 활용.
3. **시네마틱 스크롤과 마이크로 인터랙션 (Cinematic Micro-Interactions)**
   * 스크롤에 따른 부드러운 스며들기(`fade-in-up`), 3D 오브젝트 회전, 0.3s `cubic-bezier` 트랜지션.
4. **일관된 벤토 UI 모듈화 (Bento Grid Composition)**
   * 모서리가 둥근 격자형 카드(Bento Box)로 정보를 그룹화하여 시각적 인지 부하 최소화.
5. **완벽하게 은닉된 복잡성 (Hiding Complexity)**
   * 표면은 매끄럽고 단순하지만 이면의 상태 관리, 렌더링 최적화, 비동기 처리는 완벽히 내재화.

---

## 💡 2. 성공적인 AI 바이브 코딩 5대 실천 원칙 (Vibe Coding Principles)

### 원칙 1. "How(구현 방식)"가 아닌 "Vibe(경험과 느낌)"를 디렉팅하라
* ❌ **Bad Prompt:** `"div 3개 만들고 flex-direction은 column으로 해. 가운데 정렬하고 폰트는 48px로 설정해 줘."`
* ⭕ **Vibe Prompt:** `"Apple 메인 페이지 히어로 섹션처럼 프리미엄하고 시네마틱한 느낌으로 만들어줘. 텍스트는 압도적으로 크고 두껍게 중앙에 배치하고, 시선이 분산되지 않게 넓은 여백을 줘."`

### 원칙 2. 복잡성은 AI의 블랙박스에 은닉하고, 뼈대만 통제하라
* 상태 관리, API 보일러플레이트, 스크롤 최적화는 AI에게 전적으로 위임.
* 에러 발생 시 스트레스 없이 에러 로그를 전달하여 흐름을 유지하며 비즈니스 아키텍처와 UI 흐름에만 집중.

### 원칙 3. 덜어냄의 미학, "빼기(-)" 프롬프팅을 생활화하라
* AI의 오버엔지니어링(조잡한 그림자, 과도한 색상, 복잡한 선)을 덜어내기.
* *"불필요한 테두리 선과 배경색을 제거하고, 오직 '넓은 여백'으로만 구분하며 포인트 컬러 하나만 남겨줘."*

### 원칙 4. 컴포넌트를 극도로 모듈화하여 점진적으로 조립하라 (Bottom-Up)
* 한 번에 거대한 페이지를 요구하지 않고, 완벽한 단일 카드/컴포넌트를 먼저 완성한 뒤 벤토 그리드로 결합.

### 원칙 5. 집요한 마이크로 폴리싱(Micro-Polishing)으로 바이브를 완성하라
* 0.1초 애니메이션 타이밍, `backdrop-filter: blur()`, 호버 시 부드러운 텐션감(`cubic-bezier(0.16, 1, 0.3, 1)`) 튜닝.

---

## 🎨 3. 디자인 시스템 토큰 (Pinterest Aesthetic Tokens)

* **Canvas (기본 배경)**: `#FAF9F6` (Warm Milk Cream) / `#FFFFFF`
* **Pastel Card Accents**:
  * 🪻 **Soft Lavender**: `#C4B5FD` / `#DDD6FE`
  * 🌿 **Baby Mint**: `#A7F3D0` / `#CCFBF1`
  * 🍑 **Peach Pink**: `#FED7AA` / `#FBCFE8`
  * ☁️ **Sky Blue**: `#BAE6FD` / `#E0F2FE`
* **Card & Bento Radius**: `rounded-3xl` (`28px ~ 36px`)
* **Interactive Pills & Badges**: `rounded-full` (`border-radius: 9999px`)
* **Material**: Milky Frosted Glass (`backdrop-filter: blur(20px)`, `background: rgba(255, 255, 255, 0.75)`, `border: 1.5px solid rgba(255, 255, 255, 0.85)`)
* **Touch Target**: Minimum 48dp on Mobile Viewport (390px)
* **Overflow Protection**: `overflow-x: hidden` 강제 준수 (가로 스크롤 결함 원천 차단)
