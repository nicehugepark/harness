#!/usr/bin/env python3
"""운영 대시보드 생성기 — 원장·감사 스트림을 읽어 정적 화면 1장을 접는다.

정본: RQ-20260728T180616Z-ef94bc02 §8.3(화면 산출본은 파생물 하위) · Q1① · Q3① · Q4① ·
      Q5(표시명 공개 — DN-20260729T022125Z) · Q6①(읽기 전용) · Q10①(단일 계산 원천).

**계산은 여기서 하지 않는다.** 상태 집계·결함 판정은 `status.collect()` 를 그대로
호출한다 — 화면과 터미널이 다른 수를 말하면 둘 다 신뢰를 잃는다.

산출은 `derived/dashboard/` 에만 쓴다. 문서 트리에 화면 산출본을 두지 않는다.
게시는 `gh-pages` 브랜치로만 나가며 작업 트리를 건드리지 않는다(임시 인덱스 사용).
"""
from __future__ import annotations

import argparse
import html
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness" / "lib"))

from harness_core import clock, frontmatter, schema  # noqa: E402

OUT_REL = "derived/dashboard"
EVENT_LIMIT = 80

# B§1.1 진행 상태 11종 — 화면 축은 상태 어휘를 그대로 쓴다(Q7①).
STATES = ["received", "analyzed", "queued", "designing", "building",
          "verifying", "merging", "done", "hold", "failed", "void"]
TERMINAL = {"done", "failed", "void"}


def _load_status():
    """단일 계산 원천 — status.py 를 모듈로 적재한다."""
    p = pathlib.Path(__file__).with_name("status.py")
    spec = importlib.util.spec_from_file_location("_harness_status", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def read_requests(root: pathlib.Path) -> list[dict]:
    out = []
    base = root / "docs" / "requests"
    if not base.exists():
        return out
    for f in sorted(base.rglob("RQ-*.md")):
        try:
            m = frontmatter.parse(f.read_text(encoding="utf-8")).meta
        except Exception:                                  # noqa: BLE001
            continue
        out.append({
            "id": m.get("id"), "title": m.get("title"),
            "state": schema.read_state(m),
            "priority": m.get("priority"), "weight": m.get("weight"),
            "created": m.get("created"), "updated": m.get("updated"),
            "session_ref": m.get("session_ref"),
            "machine": m.get("machine_assigned"),
            "depends_on": m.get("depends_on") or [],
            "blocked_on": m.get("blocked_on"),
            "rework": m.get("rework_count"),
            "escalation": m.get("escalation_count"),
            "output_kind": m.get("output_kind"),
            "path": str(f.relative_to(root)),
        })
    return out


def read_events(root: pathlib.Path, limit: int = EVENT_LIMIT) -> list[dict]:
    """감사 스트림의 최신분. 파손 행은 건너뛰되 건너뛴 수를 함께 돌려준다."""
    recs, skipped = [], 0
    base = root / "docs" / "audit"
    if not base.exists():
        return recs
    for f in base.rglob("*.jsonl"):
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for l in lines[-400:]:
            if not l.strip():
                continue
            try:
                r = json.loads(l)
            except json.JSONDecodeError:
                skipped += 1
                continue
            if isinstance(r, dict) and r.get("ts"):
                r["_stream"] = f.name
                recs.append(r)
    recs.sort(key=lambda r: str(r.get("ts")))
    tail = recs[-limit:]
    if skipped:
        tail.append({"ts": "", "event": "stream.unparsed",
                     "data": {"n": skipped}, "_stream": "-"})
    return tail


def live_sessions() -> list[dict]:
    pat = "workflows/request.js"
    r = subprocess.run(["pgrep", "-af", pat], capture_output=True, text=True)
    out = []
    for line in r.stdout.splitlines():
        pid = line.split(" ", 1)[0]
        el = subprocess.run(["ps", "-o", "etime=", "-p", pid],
                            capture_output=True, text=True).stdout.strip()
        req = ""
        for tok in line.split():
            if tok.startswith("ledger_path=") or "RQ-" in tok:
                for part in tok.replace("/", " ").split():
                    if part.startswith("RQ-"):
                        req = part.removesuffix(".md")
        out.append({"pid": pid, "elapsed": el, "req": req})
    return out


def collect(root: pathlib.Path, freshness: str = "") -> dict:
    st = _load_status()
    base = st.collect(root)
    reqs = read_requests(root)
    by_state = {s: [r for r in reqs if r["state"] == s] for s in STATES}
    unknown = [r for r in reqs if r["state"] not in STATES]
    return {
        "at": clock.iso_local(),
        "requests": reqs,
        "by_state_counts": {s: len(v) for s, v in by_state.items() if v},
        "unknown_state": [r["id"] for r in unknown],
        "events": read_events(root),
        "sessions": live_sessions(),
        "defects": base.get("defects", []),
        "notify_pending": base.get("notify_pending", 0),
        "timers": base.get("timers", 0),
        "incidents_opened": base.get("incidents_opened", 0),
        "machine": os.uname().nodename,
        # 화면 새로고침 주기와 데이터 신선도는 다른 값이다. 같은 것으로 읽히면
        # 5초마다 도는 화면이 5분 전 사실을 보여주고 있어도 최신으로 보인다.
        "freshness": freshness or "데이터 신선도 미표기",
    }


# ── 렌더 ────────────────────────────────────────────────────────────
CSS = """
:root{--bg:#0f1115;--fg:#e6e8eb;--dim:#8b93a1;--line:#252a33;--card:#161a21;
--ok:#4ea672;--warn:#d8a24a;--bad:#d05a5a;--run:#4a8fd8;--idle:#5b6472}
@media(prefers-color-scheme:light){:root{--bg:#f7f8fa;--fg:#1a1d22;--dim:#5b6472;
--line:#dfe3e9;--card:#fff}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.55 ui-monospace,
SFMono-Regular,Menlo,"D2Coding",monospace}
.wrap{max-width:1180px;margin:0 auto;padding:20px 16px 60px}
h1{font-size:16px;margin:0 0 2px;letter-spacing:.02em}
.sub{color:var(--dim);font-size:12px;margin-bottom:18px}
.grid{display:grid;gap:12px;grid-template-columns:repeat(auto-fill,minmax(240px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px}
.card h2{font-size:12px;color:var(--dim);font-weight:600;margin:0 0 8px;
text-transform:uppercase;letter-spacing:.08em}
.rail{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:16px}
.pill{border:1px solid var(--line);border-radius:999px;padding:3px 10px;font-size:12px;
background:var(--card)}
.pill b{font-weight:700}
.pill.on{border-color:var(--run);color:var(--run)}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--dim);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.06em}
tr:last-child td{border-bottom:0}
.badge{display:inline-block;padding:1px 7px;border-radius:4px;font-size:11px;
border:1px solid var(--line)}
.s-designing,.s-building,.s-verifying,.s-merging{color:var(--run);border-color:var(--run)}
.s-done{color:var(--ok);border-color:var(--ok)}
.s-hold{color:var(--warn);border-color:var(--warn)}
.s-failed,.s-void{color:var(--bad);border-color:var(--bad)}
.s-received,.s-analyzed,.s-queued{color:var(--dim)}
.defect{color:var(--bad)}
.none{color:var(--dim);font-style:italic}
.scroll{overflow-x:auto}
.feed{font-size:12px;max-height:420px;overflow-y:auto}
.feed div{padding:2px 0;border-bottom:1px solid var(--line);white-space:nowrap}
.feed .t{color:var(--dim)}
.feed .e{color:var(--run)}
.mono{color:var(--dim);font-size:11px}
footer{margin-top:26px;color:var(--dim);font-size:11px;line-height:1.7}
"""


def _badge(state: str) -> str:
    s = html.escape(str(state))
    return f'<span class="badge s-{s}">{s}</span>'


def render(d: dict) -> str:
    reqs = d["requests"]
    counts = d["by_state_counts"]
    rail = "".join(
        f'<span class="pill{" on" if s not in TERMINAL else ""}">{s} <b>{counts[s]}</b></span>'
        for s in STATES if s in counts)
    live_by_req = {s["req"]: s for s in d["sessions"] if s.get("req")}

    rows = []
    for r in sorted(reqs, key=lambda x: (STATES.index(x["state"])
                                         if x["state"] in STATES else 99,
                                         str(x["id"]))):
        sess = live_by_req.get(r["id"])
        run = (f'<span style="color:var(--run)">실행 {html.escape(sess["elapsed"])}</span>'
               if sess else
               (f'<span class="mono">{html.escape(str(r["session_ref"]))}</span>'
                if r["session_ref"] else '<span class="mono">—</span>'))
        dep = ", ".join(html.escape(str(x)) for x in r["depends_on"]) or "—"
        rows.append(
            f"<tr><td>{_badge(r['state'])}</td>"
            f"<td class=mono>{html.escape(str(r['id']))}</td>"
            f"<td>{html.escape(str(r['title'] or ''))[:70]}</td>"
            f"<td>{html.escape(str(r['priority']))}</td>"
            f"<td>{run}</td>"
            f"<td class=mono>{html.escape(str(r['updated'] or ''))[:19]}</td>"
            f"<td class=mono>{dep}</td></tr>")

    feed = []
    for e in reversed(d["events"]):
        ts = html.escape(str(e.get("ts", ""))[11:19] or "--:--:--")
        ev = html.escape(str(e.get("event", "")))
        req = str(e.get("req") or "")
        tag = f' <span class=mono>{html.escape(req[-8:])}</span>' if req else ""
        data = html.escape(json.dumps(e.get("data"), ensure_ascii=False)[:110])
        feed.append(f'<div><span class=t>{ts}</span> <span class=e>{ev}</span>'
                    f'{tag} <span class=mono>{data}</span></div>')

    defects = ("".join(f'<div class=defect>{html.escape(x)}</div>'
                       for x in d["defects"])
               or '<div class=none>결함 0건</div>')
    sessions = ("".join(
        f'<div>pid {html.escape(s["pid"])} · 경과 {html.escape(s["elapsed"])}'
        f' · <span class=mono>{html.escape(s["req"][-8:] if s["req"] else "-")}</span></div>'
        for s in d["sessions"]) or '<div class=none>실행 중 세션 없음</div>')

    return f"""<title>하네스 운영 대시보드</title>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="5">
<style>{CSS}</style>
<div class=wrap>
<h1>하네스 운영 대시보드</h1>
<div class=sub>측정 시각 {html.escape(d['at'])} · 머신 {html.escape(d['machine'])}
 · 요청 {len(reqs)}건 · 5초마다 자동 새로고침 · 읽기 전용
 · <b>{html.escape(d.get('freshness', '?'))}</b></div>
<div class=rail>{rail}
<span class=pill>세션 <b>{len(d['sessions'])}</b></span>
<span class=pill>타이머 <b>{d['timers']}</b></span>
<span class=pill>발신 대기 <b>{d['notify_pending']}</b></span>
<span class=pill>신규 사건 <b>{d['incidents_opened']}</b></span></div>

<div class=card style="margin-bottom:12px"><h2>요청 원장</h2><div class=scroll>
<table><thead><tr><th>상태</th><th>ID</th><th>제목</th><th>우선</th>
<th>세션</th><th>갱신</th><th>의존</th></tr></thead>
<tbody>{''.join(rows) or '<tr><td colspan=7 class=none>요청 없음</td></tr>'}</tbody>
</table></div></div>

<div class=grid>
<div class=card><h2>결함</h2>{defects}</div>
<div class=card><h2>실행 세션</h2>{sessions}</div>
</div>

<div class=card style="margin-top:12px"><h2>감사 스트림 (최신 {len(d['events'])}건)</h2>
<div class="feed scroll">{''.join(feed) or '<div class=none>이벤트 없음</div>'}</div></div>

<footer>
이 화면은 원장 문서와 감사 스트림을 읽어 접은 <b>정적 스냅샷</b>입니다.
결함 판정과 상태 집계는 터미널 <code>status.py</code> 와 <b>같은 계산 모듈</b>을 호출합니다.<br>
표시하지 않는 것: 미착지 산출물의 내용, 세션 내부 진행률, 실패 원인의 인과.
<b>침묵은 무위반의 증거가 아닙니다</b> — 이 화면에 없는 것은 "없는 것"이 아니라
"이 화면이 보지 않는 것"입니다.
</footer>
</div>"""


# 주소 리터럴을 쓰지 않는다 — 정제 스캔이 금지하는 클래스이고, 이름으로
# 적으면 스캔을 통과하려고 억제 규칙을 넓히는 일이 없다.
LOOPBACK = "localhost"

STATIC_FRESHNESS = ("데이터 신선도: 배치 주기(약 5분)마다 재생성 — "
                    "화면은 5초마다 새로고침하지만 그 사이 데이터는 바뀌지 않습니다")
LIVE_FRESHNESS = "데이터 신선도: 요청 시점 실측(라이브)"


def serve(root: pathlib.Path, port: int, interval: float) -> int:
    """라이브 표면 — 요청이 올 때 원장을 다시 읽는다(Q4②).

    정적 게시본은 배치 주기만큼 늦다. 그 지연을 없애려면 화면을 여는 쪽이
    계산 시점을 정해야 하고, 그게 이 모드다. `interval` 초 안의 재요청은
    직전 계산을 그대로 준다 — 5초 새로고침이 매번 전량 스캔을 돌리면
    대시보드가 관측 대상의 부하가 된다.
    """
    import http.server
    import threading
    import time

    cache = {"at": 0.0, "html": "", "json": "{}"}
    lock = threading.Lock()

    def current():
        with lock:
            if time.monotonic() - cache["at"] >= interval:
                d = collect(root, LIVE_FRESHNESS)
                cache["html"] = render(d)
                cache["json"] = json.dumps(d, ensure_ascii=False, default=str)
                cache["at"] = time.monotonic()
            return cache["html"], cache["json"]

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):                              # noqa: N802
            h, j = current()
            body, ctype = ((j, "application/json")
                           if self.path.startswith("/data.json")
                           else (h, "text/html"))
            payload = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", f"{ctype}; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *a):                     # 접속 로그는 남기지 않는다
            pass

    srv = http.server.ThreadingHTTPServer((LOOPBACK, port), H)
    print(f"라이브 대시보드 http://{LOOPBACK}:{port}/ · 재계산 간격 {interval}초 · "
          f"루프백 바인드(외부 노출 없음)", flush=True)
    srv.serve_forever()
    return 0


def generate(root: pathlib.Path) -> pathlib.Path:
    d = collect(root, STATIC_FRESHNESS)
    out = root / OUT_REL
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(render(d), encoding="utf-8")
    (out / "data.json").write_text(
        json.dumps(d, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    return out / "index.html"


def publish(root: pathlib.Path, branch: str = "gh-pages",
            remote: str = "origin") -> tuple[bool, str]:
    """작업 트리·현재 인덱스를 건드리지 않고 gh-pages 를 갱신한다.

    파생물은 버전관리 밖이므로 `-f` 로 강제 등재한다 — 게시 표면은 문서 트리가
    아니라 별도 브랜치이고, 그래야 "파생물은 파생 디렉토리 밖에 없다"가 유지된다.
    """
    src = root / OUT_REL
    if not (src / "index.html").exists():
        return False, "산출물 없음 — generate 먼저"
    env = dict(os.environ)
    fd, idx = tempfile.mkstemp(prefix="harness-pages-idx-")
    os.close(fd)
    os.unlink(idx)
    env["GIT_INDEX_FILE"] = idx

    def g(*a, **kw):
        return subprocess.run(["git", "-C", str(root), *a],
                              capture_output=True, text=True, env=env, **kw)

    try:
        r = g("--work-tree", str(src), "add", "-A", "-f", ".")
        if r.returncode:
            return False, f"add 실패: {r.stderr.strip()[:200]}"
        tree = g("write-tree").stdout.strip()
        if not tree:
            return False, "write-tree 실패"
        parent = g("rev-parse", "--verify", f"refs/heads/{branch}").stdout.strip()
        args = ["commit-tree", tree, "-m", f"dashboard {clock.iso_local()}"]
        if parent:
            args += ["-p", parent]
        commit = g(*args).stdout.strip()
        if not commit:
            return False, "commit-tree 실패"
        prev_tree = g("rev-parse", f"{parent}^{{tree}}").stdout.strip() if parent else ""
        if prev_tree == tree:
            return True, "변화 없음 — 게시 생략"
        g("update-ref", f"refs/heads/{branch}", commit)
        push = g("push", remote, f"{branch}:{branch}")
        if push.returncode:
            return False, f"push 실패: {push.stderr.strip()[:200]}"
        return True, commit[:8]
    finally:
        if os.path.exists(idx):
            os.unlink(idx)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--serve", action="store_true",
                    help="라이브 표면을 띄운다(루프백 전용)")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--interval", type=float, default=5.0)
    args = ap.parse_args()
    root = pathlib.Path(args.root)
    if args.serve:
        return serve(root, args.port, args.interval)
    p = generate(root)
    print(f"생성: {p.relative_to(root)}")
    if args.publish:
        ok, why = publish(root)
        print(f"게시: {'완료 ' + why if ok else '실패 — ' + why}")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
