#!/bin/bash
# 使い方: pans.sh <n> <kind> '<choice JSON>' → 次の問いまで待って表示（主人公の側。全記録は読まない）。答えを受け付けなかったときは数秒で戻る
# 写し方: sed "s|__DIR__|<対戦フォルダ>|" play/agents/pans_template.sh > <対戦フォルダ>/pans.sh
G=__DIR__  # 対戦フォルダ（起動する人が置き換える）
echo "{\"n\": $1, \"kind\": \"$2\", \"choice\": $3}" >> $G/d.jsonl
for i in $(seq 1 280); do head -1 $G/question.txt | grep -q "\"n\": $1," || break; head -1 $G/question.txt | grep -q "\"error\"" && [ $i -gt 2 ] && break; sleep 2; done
cat $G/question.txt
