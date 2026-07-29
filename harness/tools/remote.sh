#!/bin/sh
# 원격 작업 머신 동기·실행 — 부분 동기 금지.
#
# 근거: lib·tools·tests 만 보내고 installer 를 빠뜨렸더니 원격에서 3건이
# 실패했다(2026-07-29T03:20). 원인은 코드가 아니라 낡은 사본이었고, 그 결과는
# "원격에서만 실패한다"는 잘못된 결론이었다. 부분 동기를 손으로 조합하는 한
# 같은 일이 반복되므로, 동기 단위를 **추적 대상 전체**로 고정한다.
#
#   remote.sh sync            추적 대상 전체를 원격 작업 루트로 보낸다
#   remote.sh run <명령>       원격에서 1회 실행
#   remote.sh verify          동기 후 시험·IV1·자산 대조를 병렬 실행
set -e
ROOT=$(git rev-parse --show-toplevel)
VAULT="$ROOT/vault/machines/jade.json"
[ -f "$VAULT" ] || { echo "볼트에 접속 정보가 없다: $VAULT" >&2; exit 2; }
eval "$(python3 - "$VAULT" <<'PY'
import json,sys,pathlib
v=json.loads(pathlib.Path(sys.argv[1]).read_text())
print('RUSER=%s; RHOST=%s; RPORT=%s' % (v["user"], v["host"], v["port"]))
PY
)"
# 원격 실행 루트 — 로컬과 같은 이름을 쓴다. 선행 판의 ~/.harness·
# ~/projects 와 경로가 겹치지 않아 섞일 자리가 구조적으로 없다.
RROOT='$HOME/workspace'
rsh() { ssh -o BatchMode=yes -o ConnectTimeout=10 -p "$RPORT" "$RUSER@$RHOST" "$@"; }

case "$1" in
  sync)
    # **코드는 git 으로 간다.** tar 로 덮어쓰던 방식은 원격에 미커밋 수정을
    # 만들어 원격의 git merge 를 막았다(실측 2026-07-29T05:18 — "Please move or
    # remove them before you merge"). 원격이 배포 키를 갖게 됐으므로 정본
    # 경로(커밋 → push → pull)를 쓴다. 파일 복사는 무시 규칙을 우회한다.
    cd "$ROOT"
    if [ -n "$(git status --porcelain)" ]; then
      echo "  로컬에 미커밋 변경이 있다 — 커밋 후 다시 실행하라" >&2
      git status --short | head -5 >&2
      exit 1
    fi
    git push -q origin main
    rsh "cd $RROOT && git fetch -q origin main && git merge --no-edit -q origin/main" \
      && echo "  원격 pull 완료"
    # 정제 목록은 저장소 밖이지만 게이트가 필요로 하는 정책이라 별도로 배포한다
    if [ -f "$ROOT/config/local/sanitize-scanlist.txt" ]; then
      cat "$ROOT/config/local/sanitize-scanlist.txt" \
        | rsh "cat > $RROOT/config/local/sanitize-scanlist.txt"
    fi
    rsh "cd $RROOT && mkdir -p config/local config/names config/machines \
         config/install/journal vault derived .claude fake-home/.claude"
    echo "동기 완료: git 경로 (원격 HEAD $(rsh "cd $RROOT && git rev-parse --short HEAD"))"
    ;;
  run) shift; rsh "cd $RROOT && $*" ;;
  verify)
    "$0" sync
    rsh "cd $RROOT && nohup sh -c '
      { python3 harness/tests/run.py > /tmp/v-tests.log 2>&1; echo EXIT=\$? >> /tmp/v-tests.log; } &
      { python3 harness/tools/iv1.py  > /tmp/v-iv1.log   2>&1; echo EXIT=\$? >> /tmp/v-iv1.log; } &
      { python3 harness/tools/genassets.py --check > /tmp/v-gen.log 2>&1; echo EXIT=\$? >> /tmp/v-gen.log; } &
      wait' >/dev/null 2>&1 &"
    echo "원격 병렬 검증 기동 — remote.sh results 로 확인"
    ;;
  pull)
    # 원격 원장을 로컬로 당긴다. **원격에 GitHub 자격증명을 두지 않는** 방향이다 —
    # 주 실행 머신에 배포 자격증명을 두면 그 머신이 유출 표면이 되고, 그것을
    # 볼트로 관리해도 사용 시점에 프로세스 환경으로 새어 나간다.
    cd "$ROOT"
    git remote get-url jade >/dev/null 2>&1 \
      || git remote add jade "ssh://$RUSER@$RHOST:$RPORT/~/workspace"
    git fetch -q jade main
    echo "원격 HEAD: $(git rev-parse --short jade/main)"
    # 양쪽이 각자 커밋하는 것이 정상이다 — 원격은 원장을, 로컬은 코드를 쓴다.
    # 그래서 ff-only 는 성립하지 않고 병합이 정상 경로다.
    git merge --no-edit jade/main 2>&1 | tail -2
    ;;
  push-upstream)
    cd "$ROOT"; git push origin main 2>&1 | tail -2 ;;
  results)
    for f in /tmp/v-tests.log /tmp/v-iv1.log /tmp/v-gen.log; do
      echo "--- $f"; rsh "tail -2 $f 2>/dev/null || echo '(아직 없음)'"
    done
    # 로컬·원격 시험 수가 갈리면 그 자체가 동기 결함이다 — 조용히 넘기지 않는다
    L=$(python3 "$ROOT/harness/tests/run.py" 2>&1 | tail -1 | grep -oE '^[0-9]+')
    R=$(rsh "grep -oE '^[0-9]+ passed' /tmp/v-tests.log | grep -oE '^[0-9]+'")
    [ "$L" = "$R" ] && echo "시험 수 일치: $L" \
      || echo "** 시험 수 불일치 — 로컬 $L · 원격 $R (동기 결함) **" ;;
  *) echo "사용: remote.sh {sync|run <명령>|verify|results}" >&2; exit 2 ;;
esac
