"""정본: A§5.1 문서 본문 구조 · A§4.1~4.2 frontmatter · A§5.2 E01.

frontmatter 파싱과 본문 절 추출만 담당한다. 스키마 판정은 schema.py,
착지 판정은 gate.py 가 한다 — 파싱과 판정을 한 모듈에 두면 "파싱 실패"와
"규격 위반"이 같은 오류 코드로 뭉개진다(E01 과 E03 은 다른 사실이다).
"""
from __future__ import annotations

import dataclasses
import re

import yaml

_FM_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n(.*)\Z", re.S)
_H2_RE = re.compile(r"^##\s+(.+?)\s*$", re.M)
# yaml 코드 펜스 안의 기계 판독 블록(A§4.2 — 검증 기준 등)
_YAML_FENCE_RE = re.compile(r"```yaml\r?\n(.*?)```", re.S)


class ParseError(ValueError):
    """E01 — frontmatter 파싱 불능."""


@dataclasses.dataclass
class Document:
    meta: dict
    body: str
    raw: str

    def sections(self) -> dict[str, str]:
        """`## 제목` 단위 절 사전. 값은 다음 H2 직전까지의 본문이다."""
        out: dict[str, str] = {}
        marks = list(_H2_RE.finditer(self.body))
        for i, m in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(self.body)
            out[m.group(1).strip()] = self.body[m.end():end].strip()
        return out

    def section(self, title: str) -> str | None:
        return self.sections().get(title)

    def has_nonempty_section(self, title: str) -> bool:
        s = self.section(title)
        return bool(s and s.strip())

    def yaml_blocks(self) -> list[dict]:
        """본문 안의 yaml 코드 펜스를 파싱한 목록. 파싱 실패 블록은 제외한다."""
        out = []
        for m in _YAML_FENCE_RE.finditer(self.body):
            try:
                v = yaml.safe_load(m.group(1))
            except yaml.YAMLError:
                continue
            if isinstance(v, dict):
                out.append(v)
        return out

    def discipline_items(self) -> list[str]:
        """X2 — `- [R<n>]` 리스트 아이템(연속 들여쓰기 줄 병합)."""
        items: list[str] = []
        cur: str | None = None
        for line in self.body.splitlines():
            if re.match(r"^\s*-\s\[R\d+\]", line):
                if cur is not None:
                    items.append(cur)
                cur = line
            elif cur is not None and re.match(r"^\s+\S", line):
                cur += "\n" + line
            elif cur is not None:
                items.append(cur)
                cur = None
        if cur is not None:
            items.append(cur)
        return items


def parse(text: str) -> Document:
    m = _FM_RE.match(text or "")
    if not m:
        raise ParseError("frontmatter 구분자(---)를 찾지 못했다")
    try:
        meta = yaml.safe_load(m.group(1))
    except yaml.YAMLError as exc:
        raise ParseError(f"frontmatter YAML 파싱 실패: {exc}") from exc
    if not isinstance(meta, dict):
        raise ParseError("frontmatter 가 매핑이 아니다")
    return Document(meta=meta, body=m.group(2), raw=text)


def render(meta: dict, body: str) -> str:
    """frontmatter 를 결정론적 키 순서로 직렬화한다.

    키 순서를 고정하는 이유: 같은 내용의 문서가 쓸 때마다 다른 바이트가 되면
    내용 해시 대조(X3 동결 판 검사·설치 매니페스트 대조)가 무의미해진다.
    """
    ordered = {k: meta[k] for k in KEY_ORDER if k in meta}
    ordered.update({k: v for k, v in meta.items() if k not in ordered})
    dumped = yaml.safe_dump(
        ordered, allow_unicode=True, sort_keys=False, default_flow_style=False,
        width=10_000,
    )
    body = body if body.endswith("\n") else body + "\n"
    return f"---\n{dumped}---\n\n{body.lstrip()}"


# 직렬화 키 순서 — 사람이 읽을 때의 정보 순서(식별 → 분류 → 신원 → 내용 → 참조)
KEY_ORDER = [
    "schema", "id", "addr", "type", "title", "visibility", "state", "stage",
    "root", "created", "updated", "machine", "session",
    "author", "requester", "participants",
    "what", "why", "tags",
    "output_kind", "criteria_delta",
    "refs", "supersedes",
    "tdd_evidence", "doc_evidence", "design_evidence",
    "closure",
]
