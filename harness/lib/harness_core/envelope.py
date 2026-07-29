"""정본: D§2.1 스트림 4계약 · D§2.2 공통 봉투 · D§2.3 R38 레코드 원자 append
      · D§2.6 상태형/사건형 분리 · A§2.4 기계 스트림 파일 계약.

무오염은 writer 유일성이 아니라 **레코드 원자 append 계약**으로 성립한다:
단일 write · 크기 상한 · 초과 스필 · 모드 스위치. 서브에이전트가 부모 세션
파일에 동시 append 해도 계약이 유지된다.

이 모듈에는 네트워크·커밋·파생 재생성 원어가 없다(R24·R33). 그것이
"훅이 파생·커밋·네트워크를 하지 않는다"의 장치다 — 규율이 아니라 부재다.
"""
from __future__ import annotations

import dataclasses
import fcntl
import hashlib
import json
import os
import pathlib
import secrets
import subprocess
import sys
import tempfile

from . import clock, ids, paths

# D§2.3 R38-2 — 계약 상수. 이식 가능한 원자성 하한을 보수적으로 차용한 값이다.
RECORD_MAX_BYTES = 4096

_PROC_NONCE = secrets.token_hex(8)

# D§2.1 — 스트림 4계약. 신설은 이 표의 등재와 같은 커밋에서만 가능하다(R23).
STREAMS = {
    "agent-events": {
        "type_code": "AU", "producer": "훅 9종(D§1.2)",
        "consumer": "대시보드 파생 뷰 · 수명주기 감독 · 실패 롤업",
        "transition": "append → 배치 파생 뷰 → 월 마감 컴팩션·콜드 아카이브",
        "retention": "활성 당월+2개월, 이후 압축 아카이브(삭제 금지)",
    },
    "workflow-journal": {
        "type_code": "AU", "producer": "Workflow 엔진(네이티브) → 수집기",
        "consumer": "검증 감사 · 재개",
        "transition": "네이티브 저널 → 요청 단위 수집 → 아카이브",
        "retention": "요청 종결 후 아카이브, 무기한",
    },
    "incidents": {
        "type_code": "FS", "producer": "봉투 조립기 · diff 방출기 · 에이전트",
        "consumer": "자기 개선 롤업(강제 소비)",
        "transition": "open → working → resolved | false_positive",
        "retention": "무기한",
    },
    "notify": {
        "type_code": "AU", "producer": "통지 파이프(D§5.4)",
        "consumer": "에스컬레이션 체커 · 대시보드",
        "transition": "append → 파생 뷰(미응답 목록) → 아카이브",
        "retention": "180일 후 아카이브",
    },
    "telemetry": {
        "type_code": "AU", "producer": "리소스 프로브(D§7.3)",
        "consumer": "용량 게이트 · 대시보드",
        "transition": "교차 사건 append(스냅샷은 별도)",
        "retention": "90일 후 아카이브",
    },
    "recorder-health": {
        "type_code": "AU", "producer": "전 훅의 dead-letter 경로",
        "consumer": "실패 롤업(강제 소비)",
        "transition": "append → 반복분 접기 → incidents 승격",
        "retention": "30일, 승격분 무기한",
    },
    "sched-decisions": {
        "type_code": "AU", "producer": "스케줄러 틱(B§4.5)",
        "consumer": "사후 감사 · 대시보드",
        "transition": "월 아카이브", "retention": "문서 체계 축 규약 승계",
    },
}

REQUIRED_ENVELOPE_FIELDS = ["id", "ts", "stream", "event", "plane", "machine",
                            "session", "data"]
# plane ≠ interactive 이면 req·role 도 필수(D§2.2 조건부)
CONDITIONAL_FIELDS = ["req", "role"]


@dataclasses.dataclass
class AppendResult:
    ok: bool
    path: str | None = None
    stream: str | None = None
    diverted: bool = False
    detail: str = ""


@dataclasses.dataclass
class HammerResult:
    lines: int
    parse_failures: int
    id_losses: int


def incident_key_id(machine: str, axis: str, violation: str) -> str:
    """A§3.2 — 동일 주체·맥락·위반은 하나의 사건 ID 로 수렴한다."""
    return ids.incident_id(f"machine={machine}|axis={axis}|violation={violation}")


def _atomic_append(path: pathlib.Path, line: str, mode: str = "atomic") -> None:
    """R38-1 — 직렬화된 레코드를 O_APPEND 파일에 **한 번의 write** 로 쓴다.

    부분 쓰기는 성공이 아니라 실패다. mode=flock 이면 그 한 번의 write 를
    OS 표준 advisory lock 으로 감싼다(잠금 구간은 write 1회로 한정).
    """
    data = line.encode("utf-8")
    if len(data) > RECORD_MAX_BYTES:
        raise ValueError("상한 초과 레코드는 이 함수에 도달하면 안 된다(R38-3)")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        if mode == "flock":
            fcntl.flock(fd, fcntl.LOCK_EX)
        written = os.write(fd, data)
        if written != len(data):
            raise OSError(f"부분 쓰기 — {written}/{len(data)}")
    finally:
        if mode == "flock":
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(fd)


class Writer:
    """스트림 1개에 대한 착지 원어. 이 클래스 밖에 append 경로가 없다(R25)."""

    def __init__(self, *, root_dir, stream: str, identity: dict,
                 append_mode: str = "atomic", rotate_bytes: int = 8 * 1024 * 1024,
                 rotate_lines: int = 10_000):
        if stream not in STREAMS:
            raise ValueError(
                f"미등재 스트림(R23 — 4계약 등재와 같은 커밋에서만 신설): {stream!r}"
            )
        self.root = pathlib.Path(root_dir)
        self.stream = stream
        self.identity = dict(identity)
        self.append_mode = append_mode
        self.rotate_bytes = rotate_bytes
        self.rotate_lines = rotate_lines
        self._seq = 0
        self._path: pathlib.Path | None = None
        self._prev_id: str | None = None

    # ── 파일 계약 (A§2.4) ────────────────────────────────────────
    def _writer_key(self) -> str:
        """A§2.4-4 — 세션 산 스트림은 세션당 1파일. 세션 없는 생산자는
        머신코드+UTC 날짜를 세션 자리에 넣는다."""
        return self.identity.get("session") or (
            f"{self.identity.get('machine', 'unknown')}-"
            f"{clock.now_utc():%Y%m%d}"
        )

    def _new_file(self) -> pathlib.Path:
        type_code = STREAMS[self.stream]["type_code"]
        sid = ids.mint(type_code,
                       machine_id=self.identity.get("machine", "unknown"),
                       session_id=self._writer_key())
        rel = paths.doc_path(sid, "public", self.stream)
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        meta = STREAMS[self.stream]
        header = {
            "stream": self.stream,
            "producer": meta["producer"],
            "consumer": meta["consumer"],
            "transition": meta["transition"],
            "retention": meta["retention"],
            "writer": self._writer_key(),
            "prev": self._prev_id,
            "schema": 1,
        }
        _atomic_append(p, json.dumps(header, ensure_ascii=False) + "\n",
                       self.append_mode)
        self._prev_id = sid
        return p

    def current_path(self) -> str:
        if self._path is None:
            self._path = self._new_file()
        return str(self._path)

    def rotate(self, reason: str = "") -> None:
        """A§2.4-5 — 새 ID 로 새 파일을 열고 헤더 prev 가 직전을 가리킨다.
        part 접미 파일명을 쓰지 않는다(파일명 문법 단일 유지)."""
        self._path = self._new_file()

    def _should_rotate(self) -> bool:
        if self._path is None or not self._path.exists():
            return False
        st = self._path.stat()
        if st.st_size >= self.rotate_bytes:
            return True
        if self._seq >= self.rotate_lines:
            return True
        return False

    # ── 봉투 조립 (D§2.2) ───────────────────────────────────────
    def build(self, event: str, data: dict) -> dict:
        self._seq += 1
        material = "|".join([
            str(self.identity.get("machine")), str(self.identity.get("session")),
            str(os.getpid()), _PROC_NONCE, clock.iso_utc(), str(self._seq),
            secrets.token_hex(4),
        ])
        rec = {
            "id": hashlib.sha256(material.encode()).hexdigest()[:12],
            "ts": clock.iso_utc(),
            "stream": self.stream,
            "event": event,
            "plane": self.identity.get("plane"),
            "machine": self.identity.get("machine"),
            "session": self.identity.get("session"),
            "data": data if isinstance(data, dict) else {"value": data},
        }
        for f in CONDITIONAL_FIELDS:
            if self.identity.get(f) is not None:
                rec[f] = self.identity[f]
        if self.identity.get("agent"):
            rec["agent"] = self.identity["agent"]
        if self.identity.get("engine"):
            rec["engine"] = self.identity["engine"]
        return rec

    def _missing_envelope_fields(self, rec: dict) -> list[str]:
        lack = [f for f in REQUIRED_ENVELOPE_FIELDS if rec.get(f) in (None, "")]
        if rec.get("plane") and rec["plane"] != "interactive":
            lack += [f for f in CONDITIONAL_FIELDS if rec.get(f) in (None, "")]
        return lack

    # ── 착지 ────────────────────────────────────────────────────
    def append(self, event: str, data: dict) -> AppendResult:
        rec = self.build(event, data)
        lack = self._missing_envelope_fields(rec)
        if lack:
            # R26 — 결손 레코드는 본류에 착지하지 않는다. 주체 미상 기록은
            # 재발 방지에 쓸 수 없다(신원 조인 실패 실측).
            return self._dead_letter("envelope-missing-field",
                                     {"missing": lack, "event": event})

        line = json.dumps(rec, ensure_ascii=False) + "\n"
        if len(line.encode("utf-8")) > RECORD_MAX_BYTES:
            rec = self._spill(rec)
            line = json.dumps(rec, ensure_ascii=False) + "\n"

        if self._should_rotate():
            self.rotate("cap")
        try:
            _atomic_append(pathlib.Path(self.current_path()), line,
                           self.append_mode)
        except (OSError, ValueError) as exc:
            return self._dead_letter("append-failed",
                                     {"event": event, "error": str(exc)})
        return AppendResult(True, self.current_path(), self.stream)

    def _spill(self, rec: dict) -> dict:
        """R38-3 — 전문은 스필 파일(임시 + rename), 본류에는 참조만."""
        spill_dir = (pathlib.Path(self.current_path()).parent / "spill")
        spill_dir.mkdir(parents=True, exist_ok=True)
        blob = json.dumps(rec["data"], ensure_ascii=False)
        target = spill_dir / f"{rec['id']}.json"
        fd, tmp = tempfile.mkstemp(dir=spill_dir, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(blob)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
        rec["data"] = {
            "oversize": True,
            "size": len(blob.encode()),
            "digest": hashlib.sha256(blob.encode()).hexdigest()[:16],
            "spill_ref": str(target.relative_to(self.root)),
        }
        return rec

    def _dead_letter(self, error_class: str, detail: dict) -> AppendResult:
        """D§7.1 — 의존이 0인 최단 경로. 기록기가 기록기를 부르는 재귀를 금지한다."""
        try:
            rec = {
                "ts": clock.iso_utc(),
                "machine": self.identity.get("machine"),
                "session": self.identity.get("session"),
                "error_class": error_class,
                "msg": json.dumps(detail, ensure_ascii=False)[:200],
            }
            sid = ids.mint("AU", machine_id=self.identity.get("machine", "unknown"),
                           session_id=self._writer_key())
            p = self.root / paths.doc_path(sid, "public", "recorder-health")
            p.parent.mkdir(parents=True, exist_ok=True)
            _atomic_append(p, json.dumps(rec, ensure_ascii=False) + "\n",
                           self.append_mode)
            return AppendResult(True, str(p), "recorder-health", diverted=True,
                                detail=error_class)
        except Exception as exc:            # noqa: BLE001 — 최후 기록
            print(f"[recorder-health-unreachable] {error_class}: {exc}",
                  file=sys.stderr)
            return AppendResult(False, None, "recorder-health", diverted=True,
                                detail="관측 불가 구간(정직 공지 — S§9.4-21)")


# ── D§2.6 상태형 스냅샷과 사건형 기록의 분리 ─────────────────────
@dataclasses.dataclass
class DiffResult:
    opened: list
    resolved: list


class DiffEmitter:
    """탐지기·프로브가 쓰는 유일한 흐름. 전량 재방출 원어가 없다(R27).

    선행 운영의 최대 왜곡은 탐지-무소비 루프였다 — 총 발생의 87.3%가 동일
    사건의 재탐지였고 신규 작업 0에도 건수가 선형 증가했다. 그 구조를
    "현재 상태는 덮어쓰고 사건은 diff 에서만" 으로 차단한다.
    """

    def __init__(self, *, root_dir, axis: str, machine: str, identity: dict):
        self.root = pathlib.Path(root_dir)
        self.axis = axis
        self.machine = machine
        self.snapshot = self.root / paths.audit_snapshot_path(axis, machine)
        self.writer = Writer(root_dir=root_dir, stream="incidents",
                             identity=identity)

    def _read(self) -> set:
        if not self.snapshot.exists():
            return set()
        try:
            return set(json.loads(self.snapshot.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            return set()

    def _write(self, cur: set) -> None:
        self.snapshot.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.snapshot.with_suffix(".tmp")
        tmp.write_text(json.dumps(sorted(cur), ensure_ascii=False),
                       encoding="utf-8")
        os.replace(tmp, self.snapshot)

    def observe(self, current: set) -> DiffResult:
        prev = self._read()
        cur = set(current)
        opened = sorted(cur - prev)
        resolved = sorted(prev - cur)
        for k in opened:
            self.writer.append("incident.opened", {
                "incident_id": incident_key_id(self.machine, self.axis, k),
                "key": k, "state": "open", "count_delta": 1,
                "owner_role": "qa",
            })
        for k in resolved:
            self.writer.append("incident.state_change", {
                "incident_id": incident_key_id(self.machine, self.axis, k),
                "key": k, "to": "resolved-candidate",
            })
        self._write(cur)
        return DiffResult(opened, resolved)


# ── D§2.3 경합 해머 시험 (프리플라이트 — append_mode 판정) ────────
_HAMMER_CHILD = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
from harness_core import envelope
path, tag, count, mode = sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5]
import pathlib
p = pathlib.Path(path)
pad = "x" * 3600
for i in range(count):
    rec = {"tag": tag, "i": i, "pad": pad}
    line = json.dumps(rec) + "\n"
    envelope._atomic_append(p, line, mode)
"""


def hammer_test(root_dir, *, processes: int = 8, per_process: int = 60,
                mode: str = "atomic") -> HammerResult:
    """판정식: 물리 줄 수 == N×M ∧ 전 줄 파싱 가능 ∧ id 유실 0.

    atomic 모드로 1회 → 실패 시 flock 모드로 1회 → 둘 다 실패면 배치 거부.
    """
    root = pathlib.Path(root_dir)
    root.mkdir(parents=True, exist_ok=True)
    target = root / "hammer.jsonl"
    if target.exists():
        target.unlink()
    lib = str(pathlib.Path(__file__).resolve().parents[1])
    script = root / "_hammer_child.py"
    script.write_text(_HAMMER_CHILD, encoding="utf-8")
    procs = [
        subprocess.Popen([sys.executable, str(script), lib, str(target),
                          f"p{i}", str(per_process), mode])
        for i in range(processes)
    ]
    for p in procs:
        p.wait()
    lines = target.read_text(encoding="utf-8").splitlines()
    lines = [ln for ln in lines if ln.strip()]
    bad = 0
    seen = set()
    for ln in lines:
        try:
            rec = json.loads(ln)
            seen.add((rec["tag"], rec["i"]))
        except (json.JSONDecodeError, KeyError):
            bad += 1
    return HammerResult(len(lines), bad,
                        processes * per_process - len(seen))
