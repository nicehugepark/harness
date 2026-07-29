# 주기 틱 등록 (T-c 트리거)

상주 데몬을 두지 않는다. OS 스케줄러가 **단명 틱을 주기적으로 기동**한다 —
"조용히 죽는 프로세스와 감시자의 감시자" 실패 모드를 들이지 않기 위해서다.

실측(2026-07-29): 이 환경에 `systemd --user` 와 `crontab` 이 둘 다 있다.

## systemd user timer

```
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/harness-tick.service <<'UNIT'
[Unit]
Description=harness scheduler tick (ephemeral)
[Service]
Type=oneshot
WorkingDirectory=%h/workspace
ExecStart=/usr/bin/python3 %h/workspace/harness/tools/tick.py --trigger periodic
UNIT
cat > ~/.config/systemd/user/harness-tick.timer <<'UNIT'
[Unit]
Description=harness scheduler tick every 15 min
[Timer]
OnBootSec=2min
OnUnitActiveSec=15min
[Install]
WantedBy=timers.target
UNIT
systemctl --user daemon-reload
systemctl --user enable --now harness-tick.timer
```

배치 잡도 같은 형태로 등록한다(`batchjob.py`, 제안 주기 5분).

## 켜기 전에 확인할 것

주기 틱을 켜는 순간 **사람이 없어도 큐가 전진한다.** 그것이 설계 의도지만
(사람 온라인은 전제가 아니다), 되돌리는 방법을 먼저 알고 켜야 한다:

```
systemctl --user disable --now harness-tick.timer   # 정지
touch <실행루트>/config/local/dispatch-kill          # 킬 스위치 — 신규 기동 전면 정지
```

킬 스위치는 파일 존재만으로 동작하고 조작 주체는 사람 전용이다.
