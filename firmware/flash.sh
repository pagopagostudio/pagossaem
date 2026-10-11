#!/bin/zsh
# 사용법: ./firmware/flash.sh mic_speaker_test
# 빌드 → 보드에 올리기 → 시리얼 모니터 (나가기: Ctrl+])
set -e
. ~/esp/esp-idf/export.sh > /dev/null
cd "$(dirname "$0")/$1"
python "$IDF_PATH/tools/idf.py" build flash monitor
