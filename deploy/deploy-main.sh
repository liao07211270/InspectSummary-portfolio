#!/usr/bin/env bash
# Push 到 main 觸發的部署:由 GitHub Actions workflow(self-hosted runner)呼叫。
# 同步 ~/InspectSummary-main 到遠端 main 最新狀態並重建容器(port 8082)。
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

# 走 deploy key 抓取(runner 環境沒有互動式認證)，key 路徑於部署環境自行設定
export GIT_SSH_COMMAND="ssh -i ${DEPLOY_KEY_PATH:-$HOME/.ssh/id_ed25519} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
git -c url."git@github.com:".insteadOf="https://github.com/" \
    fetch origin main --quiet

echo "deploying main: $(git rev-parse HEAD) -> $(git rev-parse origin/main)"
# reset --hard 不會動到未版控的 .env
git reset --hard origin/main

# 無論有無變更都重建,讓 Actions 的 Re-run job 也能強制重新部署
docker compose up -d --build

echo "deployed main @ $(git rev-parse HEAD)"
