# 검증 기록

## 2단계: GPU 미리보기·히스토그램·100%·전후 비교

동일한 실제 NEF 샘플로 **12개 테스트 통과**(2.78초). 기존 1단계의 긴 변 1600px 제한은 종횡비 유지 최대 4,000,000픽셀 제한으로 변경했습니다. 샘플 미리보기는 **2454×1629 = 3,997,566픽셀**입니다.

- 16비트 선형 RGB 바이너리의 크기/내용, 원본 좌표로 가져온 크롭과 전체 현상 데이터 일치 확인.
- 400만 화소 예산, 작은 사진의 불필요한 확대 방지, 잘못된 크롭 요청 거절, 경계에서 크기 축소 확인.
- 최초 100% 접근 이후 크롭/CPU 대체 미리보기/JPEG 저장이 전체 RAW 현상 캐시를 재사용하는지 검증.
- 세션 제거 시 RAW 사본과 원본 해상도 캐시 파일 제거 확인.

실행 중인 서버를 대상으로 아래 브라우저 검증도 통과했습니다.

```sh
/workspace/.mydarkroom/venv/bin/python scripts/browser_check.py \
  --nef /tmp/mydarkroom-sample.NEF --chromium /usr/bin/chromium --software-gl
# 150% 화면 배율에 해당하는 디바이스 픽셀 비율
/workspace/.mydarkroom/venv/bin/python scripts/browser_check.py \
  --nef /tmp/mydarkroom-sample.NEF --chromium /usr/bin/chromium --software-gl --dpr 1.5
```

- WebGL2 셰이더: 검정/흰색/색상 샘플과 7개 보정값을 사용하는 5개 조합에서 CPU 출력과 채널당 최대 **1/255** 차이.
- RGB 히스토그램 Worker에 알려진 입력을 전달해 밝기 구간별 개수 검증. 실제 사진에서는 전체 미리보기 픽셀 수와 집계 수 일치.
- 슬라이더를 연속 변경해 마지막 결과가 적용되고, WebGL2 모드에서는 서버 JPEG 미리보기를 다시 요청하지 않는지 확인.
- 보정 전 누르기, 전후 분할과 경계 이동 시 실제 화면 픽셀 변화 확인.
- 100% 확대 시 중앙 화면 픽셀이 해당 원본 좌표의 보정 결과와 1/255 이내로 일치. 확대 후 드래그로 원본 크롭 좌표 변경 확인.
- WebGL2를 비활성화한 CPU 대체 모드에서도 히스토그램/보정/확대/드래그/비교/JPEG 다운로드 통과.
- 100% 및 150% 픽셀 비율에서 위 동작 검증. 모바일 폭의 가로 넘침 및 JavaScript 런타임 오류 없음.

**검증 한계:** 클라우드 Chromium의 그래픽 경로는 **SwiftShader 소프트웨어 렌더러**입니다. 셰이더의 기능과 수치를 검증했으며 실제 GPU 가속 속도, Windows 그래픽 드라이버, 최신 니콘 기종 전체 지원을 검증한 것은 아닙니다. UI에서도 소프트웨어 렌더러를 별도로 표시합니다. 선택적 브라우저 검증 의존성은 `requirements-browser.txt`에 있으며 일반 앱 실행에는 필요하지 않습니다.

## 1단계 기록

환경: Linux x86_64, Python 3.12.14, rawpy 0.27.1, LibRaw 0.22.1. Windows/macOS와 최신 Nikon 기종 전체는 아직 검증하지 않았습니다.

## 실제 NEF 검증

외부 테스트 샘플: [rawpy의 iss030e122639.NEF](https://github.com/letmaik/rawpy/blob/401a9e5e4cd2348b8d11bdddd567915de07c0a83/test/iss030e122639.NEF). 파일은 저장소에 포함하지 않고 임시 경로에서만 사용했습니다.

SHA256: `5922721d13f11795557d97fdeb0a60b900086c402bc82a848ff280d15b99ffd4`

```sh
MYDARKROOM_TEST_NEF=/tmp/mydarkroom-sample.NEF OMP_NUM_THREADS=4 \
  /workspace/.mydarkroom/venv/bin/python -m pytest -q
```

결과: **10 passed**, 2.25초. Starlette 테스트 클라이언트의 httpx 사용에 관한 폐기 예정 경고 1개가 있었으며 테스트 실패는 없습니다.

- NEF 업로드 → 기본/수정 미리보기 비교 → 전체 해상도 JPEG를 실제 디코더로 검증.
- 출력 4284×2844, JPEG, sRGB ICC 프로파일 포함. 원본 NEF 해시 유지.
- 손상/빈 파일/잘못된 확장자 처리, 보정값 범위, 교차 출처 요청 차단, 세션 정리, 색/노출/톤 보정 검증.
- 단일 샘플에서 최초 미리보기 현상 약 0.294초, 캐시된 사진의 보정/JPEG 인코딩 약 0.064초. 이는 해당 머신의 단일 측정이며 전체 기종 성능 보장은 아닙니다.

## 브라우저 검증

설치된 Chromium에서 Playwright로 실행했습니다. 별도의 브라우저 바이너리 다운로드는 하지 않았습니다.

- 파일 선택 → 실제 사진 표시 → 노출 +0.8 조절 → 보정 전 비교 → JPEG 다운로드 → 초기화 통과.
- 브라우저 다운로드 파일명과 4284×2844 JPEG 및 ICC 프로파일 확인.
- 1440×1000 데스크톱 화면과 390×844 모바일 화면 확인. 모바일 가로 넘침 없음.
- JavaScript 런타임 오류 없음.

`python -m pip check`, `node --check darkroom/static/app.js`, `git diff --check`도 통과했습니다. 브라우저 자동화 도구는 검증 머신에만 별도로 설치했으며 앱 실행 의존성이 아닙니다.

## 남은 확인

사용자 카메라의 실제 NEF, 특히 최신 바디 및 High Efficiency / High Efficiency★ 압축 샘플로 호환성을 확인해야 합니다. 현재 기록으로 모든 Nikon NEF 지원을 주장할 수 없습니다.
