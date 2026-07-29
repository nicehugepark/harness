#!/usr/bin/env python3
"""의존 없는 최소 테스트 러너.

근거: 이 머신에 pytest·pip 가 없다(실측 2026-07-29T09:23Z — `python3 -m pip` /
`python3 -m pytest` 둘 다 ModuleNotFoundError). 하네스는 실행 환경을 전제하지
않으므로(S§4.3) 테스트 실행이 외부 패키지에 종속되면 그 자체가 이식성 결함이다.

종료 코드: 0 = 전건 통과 · 1 = 실패 검출 · 2 = 실행 오류
(2 를 0 으로 읽지 않는다 — 오류와 검출 0건을 구별하지 못한 실행이 과거에
 검사 한 종을 통째로 무효화한 이력이 있다.)
"""
import importlib.util
import pathlib
import sys
import traceback

HERE = pathlib.Path(__file__).resolve().parent


def load(path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = mod
    spec.loader.exec_module(mod)
    return mod


def main(argv):
    targets = [pathlib.Path(a) for a in argv[1:]] or sorted(HERE.glob("test_*.py"))
    passed = failed = 0
    failures = []
    for path in targets:
        try:
            mod = load(path)
        except Exception:
            failed += 1
            failures.append((f"{path.name}::<import>", traceback.format_exc()))
            continue
        for name in sorted(n for n in dir(mod) if n.startswith("test_")):
            fn = getattr(mod, name)
            if not callable(fn):
                continue
            try:
                fn()
                passed += 1
            except BaseException:   # SystemExit 은 Exception 이 아니다 —
                                    # 잡지 않으면 테스트 1건이 러너를 죽인다
                failed += 1
                failures.append((f"{path.name}::{name}", traceback.format_exc()))

    for label, tb in failures:
        print(f"\n=== FAIL {label} ===")
        print(tb.rstrip())
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception:
        traceback.print_exc()
        sys.exit(2)
