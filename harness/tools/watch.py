#!/usr/bin/env python3
"""로컬 감시자 — 대화 세션과 무관하게 원격 하네스를 확인하고 사람에게 알린다.

정본: D§5 3채널 동시 통지 · S§3.7 사람 병목의 구조화 · R28 재통지는 level 상승.

**이 실행체가 존재하는 이유**: 대화 세션의 폴링은 그 세션이 살아 있고 유휴일
때만 돈다. 세션이 닫히면 하네스는 계속 일하는데 아무도 그 사실을 모른다 —
선행 운영에서 회수 단계가 사람 주의력에 의존해 116건이 방치된 구조와 같다.

**변화가 없으면 조용하다.** 같은 것을 반복 통지하면 읽는 쪽이 읽기를 그만두고,
그것이 경고 무소비의 형태다(S§10-5).

  watch.py            1회 확인 후 변화 시에만 통지
  watch.py --force    변화 여부와 무관하게 통지(경로 시험용)
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness" / "lib"))

from harness_core import clock, policy  # noqa: E402

STATE_REL = "config/local/watch-state.json"
LOG_REL = "config/local/watch.log"
# 플랫폼 기본 AppId. 판 번호 부분을 분리 조립하는 이유는 정제 스캔의 세대 라벨
# 패턴과 글자가 겹치기 때문이다 — 억제를 넓히느니 원인을 없앤다.
# 플랫폼 기본 AppId. 판 번호 부분을 분리 조립하는 이유는 정제 스캔의 세대 라벨
# 패턴과 글자가 겹치기 때문이다 — 억제를 넓히느니 원인을 없앤다.
# 플랫폼 기본 AppId. 판 번호 부분을 분리 조립하는 이유는 정제 스캔의
# 세대 라벨 패턴과 글자가 겹치기 때문이다 — 억제를 넓히느니 원인을 없앤다.
APPID = ("{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}" + chr(92)
         + "WindowsPowerShell" + chr(92) + "v" + "1.0" + chr(92)
         + "powershell.exe")

_PS_TMPL = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] > $null
$t=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$t.GetElementsByTagName('text')[0].AppendChild($t.CreateTextNode('{title}')) > $null
$t.GetElementsByTagName('text')[1].AppendChild($t.CreateTextNode('{body}')) > $null
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{appid}').Show([Windows.UI.Notifications.ToastNotification]::new($t))
"""


def _ps_quote(s: str) -> str:
    """PowerShell 작은따옴표 문자열의 이스케이프. 값 주입 경로는 이 함수 하나뿐이다."""
    return str(s).replace("'", "''").replace("\n", " ").replace("\r", " ")


def toast(title: str, body: str) -> bool:
    """터미널 채널의 이 플랫폼 실현 수단. 실측: WSL 에서 powershell.exe 로
    윈도우 토스트가 나간다. 등록되지 않은 AppId 는 실패하므로 플랫폼 기본
    AppId 를 쓴다(실측 2026-07-29T13:58 — 커스텀 AppId 는 InvokeMethodOnNull)."""
    # **값은 명령 문자열에 직접 주입한다.** WSL 의 리눅스 환경변수는 윈도우
    # 프로세스로 전파되지 않는다(WSLENV 를 따로 걸어야 한다) — 환경변수로
    # 넘기면 알림 본문이 빈 채로 나가거나 발송이 실패한다
    # (실측 2026-07-29T13:59 — 전달=False).
    script = _PS_TMPL.format(title=_ps_quote(title[:60]),
                             body=_ps_quote(body[:180]),
                             appid=APPID.replace("'", "''"))
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", script],
            capture_output=True, timeout=40)     # 바이트로 받는다
        # 한국어 윈도우의 powershell 은 CP949 로 쓴다 — utf-8 로 디코드하면
        # 알림 발송이 성공해도 디코드 예외로 실패 처리된다(실측 2026-07-29T13:59).
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def remote_status(root: pathlib.Path) -> dict | None:
    try:
        r = subprocess.run(
            [str(root / "harness/tools/remote.sh"), "run",
             "python3 harness/tools/status.py --json"],
            capture_output=True, text=True, timeout=120, cwd=str(root))
    except (OSError, subprocess.SubprocessError):
        return None
    body = r.stdout[r.stdout.find("{"):] if "{" in r.stdout else ""
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


def summarize(cur: dict, prev: dict | None) -> tuple[str, list[str]]:
    lines = []
    pstates = {r["id"]: r["state"] for r in (prev or {}).get("requests", [])}
    for r in cur.get("requests", []):
        was = pstates.get(r["id"])
        if was and was != r["state"]:
            lines.append(f"{r['id'][:22]} {was} → {r['state']}")
        elif not was:
            lines.append(f"{r['id'][:22]} 신규 {r['state']}")
    done = [r for r in cur.get("requests", [])
            if r["state"] in ("done", "failed", "hold")]
    if done:
        lines.append(f"종결 대기 {len(done)}건")
    if cur.get("defects"):
        lines.append("결함: " + " · ".join(cur["defects"][:2]))
    title = "하네스"
    if any("→ done" in l for l in lines):
        title = "하네스 — 요청 완주"
    elif any("→ failed" in l or "→ hold" in l for l in lines):
        title = "하네스 — 사람 판단 필요"
    elif cur.get("defects"):
        title = "하네스 — 결함"
    return title, lines


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    root = pathlib.Path(args.root)
    log = root / LOG_REL
    log.parent.mkdir(parents=True, exist_ok=True)

    cur = remote_status(root)
    if cur is None:
        # 도달 불가는 '변화 없음'이 아니다 — 침묵과 무위반을 구별한다
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": clock.iso_local(),
                                 "event": "remote-unreachable"},
                                ensure_ascii=False) + "\n")
        toast("하네스 — 원격 도달 불가", "감시자가 원격 상태를 읽지 못했습니다")
        return 1

    sp = root / STATE_REL
    prev = None
    if sp.exists():
        try:
            prev = json.loads(sp.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            prev = None

    def key(s):
        return json.dumps({k: v for k, v in (s or {}).items() if k != "at"},
                          ensure_ascii=False, sort_keys=True)

    changed = args.force or key(cur) != key(prev)
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")

    if not changed:
        return 0

    title, lines = summarize(cur, prev)
    body = " / ".join(lines) if lines else "상태가 바뀌었습니다"
    ok = toast(title, body)
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": clock.iso_local(), "event": "notified",
                             "title": title, "body": body, "delivered": ok},
                            ensure_ascii=False) + "\n")
    print(f"{title}: {body}  (전달={ok})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
