#!/bin/bash
# clone.sh <name> <url> : shallow clone into isolated dir, record commit
set -e
D=/home/user/apps-test/$1; E=/home/user/ATTa/evidence/$1; mkdir -p $E
rm -rf $D; git clone --depth 1 -q "$2" $D
cd $D; git log -1 --format='commit=%H%ndate=%cI%nsubject=%s' | tee $E/commit.txt
