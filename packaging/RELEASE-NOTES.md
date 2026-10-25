0.12.1은 `Failed to resolve Python.Runtime.Loader.Initialize`로 자체 창이 시작되지 않는 경우를 수정했습니다. 다운로드된 관리 DLL의 Windows Internet Zone 표시를 유지한 상태에서 실행할 수 있도록 앱 전용 .NET 설정을 추가했습니다. **EXE 옆 `MyDarkRoom.exe.config`도 함께 보관**하세요. 동일 오류 재현과 수정 후 전체 Windows 검사를 통과했습니다.

자체 편집 창을 사용하는 Windows 10/11 x64용 MyDarkRoom입니다.

1. 아래 Windows-x64.zip 전체를 새 폴더에 압축 해제하세요.
2. 기존 실행 창을 종료하고 `MyDarkRoom.exe`를 실행하세요. `_internal` 폴더도 함께 보관하세요.
3. 별도 브라우저 없이 편집 창이 열리고, 창의 X로 작업과 임시 파일을 정리한 뒤 종료합니다.

Edge WebView2를 내장 창으로 사용하며 기존 RAW 엔진과 4MP WebGL2 미리보기, 크롭 후 200% 확대, 마스크별 선예도/텍스처/명료도/디헤이즈를 유지합니다. JPEG는 Windows 저장 대화상자 또는 선택한 폴더에 직접 저장합니다. 큰 JPEG를 JavaScript 브리지로 복사하지 않습니다. 새로 열 때는 빛(노출)만 펼치고 마스크를 포함한 나머지 항목은 접습니다.

Python 설치는 필요 없습니다. Microsoft Edge WebView2 Runtime이 필요하며 대부분의 Windows에 이미 설치되어 있습니다. 없다면 [Microsoft 공식 런타임](https://go.microsoft.com/fwlink/p/?LinkId=2124703)을 설치하세요. 구형 IE 엔진으로 대체하지 않습니다.

자체 창의 프리셋은 업데이트 후에도 같은 사용자 프로필에 보관합니다. 기존 외부 브라우저의 프리셋은 JSON으로 내보낸 뒤 새 앱에서 불러올 수 있습니다. Nikon Z5 II 고효율/고효율★에는 기존처럼 Adobe DNG Converter를 별도 설치해야 합니다.

Windows 엔진 테스트, Python 없는 PATH의 실제 EXE 검사, 내장 WebView2의 실제 NEF 로딩/확대/마스크 GPU 처리/Windows 저장 대화상자/JPEG/종료 검사를 통과한 파일만 게시합니다. 결과는 `windows-smoke.json`에 있습니다. 실제 PC의 GPU 프레임 속도와 모든 카메라 파일을 보장하는 검사는 아닙니다.

개인 배포판에는 코드 서명 인증서가 없습니다. ZIP은 `SHA256SUMS.txt`로 확인할 수 있습니다. 진단 로그는 `%LOCALAPPDATA%\MyDarkRoom\logs`에 있습니다.
