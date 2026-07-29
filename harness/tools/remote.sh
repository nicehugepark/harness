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
    cd "$ROOT"
    # **코드만 보낸다. docs/ 는 절대 보내지 않는다.**
    #
    # 원격이 주 실행 머신이므로 그 머신의 docs/ 가 **살아 있는 원장**이다.
    # 로컬본으로 덮으면 진행 중인 요청의 state 와 lease 가 사라지고, 다음 틱이
    # 같은 요청을 다시 기동한다 — 실사고 2026-07-29T04:49: sync 직후 원장이
    # designing→queued 로 되돌아가 lease 를 잃었다.
    #
    # 추적 + 미추적을 함께 보내는 이유는 그대로다(미커밋 작업이 조용히 빠지면
    # 원격 결과가 과소 보고된다 — 실측 원격 118 대 로컬 168).
    git ls-files -co --exclude-standard -z \
      | tr "\0" "\n" | grep -v "^docs/" | tr "\n" "\0" \
      | tar --null -T - -czf /tmp/_hsync.tgz
    # 원격 루트를 통째로 지우지 않는다. config/·vault/·derived/·.claude/ 는
    # **그 머신의 로컬 상태**(머신 편성·이름 레지스트리·설치 저널·볼트)이고
    # 버전관리 밖이라 tar 에 실리지 않는다 — 지우면 복구 경로가 없다.
    # 실사고 2026-07-29T03:39: sync 가 매번 원격 config/ 를 파괴해 사람이 적용한
    # 동시성 상한 오버라이드가 조용히 사라졌고, 같은 값을 두 번 다시 적용했다.
    rsh "mkdir -p $RROOT"
    # 추적 트리만 정리한다(사라진 파일이 남지 않게). 로컬 상태 디렉토리는 제외.
    # docs/ 는 정리 대상에서도 뺀다 — 원격 원장은 로컬이 관여하지 않는다
    rsh "cd $RROOT && rm -rf harness README.md .githooks"
    cat /tmp/_hsync.tgz | rsh "tar xzf - -C $RROOT"
    rsh "cd $RROOT && mkdir -p config/local config/names config/machines \
         config/install/journal vault derived .claude fake-home/.claude"
    # 정제 목록은 저장소 밖이지만 **게이트가 없으면 커밋을 못 한다**.
    # 목록 부재를 '검출 0'으로 읽지 않는 것이 계약이므로, 목록을 배포하지 않으면
    # 그 머신은 영원히 커밋할 수 없다(실측 2026-07-29T04:52 — 원격 배치 커밋 차단).
    if [ -f "$ROOT/config/local/sanitize-scanlist.txt" ]; then
      cat "$ROOT/config/local/sanitize-scanlist.txt" \
        | rsh "cat > $RROOT/config/local/sanitize-scanlist.txt"
      echo "  정제 목록 배포"
    fi
    N=$(git ls-files -co --exclude-standard | grep -vc "^docs/")
    echo "동기 완료: $N 파일 (코드만 — docs/ 는 원격 원장이라 보내지 않는다)"
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
